"""Bounded, read-only access to the cluster's public research snapshots."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException, Response

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, freshness, signed

SNAPSHOT_URL = "https://raw.githubusercontent.com/panxy1019/QTrade-observations/main/latest.json"
SCHEMA = "qtrade.public-observations.v1"
MAX_BYTES = 2_000_000
MAX_PUBLICATION_AGE = 1200
router = APIRouter()


def validate_snapshot(document: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Recompute health at request time; old green JSON must not stay green."""
    now = now or datetime.now(UTC)
    unsigned = {key: value for key, value in document.items() if key != "evidence_sha256"}
    if document.get("evidence_sha256") != signed(unsigned)["evidence_sha256"]:
        raise ValueError("PUBLICATION_CHECKSUM_MISMATCH")
    if document.get("schema_version") != SCHEMA or any(
        document.get(key) != value for key, value in AUTHORITY.items()
    ):
        raise ValueError("PUBLICATION_AUTHORITY_MISMATCH")
    published = datetime.fromisoformat(document["published_at"])
    if published.tzinfo is None or published > now:
        raise ValueError("INVALID_PUBLICATION_CLOCK")
    age = (now - published).total_seconds()
    heartbeat = datetime.fromisoformat(document["observer"]["heartbeat_at"])
    if heartbeat.tzinfo is None or not 0 <= (published - heartbeat).total_seconds() < 90:
        heartbeat_valid = False
    else:
        heartbeat_valid = document["observer"]["worker_alive"] is True
    calendar = Calendar(document["calendar"])
    streams: dict[str, Any] = {}
    if set(document["datasets"]) != {"daily", "minute"}:
        raise ValueError("REQUIRED_STREAM_MISSING")
    for kind, dataset in document["datasets"].items():
        report = dataset["report"]
        report_unsigned = {k: v for k, v in report.items() if k != "evidence_sha256"}
        if report.get("evidence_sha256") != signed(report_unsigned)["evidence_sha256"]:
            raise ValueError("REPORT_CHECKSUM_MISMATCH")
        if any(report.get(key) != value for key, value in AUTHORITY.items()):
            raise ValueError("REPORT_AUTHORITY_MISMATCH")
        if report.get("kind") != kind or report.get("required_symbol_count") != (
            30 if kind == "daily" else 75
        ):
            raise ValueError("REQUIRED_UNIVERSE_MISMATCH")
        latest = dataset["latest_bars"]
        symbols = [bar["symbol"] for bar in latest]
        if len(set(symbols)) != len(symbols) or set(symbols) != set(report["latest_by_symbol"]):
            raise ValueError("LATEST_BAR_COVERAGE_MISMATCH")
        for bar in latest:
            if bar["timestamp"] != report["latest_by_symbol"][bar["symbol"]]:
                raise ValueError("LATEST_BAR_CLOCK_MISMATCH")
            prices = [Decimal(bar[key]) for key in ("open", "high", "low", "close")]
            volume = Decimal(bar["volume"])
            if (
                any(not price.is_finite() or price <= 0 for price in prices)
                or not volume.is_finite()
                or volume < 0
                or prices[2] > min(prices[0], prices[3])
                or prices[1] < max(prices[0], prices[3])
            ):
                raise ValueError("INVALID_LATEST_BAR")
        row = freshness(report, calendar.expected(now, kind), now, kind)
        row["bar_count"] = report["bar_count"]
        row["last_attempt"] = document["observer"]["streams"][kind]["last_attempt"]
        if row["last_attempt"].get("outcome") == "FAILED":
            row["status"] = "COLLECTION_FAILED"
        streams[kind] = row
    experiments = document.get("experiments", [])
    if not isinstance(experiments, list) or len(experiments) > 200:
        raise ValueError("INVALID_EXPERIMENT_CATALOG")
    for experiment in experiments:
        if (
            experiment.get("persisted") is not True
            or experiment.get("promotion") != "NOT_ELIGIBLE"
            or experiment.get("external_order_transmission") is not False
        ):
            raise ValueError("EXPERIMENT_AUTHORITY_MISMATCH")
    healthy = (
        age <= MAX_PUBLICATION_AGE
        and heartbeat_valid
        and all(row["status"] == "FRESH" for row in streams.values())
    )
    return {
        **document,
        "checked_at": now.isoformat(),
        "healthy": healthy,
        "publication_status": "CURRENT" if age <= MAX_PUBLICATION_AGE else "STALE",
        "publication_age_seconds": age,
        "maximum_publication_age_seconds": MAX_PUBLICATION_AGE,
        "worker_alive_at_publication": heartbeat_valid,
        "streams": streams,
        "read_only": True,
    }


def fetch_snapshot() -> dict[str, Any]:
    # The destination is fixed in reviewed source, never supplied by a caller.
    request = Request(SNAPSHOT_URL, headers={"User-Agent": "QTrade-ReadOnly/1"})
    with urlopen(request, timeout=8) as response:
        raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("PUBLICATION_TOO_LARGE")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("PUBLICATION_MUST_BE_OBJECT")
    return validate_snapshot(value)


def _read() -> dict[str, Any]:
    try:
        return fetch_snapshot()
    except Exception as error:
        # Do not return upstream responses, paths or credentials to a public client.
        raise HTTPException(status_code=503, detail="research publication unavailable") from error


@router.get("/observations/latest")
def latest_observations(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return _read()


@router.get("/data-freshness")
def data_freshness(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    value = _read()
    return {
        key: value[key]
        for key in (
            "classification",
            "checked_at",
            "published_at",
            "healthy",
            "streams",
            "publication_status",
            "publication_age_seconds",
            "maximum_publication_age_seconds",
            "worker_alive_at_publication",
            "read_only",
            "external_order_transmission",
        )
    }


@router.get("/observations/health")
def observation_health(response: Response) -> dict[str, Any]:
    result = data_freshness(response)
    response.status_code = 200 if result["healthy"] else 503
    return result


@router.get("/persisted-experiments")
def persisted_experiments(response: Response) -> dict[str, Any]:
    value = _read()
    response.headers["Cache-Control"] = "no-store"
    return {
        "classification": "PERSISTED_RESEARCH_EXPERIMENT_CATALOG",
        "published_at": value["published_at"],
        "publication_status": value["publication_status"],
        "experiments": value["experiments"],
        "external_order_transmission": False,
    }
