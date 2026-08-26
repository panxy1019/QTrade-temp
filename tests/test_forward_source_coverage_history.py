import json
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from zoneinfo import ZoneInfo

import pytest

from scripts.update_baostock_forward_coverage_history import (
    append_coverage_history,
    publish_coverage_release,
    verify_coverage_releases,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _digest(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _coverage(*, hour: int, minute: int, ready: int) -> dict[str, object]:
    unsigned: dict[str, object] = {
        "schema_version": 1,
        "report_id": "baostock-forward-source-coverage-diagnostic-v1",
        "classification": "SOURCE_COVERAGE_DIAGNOSTIC_NOT_FORWARD_EVIDENCE",
        "generated_at": datetime(2026, 8, 26, hour, minute, tzinfo=SHANGHAI).isoformat(),
        "provider": "BaoStock",
        "provider_version": "0.9.3",
        "frequency": "5",
        "adjustflag": "2",
        "anchor_day": "2026-08-25",
        "target_day": "2026-08-26",
        "preregistration_id": "baostock-locked-forward-observation-v1",
        "preregistration_sha256": "a" * 64,
        "warmup_data_version": "warmup-v1",
        "warmup_checksum": "b" * 64,
        "warmup_evidence_sha256": "c" * 64,
        "required_symbol_count": 75,
        "required_bars_per_symbol_session": 48,
        "target_complete_symbol_count": ready,
        "ready_symbol_count": ready,
        "incomplete_symbol_count": 75 - ready,
        "incomplete_symbols": [f"{index:06d}" for index in range(75 - ready)],
        "target_incomplete_symbols": [
            f"{index:06d}" for index in range(75 - ready)
        ],
        "fetch_error_symbols": [],
        "coverage": [{} for _ in range(75)],
        "status": "READY_FOR_FORMAL_COLLECTION" if ready == 75 else "SOURCE_INCOMPLETE",
        "ready_for_formal_collection": ready == 75,
        "enters_forward_metrics": False,
        "promotion": "NOT_ELIGIBLE",
        "realtime_readiness": False,
        "real_data_paper_readiness": False,
        "external_order_transmission": False,
    }
    return {**unsigned, "evidence_sha256": _digest(unsigned)}


def test_coverage_history_appends_hash_chained_monotonic_progress() -> None:
    first, status = append_coverage_history(_coverage(hour=18, minute=14, ready=41))
    second, status = append_coverage_history(
        _coverage(hour=18, minute=31, ready=47), first
    )

    assert status == "APPENDED"
    assert second["checkpoint_count"] == 2
    assert second["all_checkpoints_monotonic"] is True
    assert second["checkpoints"][1]["ready_symbol_count_delta"] == 6
    assert second["checkpoints"][1]["previous_checkpoint_sha256"] == (
        second["checkpoints"][0]["checkpoint_sha256"]
    )
    unchanged, status = append_coverage_history(
        _coverage(hour=18, minute=31, ready=47), second
    )
    assert status == "UNCHANGED"
    assert unchanged == second


def test_coverage_history_retains_a_regression_without_calling_it_monotonic() -> None:
    first, _ = append_coverage_history(_coverage(hour=18, minute=31, ready=47))
    regressed, _ = append_coverage_history(
        _coverage(hour=18, minute=47, ready=46), first
    )

    assert regressed["all_checkpoints_monotonic"] is False
    assert regressed["checkpoints"][-1]["ready_symbol_count_delta"] == -1
    assert regressed["checkpoints"][-1]["monotonic_vs_previous"] is False


def test_coverage_history_rejects_tamper_identity_drift_and_stale_time() -> None:
    first, _ = append_coverage_history(_coverage(hour=18, minute=31, ready=47))
    tampered = deepcopy(first)
    tampered["checkpoints"][0]["ready_symbol_count"] = 74
    checkpoint = tampered["checkpoints"][0]
    checkpoint.pop("checkpoint_sha256")
    checkpoint["checkpoint_sha256"] = _digest(checkpoint)
    tampered.pop("evidence_sha256")
    tampered["evidence_sha256"] = _digest(tampered)
    with pytest.raises(ValueError, match="checkpoint chain"):
        append_coverage_history(_coverage(hour=18, minute=47, ready=52), tampered)

    drifted = _coverage(hour=18, minute=47, ready=52)
    drifted["warmup_checksum"] = "d" * 64
    drifted.pop("evidence_sha256")
    drifted["evidence_sha256"] = _digest(drifted)
    with pytest.raises(ValueError, match="identity changed"):
        append_coverage_history(drifted, first)

    with pytest.raises(ValueError, match="strictly advance"):
        append_coverage_history(_coverage(hour=18, minute=14, ready=52), first)


def test_coverage_release_is_immutable_and_content_addressed(tmp_path) -> None:
    report = _coverage(hour=18, minute=47, ready=52)
    root = tmp_path / "releases"

    target = publish_coverage_release(report, root)

    assert target.name == f"{report['evidence_sha256']}.json"
    assert json.loads(target.read_text(encoding="utf-8")) == report
    assert publish_coverage_release(report, root) == target
    history, _ = append_coverage_history(report)
    verify_coverage_releases(history, root)

    target.write_text("{}\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="identity already exists"):
        publish_coverage_release(report, root)
    with pytest.raises(ValueError):
        verify_coverage_releases(history, root)
