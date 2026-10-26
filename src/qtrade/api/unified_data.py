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
from qtrade.monitoring.data_freshness import AUTHORITY, signed

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
    return value


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
            "master": {k: v for k, v in value["master"].items() if k != "securities"}}


@router.get("/securities")
def securities(
    response: Response, query: str = Query("", max_length=80),
    frequency: Literal["day", "5min"] = "day", quality: str = Query("", max_length=50),
    target_only: bool = True, offset: int = Query(0, ge=0, le=10_000),
    limit: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    value = catalog()
    needle = query.strip().lower()
    rows = [r for r in value["master"]["securities"]
            if (not target_only or r["in_target"])
            and (not quality or r["frequencies"][frequency]["state"] == quality)
            and (not needle or needle in (r["instrument_id"] + r["name"]).lower())]
    return {**AUTHORITY, "universe_version": value["master"]["universe_version"],
            "catalog_sha256": value["evidence_sha256"], "built_at": value["built_at"],
            "frequency": frequency, "total": len(rows), "offset": offset, "limit": limit,
            "rows": rows[offset:offset + limit], "read_only": True}
