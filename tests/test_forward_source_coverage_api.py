import json
from copy import deepcopy
from pathlib import Path

from qtrade.api.app import (
    _canonical_dict_sha256,
    _load_verified_forward_source_coverage,
    _load_verified_forward_source_coverage_history,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = (
    PROJECT_ROOT / "deployment/evidence/forward-source-coverage/report.json"
)
HISTORY_PATH = (
    PROJECT_ROOT / "deployment/evidence/forward-source-coverage/history.json"
)


def _inputs() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    dataset = {
        "data_version": report["warmup_data_version"],
        "checksum": report["warmup_checksum"],
        "evidence_sha256": report["warmup_evidence_sha256"],
        "eligible_symbols": [row["symbol"] for row in report["coverage"]],
    }
    preregistration = {
        "preregistration_id": report["preregistration_id"],
        "preregistration_sha256": report["preregistration_sha256"],
        "first_eligible_trading_day": "2026-08-26",
        "warmup_dataset": {
            "data_version": report["warmup_data_version"],
            "checksum": report["warmup_checksum"],
            "last_trading_day": "2026-08-25",
        },
    }
    return report, dataset, preregistration


def _write_signed(path: Path, report: dict[str, object]) -> None:
    unsigned = {key: value for key, value in report.items() if key != "evidence_sha256"}
    report["evidence_sha256"] = _canonical_dict_sha256(unsigned)
    path.write_text(json.dumps(report), encoding="utf-8")


def test_forward_source_coverage_accepts_packaged_diagnostic() -> None:
    report, dataset, preregistration = _inputs()

    verified = _load_verified_forward_source_coverage(
        REPORT_PATH,
        dataset,
        preregistration,
    )

    assert verified == report
    assert verified["ready_symbol_count"] == 52
    assert verified["incomplete_symbol_count"] == 23
    assert verified["enters_forward_metrics"] is False
    assert verified["external_order_transmission"] is False


def test_forward_source_coverage_accepts_packaged_history() -> None:
    coverage, _, _ = _inputs()

    verified = _load_verified_forward_source_coverage_history(
        HISTORY_PATH,
        coverage,
    )

    assert verified is not None
    assert verified["checkpoint_count"] == 3
    assert verified["all_checkpoints_monotonic"] is True
    assert verified["checkpoints"][-1]["ready_symbol_count"] == 52
    assert verified["checkpoints"][-1]["ready_symbol_count_delta"] == 5
    assert verified["enters_forward_metrics"] is False


def test_forward_source_coverage_rejects_resigned_history_chain_tamper(
    tmp_path: Path,
) -> None:
    coverage, _, _ = _inputs()
    history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    history["checkpoints"][1]["ready_symbol_count"] = 74
    target = tmp_path / "history.json"
    _write_signed(target, history)

    assert _load_verified_forward_source_coverage_history(target, coverage) is None


def test_forward_source_coverage_rejects_signed_inconsistent_arithmetic(
    tmp_path: Path,
) -> None:
    report, dataset, preregistration = _inputs()
    tampered = deepcopy(report)
    tampered["ready_symbol_count"] = 42
    target = tmp_path / "report.json"
    _write_signed(target, tampered)

    assert (
        _load_verified_forward_source_coverage(target, dataset, preregistration)
        is None
    )


def test_forward_source_coverage_rejects_signed_inconsistent_symbol_reason(
    tmp_path: Path,
) -> None:
    report, dataset, preregistration = _inputs()
    tampered = deepcopy(report)
    coverage = tampered["coverage"]
    assert isinstance(coverage, list)
    row = next(value for value in coverage if value.get("ready") is False)
    assert isinstance(row, dict)
    row["reasons"] = []
    target = tmp_path / "report.json"
    _write_signed(target, tampered)

    assert (
        _load_verified_forward_source_coverage(target, dataset, preregistration)
        is None
    )


def test_forward_source_coverage_rejects_signed_pre_close_diagnostic(
    tmp_path: Path,
) -> None:
    report, dataset, preregistration = _inputs()
    tampered = deepcopy(report)
    tampered["generated_at"] = "2026-08-26T15:09:59+08:00"
    target = tmp_path / "report.json"
    _write_signed(target, tampered)

    assert (
        _load_verified_forward_source_coverage(target, dataset, preregistration)
        is None
    )


def test_forward_source_coverage_rejects_signed_unknown_public_field(
    tmp_path: Path,
) -> None:
    report, dataset, preregistration = _inputs()
    tampered = deepcopy(report)
    tampered["unexpected_public_field"] = "must-not-pass-through"
    target = tmp_path / "report.json"
    _write_signed(target, tampered)

    assert (
        _load_verified_forward_source_coverage(target, dataset, preregistration)
        is None
    )


def test_forward_source_coverage_rejects_signed_future_timestamp(
    tmp_path: Path,
) -> None:
    report, dataset, preregistration = _inputs()
    tampered = deepcopy(report)
    tampered["generated_at"] = "2099-08-26T18:31:04+08:00"
    target = tmp_path / "report.json"
    _write_signed(target, tampered)

    assert (
        _load_verified_forward_source_coverage(target, dataset, preregistration)
        is None
    )
