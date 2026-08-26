"""Append signed BaoStock source-coverage snapshots to an audit chain."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from scripts.atomic_json import write_json_atomic

COVERAGE_CLASSIFICATION = "SOURCE_COVERAGE_DIAGNOSTIC_NOT_FORWARD_EVIDENCE"
HISTORY_CLASSIFICATION = "SIGNED_SOURCE_COVERAGE_HISTORY_NOT_FORWARD_EVIDENCE"
_MAX_CHECKPOINTS = 64
_COVERAGE_KEYS = {
    "schema_version",
    "report_id",
    "classification",
    "generated_at",
    "provider",
    "provider_version",
    "frequency",
    "adjustflag",
    "anchor_day",
    "target_day",
    "preregistration_id",
    "preregistration_sha256",
    "warmup_data_version",
    "warmup_checksum",
    "warmup_evidence_sha256",
    "required_symbol_count",
    "required_bars_per_symbol_session",
    "target_complete_symbol_count",
    "ready_symbol_count",
    "incomplete_symbol_count",
    "incomplete_symbols",
    "target_incomplete_symbols",
    "fetch_error_symbols",
    "coverage",
    "status",
    "ready_for_formal_collection",
    "enters_forward_metrics",
    "promotion",
    "realtime_readiness",
    "real_data_paper_readiness",
    "external_order_transmission",
    "evidence_sha256",
}
_HISTORY_KEYS = {
    "schema_version",
    "report_id",
    "classification",
    "source_identity",
    "checkpoint_count",
    "all_checkpoints_monotonic",
    "checkpoints",
    "enters_forward_metrics",
    "promotion",
    "realtime_readiness",
    "real_data_paper_readiness",
    "external_order_transmission",
    "evidence_sha256",
}
_CHECKPOINT_KEYS = {
    "sequence",
    "generated_at",
    "report_evidence_sha256",
    "ready_symbol_count",
    "target_complete_symbol_count",
    "incomplete_symbol_count",
    "fetch_error_symbol_count",
    "status",
    "ready_symbol_count_delta",
    "monotonic_vs_previous",
    "previous_checkpoint_sha256",
    "checkpoint_sha256",
}


def _canonical_sha256(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _verified_coverage(report: object) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise ValueError("coverage report must be an object")
    digest = report.get("evidence_sha256")
    unsigned = {key: value for key, value in report.items() if key != "evidence_sha256"}
    generated_at = datetime.fromisoformat(str(report.get("generated_at")))
    counts = (
        report.get("required_symbol_count"),
        report.get("required_bars_per_symbol_session"),
        report.get("target_complete_symbol_count"),
        report.get("ready_symbol_count"),
        report.get("incomplete_symbol_count"),
    )
    incomplete = report.get("incomplete_symbols")
    target_incomplete = report.get("target_incomplete_symbols")
    fetch_errors = report.get("fetch_error_symbols")
    coverage = report.get("coverage")
    ready = counts[3] == 75
    if (
        set(report) != _COVERAGE_KEYS
        or not _is_sha256(digest)
        or digest != _canonical_sha256(unsigned)
        or report.get("schema_version") != 1
        or report.get("report_id")
        != "baostock-forward-source-coverage-diagnostic-v1"
        or report.get("classification") != COVERAGE_CLASSIFICATION
        or generated_at.tzinfo is None
        or generated_at > datetime.now(UTC) + timedelta(minutes=5)
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in counts
        )
        or counts[0] != 75
        or counts[1] != 48
        or cast(int, counts[2]) > 75
        or cast(int, counts[3]) > cast(int, counts[2])
        or cast(int, counts[3]) + cast(int, counts[4]) != 75
        or not isinstance(incomplete, list)
        or len(incomplete) != counts[4]
        or not isinstance(target_incomplete, list)
        or len(target_incomplete) != 75 - cast(int, counts[2])
        or not isinstance(fetch_errors, list)
        or not isinstance(coverage, list)
        or len(coverage) != 75
        or report.get("status")
        != ("READY_FOR_FORMAL_COLLECTION" if ready else "SOURCE_INCOMPLETE")
        or report.get("ready_for_formal_collection") is not ready
        or report.get("enters_forward_metrics") is not False
        or report.get("promotion") != "NOT_ELIGIBLE"
        or report.get("realtime_readiness") is not False
        or report.get("real_data_paper_readiness") is not False
        or report.get("external_order_transmission") is not False
    ):
        raise ValueError("coverage report contract is invalid")
    return cast(dict[str, Any], report)


def _identity(report: dict[str, Any]) -> dict[str, object]:
    return {
        "provider": report["provider"],
        "provider_version": report["provider_version"],
        "frequency": report["frequency"],
        "adjustflag": report["adjustflag"],
        "anchor_day": report["anchor_day"],
        "target_day": report["target_day"],
        "preregistration_id": report["preregistration_id"],
        "preregistration_sha256": report["preregistration_sha256"],
        "warmup_data_version": report["warmup_data_version"],
        "warmup_checksum": report["warmup_checksum"],
        "warmup_evidence_sha256": report["warmup_evidence_sha256"],
        "required_symbol_count": report["required_symbol_count"],
        "required_bars_per_symbol_session": report[
            "required_bars_per_symbol_session"
        ],
    }


def _verified_history(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("coverage history must be an object")
    digest = value.get("evidence_sha256")
    unsigned = {key: item for key, item in value.items() if key != "evidence_sha256"}
    checkpoints = value.get("checkpoints")
    identity = value.get("source_identity")
    if (
        set(value) != _HISTORY_KEYS
        or not _is_sha256(digest)
        or digest != _canonical_sha256(unsigned)
        or value.get("schema_version") != 1
        or value.get("report_id") != "baostock-forward-source-coverage-history-v1"
        or value.get("classification") != HISTORY_CLASSIFICATION
        or not isinstance(identity, dict)
        or not isinstance(checkpoints, list)
        or not checkpoints
        or len(checkpoints) > _MAX_CHECKPOINTS
        or value.get("checkpoint_count") != len(checkpoints)
        or value.get("enters_forward_metrics") is not False
        or value.get("promotion") != "NOT_ELIGIBLE"
        or value.get("realtime_readiness") is not False
        or value.get("real_data_paper_readiness") is not False
        or value.get("external_order_transmission") is not False
    ):
        raise ValueError("coverage history contract is invalid")
    previous_hash: str | None = None
    previous_time: datetime | None = None
    previous_ready: int | None = None
    previous_target: int | None = None
    previous_incomplete: int | None = None
    all_monotonic = True
    required_symbols = identity.get("required_symbol_count")
    if (
        not isinstance(required_symbols, int)
        or isinstance(required_symbols, bool)
        or required_symbols != 75
    ):
        raise ValueError("coverage history identity is invalid")
    for sequence, checkpoint in enumerate(checkpoints, start=1):
        if not isinstance(checkpoint, dict) or set(checkpoint) != _CHECKPOINT_KEYS:
            raise ValueError("coverage history checkpoint is invalid")
        checkpoint_digest = checkpoint.get("checkpoint_sha256")
        checkpoint_unsigned = {
            key: item
            for key, item in checkpoint.items()
            if key != "checkpoint_sha256"
        }
        captured_at = datetime.fromisoformat(str(checkpoint.get("generated_at")))
        ready = checkpoint.get("ready_symbol_count")
        target_complete = checkpoint.get("target_complete_symbol_count")
        incomplete = checkpoint.get("incomplete_symbol_count")
        fetch_errors = checkpoint.get("fetch_error_symbol_count")
        delta = checkpoint.get("ready_symbol_count_delta")
        if (
            captured_at.tzinfo is None
            or captured_at > datetime.now(UTC) + timedelta(minutes=5)
            or checkpoint.get("sequence") != sequence
            or checkpoint.get("previous_checkpoint_sha256") != previous_hash
            or not _is_sha256(checkpoint_digest)
            or checkpoint_digest != _canonical_sha256(checkpoint_unsigned)
            or not _is_sha256(checkpoint.get("report_evidence_sha256"))
            or previous_time is not None
            and captured_at <= previous_time
            or any(
                not isinstance(count, int)
                or isinstance(count, bool)
                or not 0 <= count <= required_symbols
                for count in (ready, target_complete, incomplete, fetch_errors)
            )
            or cast(int, ready) > cast(int, target_complete)
            or cast(int, ready) + cast(int, incomplete) != required_symbols
            or checkpoint.get("status")
            != (
                "READY_FOR_FORMAL_COLLECTION"
                if ready == required_symbols
                else "SOURCE_INCOMPLETE"
            )
            or sequence == 1
            and delta is not None
            or sequence > 1
            and (
                not isinstance(delta, int)
                or isinstance(delta, bool)
                or delta != cast(int, ready) - cast(int, previous_ready)
            )
        ):
            raise ValueError("coverage history checkpoint chain is invalid")
        monotonic = previous_ready is None or (
            cast(int, ready) >= previous_ready
            and cast(int, target_complete) >= cast(int, previous_target)
            and cast(int, incomplete) <= cast(int, previous_incomplete)
        )
        if checkpoint.get("monotonic_vs_previous") is not monotonic:
            raise ValueError("coverage history checkpoint monotonicity is invalid")
        previous_hash = str(checkpoint_digest)
        previous_time = captured_at
        previous_ready = cast(int, ready)
        previous_target = cast(int, target_complete)
        previous_incomplete = cast(int, incomplete)
        all_monotonic = all_monotonic and monotonic
    if value.get("all_checkpoints_monotonic") is not all_monotonic:
        raise ValueError("coverage history monotonic status is invalid")
    return cast(dict[str, Any], value)


def append_coverage_history(
    report: object,
    existing: object | None = None,
) -> tuple[dict[str, Any], str]:
    coverage = _verified_coverage(report)
    history = _verified_history(existing) if existing is not None else None
    identity = _identity(coverage)
    checkpoints = list(history["checkpoints"]) if history else []
    if history is not None and history["source_identity"] != identity:
        raise ValueError("coverage history source identity changed")
    if checkpoints and checkpoints[-1]["report_evidence_sha256"] == coverage[
        "evidence_sha256"
    ]:
        assert history is not None
        return history, "UNCHANGED"
    if len(checkpoints) >= _MAX_CHECKPOINTS:
        raise ValueError("coverage history reached its checkpoint limit")
    generated_at = datetime.fromisoformat(str(coverage["generated_at"]))
    previous = checkpoints[-1] if checkpoints else None
    if previous is not None and generated_at <= datetime.fromisoformat(
        str(previous["generated_at"])
    ):
        raise ValueError("coverage history time must strictly advance")
    ready = int(coverage["ready_symbol_count"])
    target_complete = int(coverage["target_complete_symbol_count"])
    incomplete = int(coverage["incomplete_symbol_count"])
    ready_delta = ready - int(previous["ready_symbol_count"]) if previous else None
    monotonic = previous is None or (
        ready >= int(previous["ready_symbol_count"])
        and target_complete >= int(previous["target_complete_symbol_count"])
        and incomplete <= int(previous["incomplete_symbol_count"])
    )
    checkpoint_unsigned: dict[str, object] = {
        "sequence": len(checkpoints) + 1,
        "generated_at": coverage["generated_at"],
        "report_evidence_sha256": coverage["evidence_sha256"],
        "ready_symbol_count": ready,
        "target_complete_symbol_count": target_complete,
        "incomplete_symbol_count": incomplete,
        "fetch_error_symbol_count": len(coverage["fetch_error_symbols"]),
        "status": coverage["status"],
        "ready_symbol_count_delta": ready_delta,
        "monotonic_vs_previous": monotonic,
        "previous_checkpoint_sha256": (
            previous["checkpoint_sha256"] if previous else None
        ),
    }
    checkpoint = {
        **checkpoint_unsigned,
        "checkpoint_sha256": _canonical_sha256(checkpoint_unsigned),
    }
    checkpoints.append(checkpoint)
    unsigned: dict[str, object] = {
        "schema_version": 1,
        "report_id": "baostock-forward-source-coverage-history-v1",
        "classification": HISTORY_CLASSIFICATION,
        "source_identity": identity,
        "checkpoint_count": len(checkpoints),
        "all_checkpoints_monotonic": all(
            row["monotonic_vs_previous"] is True for row in checkpoints
        ),
        "checkpoints": checkpoints,
        "enters_forward_metrics": False,
        "promotion": "NOT_ELIGIBLE",
        "realtime_readiness": False,
        "real_data_paper_readiness": False,
        "external_order_transmission": False,
    }
    return (
        {**unsigned, "evidence_sha256": _canonical_sha256(unsigned)},
        "APPENDED",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, action="append", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("deployment/evidence/forward-source-coverage/history.json"),
    )
    args = parser.parse_args()
    history: object | None = None
    if args.output.is_file():
        history = json.loads(args.output.read_text(encoding="utf-8"))
    status = "UNCHANGED"
    for path in args.report:
        report = json.loads(path.read_text(encoding="utf-8"))
        history, status = append_coverage_history(report, history)
    assert isinstance(history, dict)
    if status != "UNCHANGED" or not args.output.is_file():
        write_json_atomic(args.output, history)
    print(
        json.dumps(
            {
                "status": status,
                "checkpoint_count": history["checkpoint_count"],
                "all_checkpoints_monotonic": history["all_checkpoints_monotonic"],
                "evidence_sha256": history["evidence_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
