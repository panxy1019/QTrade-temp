"""Append verified intraday source snapshots to a signed continuity chain."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scripts.atomic_json import write_json_atomic

SHANGHAI = ZoneInfo("Asia/Shanghai")
DETAIL_CLASSIFICATION = (
    "FREE_HISTORICAL_SOURCE_INTRADAY_OBSERVATION_NOT_REALTIME_ELIGIBLE"
)
BREADTH_CLASSIFICATION = (
    "FREE_HISTORICAL_SOURCE_INTRADAY_BREADTH_NOT_REALTIME_ELIGIBLE"
)
CONTINUITY_CLASSIFICATION = (
    "SIGNED_INTRADAY_SOURCE_CONTINUITY_NOT_REALTIME_ELIGIBLE"
)


def _canonical_sha256(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _load_signed(path: Path, expected_classification: str) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError(f"{path} must contain an object")
    digest = report.get("evidence_sha256")
    unsigned = {key: value for key, value in report.items() if key != "evidence_sha256"}
    if digest != _canonical_sha256(unsigned):
        raise ValueError(f"{path} integrity check failed")
    if report.get("classification") != expected_classification:
        raise ValueError(f"{path} classification is invalid")
    if (
        report.get("realtime_readiness") is not False
        or report.get("real_data_paper_readiness") is not False
        or report.get("promotion") != "NOT_ELIGIBLE"
        or report.get("external_order_transmission") is not False
    ):
        raise ValueError(f"{path} safety boundary is invalid")
    return report


def _non_regressing(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    if current["same_day_symbol_count"] < previous["same_day_symbol_count"]:
        return False
    if (
        current["synchronized_finalized_timestamp_count"]
        < previous["synchronized_finalized_timestamp_count"]
    ):
        return False
    previous_latest = previous["latest_synchronized_finalized_timestamp"]
    current_latest = current["latest_synchronized_finalized_timestamp"]
    if previous_latest is None:
        return True
    if current_latest is None:
        return False
    return datetime.fromisoformat(str(current_latest)) >= datetime.fromisoformat(
        str(previous_latest)
    )


def _checkpoint_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_sha256": report["evidence_sha256"],
        "completed_at": report["completed_at"],
        "session_phase": report["session_phase"],
        "symbol_count": report["symbol_count"],
        "sector_count": report["sector_count"],
        "same_day_symbol_count": report["same_day_symbol_count"],
        "synchronized_finalized_timestamp_count": report[
            "synchronized_finalized_timestamp_count"
        ],
        "latest_synchronized_finalized_timestamp": report[
            "latest_synchronized_finalized_timestamp"
        ],
        "source_request_window_seconds": report["source_request_window_seconds"],
        "maximum_same_day_latest_age_seconds": report[
            "maximum_same_day_latest_age_seconds"
        ],
        "all_symbols_same_day_coverage": report["all_symbols_same_day_coverage"],
    }


def _load_existing(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    report = _load_signed(path, CONTINUITY_CLASSIFICATION)
    checkpoints = report.get("checkpoints")
    if not isinstance(checkpoints, list) or len(checkpoints) > 32:
        raise ValueError("continuity checkpoint inventory is invalid")
    previous_hash: str | None = None
    for index, checkpoint in enumerate(checkpoints, start=1):
        if not isinstance(checkpoint, dict):
            raise ValueError("continuity checkpoint is malformed")
        digest = checkpoint.get("checkpoint_sha256")
        unsigned = {
            key: value
            for key, value in checkpoint.items()
            if key != "checkpoint_sha256"
        }
        if (
            checkpoint.get("sequence") != index
            or checkpoint.get("previous_checkpoint_sha256") != previous_hash
            or digest != _canonical_sha256(unsigned)
        ):
            raise ValueError("continuity checkpoint chain is invalid")
        previous_hash = str(digest)
    if report.get("checkpoint_count") != len(checkpoints):
        raise ValueError("continuity checkpoint count is invalid")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--detail",
        type=Path,
        default=Path("deployment/evidence/intraday-observation/report.json"),
    )
    parser.add_argument(
        "--breadth",
        type=Path,
        default=Path("deployment/evidence/intraday-breadth/report.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    detail = _load_signed(args.detail, DETAIL_CLASSIFICATION)
    breadth = _load_signed(args.breadth, BREADTH_CLASSIFICATION)
    detail_completed = datetime.fromisoformat(str(detail["completed_at"]))
    breadth_completed = datetime.fromisoformat(str(breadth["completed_at"]))
    if detail_completed.tzinfo is None or breadth_completed.tzinfo is None:
        raise ValueError("intraday completion time must be timezone-aware")
    detail_day = detail_completed.astimezone(SHANGHAI).date()
    breadth_day = breadth_completed.astimezone(SHANGHAI).date()
    if detail_day != breadth_day:
        raise ValueError("detail and breadth observations must share a trading day")
    existing = _load_existing(args.output)
    checkpoints = list(existing.get("checkpoints", [])) if existing else []
    if existing and existing.get("trading_day") != detail_day.isoformat():
        raise ValueError("continuity output belongs to a different trading day")
    if len(checkpoints) >= 32:
        raise ValueError("continuity output reached the 32-checkpoint limit")
    if checkpoints:
        latest = checkpoints[-1]
        if (
            latest["detail"]["evidence_sha256"] == detail["evidence_sha256"]
            and latest["breadth"]["evidence_sha256"] == breadth["evidence_sha256"]
        ):
            print(json.dumps({"status": "UNCHANGED", "checkpoint_count": len(checkpoints)}))
            return 0
    detail_summary = _checkpoint_summary(detail)
    breadth_summary = _checkpoint_summary(breadth)
    previous = checkpoints[-1] if checkpoints else None
    monotonic = previous is None or (
        _non_regressing(previous["detail"], detail_summary)
        and _non_regressing(previous["breadth"], breadth_summary)
    )
    captured_at = max(detail_completed, breadth_completed)
    if previous is not None and captured_at <= datetime.fromisoformat(
        str(previous["captured_at"])
    ):
        raise ValueError("continuity checkpoint time must advance")
    checkpoint_unsigned: dict[str, Any] = {
        "sequence": len(checkpoints) + 1,
        "captured_at": captured_at.isoformat(),
        "trading_day": detail_day.isoformat(),
        "detail": detail_summary,
        "breadth": breadth_summary,
        "monotonic_vs_previous": monotonic,
        "previous_checkpoint_sha256": (
            checkpoints[-1]["checkpoint_sha256"] if checkpoints else None
        ),
    }
    checkpoint = {
        **checkpoint_unsigned,
        "checkpoint_sha256": _canonical_sha256(checkpoint_unsigned),
    }
    checkpoints.append(checkpoint)
    unsigned: dict[str, Any] = {
        "schema_version": 1,
        "classification": CONTINUITY_CLASSIFICATION,
        "trading_day": detail_day.isoformat(),
        "checkpoint_count": len(checkpoints),
        "all_checkpoints_monotonic": all(
            row["monotonic_vs_previous"] for row in checkpoints
        ),
        "checkpoints": checkpoints,
        "realtime_readiness": False,
        "real_data_paper_readiness": False,
        "promotion": "NOT_ELIGIBLE",
        "external_order_transmission": False,
    }
    report = {**unsigned, "evidence_sha256": _canonical_sha256(unsigned)}
    write_json_atomic(args.output, report)
    print(
        json.dumps(
            {
                "status": "APPENDED",
                "checkpoint_count": report["checkpoint_count"],
                "captured_at": checkpoint["captured_at"],
                "monotonic_vs_previous": checkpoint["monotonic_vs_previous"],
                "checkpoint_sha256": checkpoint["checkpoint_sha256"],
                "evidence_sha256": report["evidence_sha256"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
