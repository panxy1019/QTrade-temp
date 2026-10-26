"""Bounded read-only queries over the published observed catalog; no cluster writer."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response

from qtrade.api.current_datasets import download
from qtrade.instruments.observed import SCHEMA, instrument_id
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed

router = APIRouter(prefix="/unified")


def validate_publication(value: dict[str, Any]) -> dict[str, Any]:
    master = value["master"]
    for document in (value, master):
        if (signed({k: v for k, v in document.items() if k != "evidence_sha256"}) != document
                or any(document.get(k) != v for k, v in AUTHORITY.items())):
            raise ValueError("UNIFIED_CATALOG_HASH_OR_AUTHORITY_MISMATCH")
    now = datetime.now(UTC)
    clock = datetime.fromisoformat(value["built_at"])
    rows = master["securities"]
    if (value.get("schema_version") != "qtrade.unified-catalog.v1"
            or master.get("schema_version") != SCHEMA or clock.tzinfo is None or clock > now
            or not 1 <= len(rows) <= 10_000 or master["master_count"] != len(rows)
            or len({r["instrument_id"] for r in rows}) != len(rows)
            or re.fullmatch(r"[a-f0-9]{64}", master["universe_version"]) is None
            or master["historical_pit_certified_count"] != 0):
        raise ValueError("UNIFIED_CATALOG_SCHEMA_MISMATCH")
    target = [r for r in rows if r["in_target"]]
    if (len(target) != master["target_actual"]
            or dict(Counter(r["security_type"] for r in target)) != master["target_by_type"]):
        raise ValueError("UNIFIED_CATALOG_COUNTS_MISMATCH")
    for row in rows:
        if (row["instrument_id"] != instrument_id(row["symbol"], row["security_type"])
                or row["exchange"] != row["instrument_id"].split(".")[0]
                or row["universe_version"] != master["universe_version"]
                or row["available_at"] is not None
                or set(row["frequencies"]) != {"day", "5min"}
                or row["in_target"] and (row["status"] != "ACTIVE_OBSERVED" or row["issues"])):
            raise ValueError("UNIFIED_SECURITY_IDENTITY_MISMATCH")
    for freq in ("day", "5min"):
        if dict(Counter(r["frequencies"][freq]["state"] for r in target)) != (
                master["frequency_counts"][freq]):
            raise ValueError("UNIFIED_FREQUENCY_COUNTS_MISMATCH")
    if "incremental_counts" in master:
        for freq in ("day", "5min"):
            values = [r["incremental"][freq] for r in target]
            if dict(Counter(r["state"] for r in values)) != master["incremental_counts"][freq]:
                raise ValueError("UNIFIED_INCREMENTAL_COUNTS_MISMATCH")
            for row in values:
                if (row["state"] not in {"PLANNED", "CONTENT_COVERAGE_VERIFIED", "LIMITED_RESEARCH",
                                         "QUARANTINED", "FAILED_RETAINING_LAST_SUCCESS"}
                        or row["independent_source_reconciled"] is not False
                        or row["historical_pit_certified"] is not False
                        or type(row["bar_count"]) is not int or row["bar_count"] < 0):
                    raise ValueError("INVALID_UNIFIED_INCREMENTAL_STATE")
                for key in ("expected_end", "observed_end", "observed_start", "last_success_at"):
                    if row.get(key) is not None:
                        at = datetime.fromisoformat(row[key])
                        if at.tzinfo is None or at > now:
                            raise ValueError("INVALID_UNIFIED_INCREMENTAL_CLOCK")
                if row["observed_end"] is not None and (
                        row["expected_end"] is None or datetime.fromisoformat(
                            row["observed_end"]) > datetime.fromisoformat(row["expected_end"])):
                    raise ValueError("UNIFIED_INCREMENTAL_FUTURE_END")
    return value


def collection_freshness(value: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    master = value["master"]
    if "incremental_counts" not in master:
        return {}
    try:
        calendar = Calendar(master["collection"]["calendar"])
        ends = {freq: calendar.expected(now, "daily" if freq == "day" else "minute")
                for freq in ("day", "5min")}
    except (ValueError, KeyError):
        return {freq: {"status": "CALENDAR_UNVERIFIED", "ready_symbols": 0,
                       "required_symbols": master["target_actual"], "expected_end": None}
                for freq in ("day", "5min")}
    result = {}
    age = (now - datetime.fromisoformat(value["built_at"])).total_seconds()
    fresh_publication = 0 <= age <= 1200
    for freq, expected in ends.items():
        ready = sum(r["incremental"][freq]["observed_end"] is not None and
                    datetime.fromisoformat(r["incremental"][freq]["observed_end"]) >= expected
                    for r in master["securities"] if r["in_target"])
        result[freq] = {"status": "STALE_PUBLICATION" if not fresh_publication else
                        "FRESH" if ready == master["target_actual"] else "PARTIAL",
                        "ready_symbols": ready, "required_symbols": master["target_actual"],
                        "expected_end": expected.isoformat()}
    return result


@lru_cache(maxsize=1)
def _load(minute: int) -> dict[str, Any]:
    return validate_publication(json.loads(download("unified.json", 12_000_000)))


def catalog() -> dict[str, Any]:
    try:
        return _load(int(datetime.now(UTC).timestamp()) // 60)
    except Exception as error:
        raise HTTPException(503, "unified observed catalog unavailable") from error


@router.get("/summary")
def summary(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    value = catalog()
    return {**AUTHORITY, "schema_version": value["schema_version"],
            "built_at": value["built_at"], "checked_at": datetime.now(UTC).isoformat(),
            "snapshot_age_seconds": (datetime.now(UTC) - datetime.fromisoformat(
                value["built_at"])).total_seconds(),
            "evidence_sha256": value["evidence_sha256"], "read_only": True,
            "incremental_freshness": collection_freshness(value),
            "master": {k: v for k, v in value["master"].items() if k != "securities"}}


@router.get("/securities")
def securities(
    response: Response, query: str = Query("", max_length=80),
    frequency: Literal["day", "5min"] = "day", quality: str = Query("", max_length=50),
    target_only: bool = True, offset: int = Query(0, ge=0, le=10_000),
    limit: int = Query(25, ge=1, le=100),
    coverage: Literal["historical", "incremental"] = "historical",
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    value = catalog()
    needle = query.strip().lower()
    def state(row: dict[str, Any]) -> str:
        return str(row["frequencies"][frequency]["state"] if coverage == "historical"
                   else row.get("incremental", {}).get(frequency, {}).get("state", "NOT_PLANNED"))

    rows = [r for r in value["master"]["securities"]
            if (not target_only or r["in_target"])
            and (not quality or state(r) == quality)
            and (not needle or needle in (r["instrument_id"] + r["name"]).lower())]
    return {**AUTHORITY, "universe_version": value["master"]["universe_version"],
            "catalog_sha256": value["evidence_sha256"], "built_at": value["built_at"],
            "frequency": frequency, "total": len(rows), "offset": offset, "limit": limit,
            "rows": rows[offset:offset + limit], "read_only": True}
