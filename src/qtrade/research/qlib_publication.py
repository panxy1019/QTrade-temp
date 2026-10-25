"""Checksummed Qlib archives and bounded summaries; never executable trade authority."""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed

SCHEMA = "qtrade.qlib-baseline.v1"
RUN_SCHEMA = "qtrade.qlib-run.v1"


def verify_signed(value: dict[str, Any]) -> None:
    if signed({k: v for k, v in value.items() if k != "evidence_sha256"}) != value:
        raise ValueError("QLIB_EVIDENCE_CHECKSUM_MISMATCH")


def verify_archive(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "checksums.json").read_text(encoding="utf-8"))
    verify_signed(manifest)
    if not manifest["files"] or len(manifest["files"]) > 20_000:
        raise ValueError("INVALID_QLIB_ARCHIVE_FILES")
    for name, digest in manifest["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError("QLIB_ARTIFACT_OUTSIDE_ARCHIVE")
        if sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("QLIB_ARTIFACT_CHECKSUM_MISMATCH")
    summary: dict[str, Any] = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    verify_signed(summary)
    if summary["run_id"] != manifest["run_id"] or "summary.json" not in manifest["files"]:
        raise ValueError("QLIB_ARCHIVE_IDENTITY_MISMATCH")
    return summary


def validate_publication(value: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    verify_signed(value)
    if value.get("schema_version") != SCHEMA or any(
        value.get(k) != v for k, v in AUTHORITY.items()
    ):
        raise ValueError("QLIB_PUBLICATION_AUTHORITY_MISMATCH")
    now = now or datetime.now(UTC)
    published = datetime.fromisoformat(value["published_at"])
    if published.tzinfo is None or published > now:
        raise ValueError("INVALID_QLIB_PUBLICATION_CLOCK")
    state = value["state"]
    if state not in {
        "NOT_STARTED",
        "PREPARING_DATA",
        "BUILDING_PROVIDER",
        "COMPUTING_ALPHA158",
        "TRAINING_LIGHTGBM",
        "BACKTESTING",
        "COMPLETE",
        "FAILED",
    }:
        raise ValueError("INVALID_QLIB_PUBLICATION_STATE")
    summary = value.get("run")
    if state == "COMPLETE":
        if not isinstance(summary, dict):
            raise ValueError("QLIB_COMPLETE_WITHOUT_RUN")
        verify_signed(summary)
        if (
            summary.get("schema_version") != RUN_SCHEMA
            or any(summary.get(k) != v for k, v in AUTHORITY.items())
            or summary.get("persisted") is not True
            or summary.get("feature_count") != 158
            or summary.get("persistence_kind") != "FORMAL_IMMUTABLE_QLIB_RUN"
            or not 1 <= summary.get("symbols", 0) <= 1000
            or re.fullmatch(r"[a-f0-9]{64}", value.get("artifact_manifest_sha256", "")) is None
            or re.fullmatch(r"[a-f0-9]{40}", summary.get("source_revision", "")) is None
        ):
            raise ValueError("INVALID_QLIB_RUN_CONTRACT")
        for number in summary["metrics"].values():
            if number is not None and (isinstance(number, bool) or not math.isfinite(number)):
                raise ValueError("NON_FINITE_QLIB_METRIC")
        curve = summary["curve"]
        if not isinstance(curve, list) or not 2 <= len(curve) <= 5000:
            raise ValueError("INVALID_QLIB_CURVE_SIZE")
        last: str | None = None
        for row in curve:
            day = str(date.fromisoformat(row["date"]))
            if last and day <= last:
                raise ValueError("NON_MONOTONIC_QLIB_CURVE")
            if day > summary["data_end"] or day > str(now.date()):
                raise ValueError("FUTURE_QLIB_CURVE")
            for key in ("equity", "benchmark_equity", "drawdown", "turnover", "cost"):
                if isinstance(row[key], bool) or not math.isfinite(row[key]):
                    raise ValueError("INVALID_QLIB_CURVE_NUMBER")
            if row["equity"] <= 0 or row["benchmark_equity"] <= 0 or row["drawdown"] > 1e-8:
                raise ValueError("INVALID_QLIB_EQUITY")
            last = day
        evaluation = summary["portfolio_evaluation"]
        if (
            evaluation["start"] != curve[0]["date"]
            or evaluation["end"] != curve[-1]["date"]
            or evaluation["days"] != len(curve)
            or summary["segments"]["train"][1] >= summary["segments"]["valid"][0]
            or summary["segments"]["valid"][1] >= summary["segments"]["test"][0]
        ):
            raise ValueError("QLIB_EVALUATION_WINDOW_MISMATCH")
    elif summary is not None:
        raise ValueError("INCOMPLETE_STATE_WITH_QLIB_RESULT")
    expected = str(Calendar(value["calendar"]).expected(now, "daily").date())
    return {
        **value,
        "checked_at": now.isoformat(),
        "read_only": True,
        "publication_status": "CURRENT" if (now - published).total_seconds() <= 1200 else "STALE",
        "expected_complete_day": expected,
        "result_data_status": (
            "CURRENT_THROUGH_COMPLETE_DAY"
            if summary and summary["data_end"] >= expected
            else "HISTORICAL"
            if summary
            else "NOT_AVAILABLE"
        ),
    }


def build_publication(base: Path, calendar: dict[str, Any]) -> dict[str, Any]:
    root = base / "qlib"
    progress_file = base / "qlib-data-20261023/progress.json"
    progress = json.loads(progress_file.read_text()) if progress_file.exists() else {}
    status_file = root / "status.json"
    status = json.loads(status_file.read_text()) if status_file.exists() else {}
    state = status.get("state", "PREPARING_DATA" if progress else "NOT_STARTED")
    summary = None
    digest = None
    if state == "COMPLETE":
        run_id = status["run_id"]
        if re.fullmatch(r"[a-z0-9-]{1,120}", run_id) is None:
            raise ValueError("INVALID_QLIB_RUN_ID")
        archive = root / "runs" / run_id
        summary = verify_archive(archive)
        digest = sha256((archive / "checksums.json").read_bytes()).hexdigest()
    value = signed(
        {
            **AUTHORITY,
            "schema_version": SCHEMA,
            "published_at": datetime.now(UTC).isoformat(),
            "calendar": calendar,
            "state": state,
            "progress": {
                k: progress.get(k) for k in ("completed", "failed", "target", "updated_at")
            },
            "run": summary,
            "artifact_manifest_sha256": digest,
        }
    )
    validate_publication(value)
    return value
