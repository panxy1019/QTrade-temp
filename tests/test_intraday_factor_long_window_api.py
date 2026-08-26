import json
from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient

from qtrade.api.app import bootstrap_context, create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    PROJECT_ROOT / "deployment/evidence/long-five-minute-market-data/manifest.json"
)
REPORT_PATH = (
    PROJECT_ROOT / "deployment/evidence/intraday-factor-long-window/report.json"
)


def _canonical_sha256(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _client(report_path: Path = REPORT_PATH) -> TestClient:
    context = bootstrap_context()
    context.long_five_minute_manifest_path = MANIFEST_PATH
    context.intraday_factor_robustness_path = report_path
    return TestClient(create_app(context))


def test_long_window_intraday_factor_report_is_verified_and_read_only() -> None:
    response = _client().get("/intraday-factor-robustness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["report_id"] == (
        "opening-triple-bullish-long-window-robustness-v1"
    )
    assert payload["classification"] == (
        "SIGNED_REUSED_LONG_WINDOW_INTRADAY_FACTOR_DIAGNOSTIC"
    )
    assert payload["evaluation_contract"]["selected_session_count"] == 120
    assert payload["evaluation_contract"]["initial_training_session_count"] == 60
    assert payload["evaluation_contract"]["fold_count"] == 6
    assert payload["evaluation_contract"]["test_sessions_per_fold"] == 10
    assert payload["summary"]["observation_count"] == 9_000
    assert payload["summary"]["signal_count"] == 1_001
    assert payload["summary"]["fold_count"] == 6
    assert payload["summary"]["positive_fold_count"] == 1
    assert payload["summary"]["selected_thresholds_bps"] == [
        "10",
        "10",
        "5",
        "5",
        "5",
        "5",
    ]
    assert float(payload["summary"]["full_window_strategy_metrics"]["total_return"]) < 0
    assert float(payload["summary"]["stitched_strategy_metrics"]["total_return"]) < 0
    bootstrap = payload["summary"]["stitched_excess_bootstrap"]
    assert bootstrap["method"] == "PAIRED_STATIONARY_BOOTSTRAP"
    assert bootstrap["observation_count"] == 60
    assert bootstrap["replications"] == 1_000
    assert bootstrap["expected_block_length"] == 10
    assert bootstrap["seed"] == 20260827
    assert bootstrap["observed_excess_total_return"] == (
        payload["summary"]["stitched_strategy_metrics"]["excess_return"]
    )
    assert (
        float(bootstrap["p05_excess_total_return"])
        <= float(bootstrap["median_excess_total_return"])
        <= float(bootstrap["p95_excess_total_return"])
    )
    assert 0 <= float(bootstrap["probability_positive_excess"]) <= 1
    assert len(payload["summary"]["threshold_sensitivity"]) == 4
    stability = payload["result"]["stability_validation"]
    assert stability["block_count"] == 4
    assert stability["positive_block_count"] == 2
    assert stability["passed"] is False
    assert float(stability["worst_block_mean_conditional_excess_return"]) < 0
    assert float(stability["maximum_absolute_block_contribution_share"]) < 0.60
    assert stability["criteria"]["positive_block_ratio_at_least_75pct"] is False
    paper_admission = payload["result"]["paper_admission"]
    assert paper_admission["decision"] == "BLOCKED"
    assert paper_admission["passed_gate_count"] == 1
    assert paper_admission["required_gate_count"] == 8
    assert paper_admission["all_required_gates_passed"] is False
    assert paper_admission["automatic_start_allowed"] is False
    assert paper_admission["runtime_mode_change"] is False
    assert paper_admission["state_mutation"] is False
    assert paper_admission["external_order_transmission"] is False
    strategy_identity = payload["result"]["strategy_identity"]
    strategy_spec = {
        key: value
        for key, value in payload["result"]["strategy"].items()
        if key not in {"conditional_exit_count", "fallback_time_stop_count"}
    }
    assert strategy_identity["spec_hash"] == _canonical_sha256(
        strategy_spec
    )
    assert strategy_identity["strategy_id"] == (
        f"adhoc-intraday-{strategy_identity['spec_hash'][:16]}"
    )
    assert strategy_identity["registration_status"] == "AD_HOC_UNREGISTERED"
    assert strategy_identity["paper_eligible"] is False
    assert strategy_identity["external_order_transmission"] is False
    advancement = payload["result"]["sequential_advancement"]
    assert advancement["passed_research_stage_count"] == 1
    assert advancement["required_research_stage_count"] == 3
    assert advancement["all_research_stages_passed"] is False
    assert advancement["first_blocking_stage"] == "FACTOR_VALIDATION"
    assert advancement["strategy_construction_status"] == (
        "DIAGNOSTIC_PREVIEW_ONLY"
    )
    assert advancement["causal_backtest_diagnostic_completed"] is True
    assert advancement["historical_result_can_authorize_paper"] is False
    assert advancement["viewed_window_reselection_allowed"] is False
    registration_gate = next(
        gate
        for gate in paper_admission["required_gates"]
        if gate["gate_id"]
        == "registered_immutable_strategy_version_available"
    )
    assert strategy_identity["strategy_id"] in registration_gate["evidence"]
    assert strategy_identity["spec_hash"] in registration_gate["evidence"]
    l2_readiness = payload["result"]["l2_factor_readiness"]
    assert l2_readiness["classification"] == (
        "READ_ONLY_L2_FACTOR_DATA_CONTRACT"
    )
    assert l2_readiness["passed_gate_count"] == 0
    assert l2_readiness["required_gate_count"] == 6
    assert l2_readiness["buying_hypothesis"]["validation_status"] == (
        "UNTESTED_NO_AUTHORIZED_L2_DATA"
    )
    assert l2_readiness["sell_condition_status"] == (
        "NOT_DEFINED_REQUIRES_SEPARATE_RESEARCH"
    )
    assert l2_readiness["derived_from_ohlcv"] is False
    assert l2_readiness["external_order_transmission"] is False
    assert payload["result_preview_hash"] == (
        "08d4a6f23e4dc5ec5499b4684cb879f0ec1ad242506af97c144afcfd46e13c79"
    )
    assert payload["promotion"] == "NOT_ELIGIBLE"
    assert payload["paper_readiness"] == "NOT_ELIGIBLE"
    assert payload["live_readiness"] == "DISABLED_REQUIRES_SEPARATE_AUTHORIZATION"
    assert payload["state_mutation"] is False
    assert payload["external_order_transmission"] is False


def test_long_window_intraday_factor_report_fails_closed_when_missing() -> None:
    payload = _client(PROJECT_ROOT / "missing-long-factor-report.json").get(
        "/intraday-factor-robustness"
    ).json()

    assert payload["report_id"] is None
    assert payload["summary"] is None
    assert payload["promotion"] == "NOT_EVALUATED"
    assert payload["paper_readiness"] == "NOT_ELIGIBLE"
    assert payload["external_order_transmission"] is False


def test_resigned_changed_result_cannot_replace_fixed_long_window_evidence(
    tmp_path: Path,
) -> None:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    report["result"]["robustness_validation"]["stitched_strategy_metrics"][
        "total_return"
    ] = "0.99"
    result_unsigned = {
        key: value
        for key, value in report["result"].items()
        if key != "preview_hash"
    }
    changed_result_hash = _canonical_sha256(result_unsigned)
    report["result"]["preview_hash"] = changed_result_hash
    report["result_preview_hash"] = changed_result_hash
    report_unsigned = {
        key: value for key, value in report.items() if key != "evidence_sha256"
    }
    report["evidence_sha256"] = _canonical_sha256(report_unsigned)
    changed_path = tmp_path / "changed-report.json"
    changed_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )

    payload = _client(changed_path).get("/intraday-factor-robustness").json()

    assert changed_result_hash != (
        "08d4a6f23e4dc5ec5499b4684cb879f0ec1ad242506af97c144afcfd46e13c79"
    )
    assert payload["report_id"] is None
    assert payload["promotion"] == "NOT_EVALUATED"
    assert payload["external_order_transmission"] is False


def test_resigned_changed_bootstrap_cannot_replace_recomputed_evidence(
    tmp_path: Path,
) -> None:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    report["summary"]["stitched_excess_bootstrap"][
        "probability_positive_excess"
    ] = "1"
    report_unsigned = {
        key: value for key, value in report.items() if key != "evidence_sha256"
    }
    report["evidence_sha256"] = _canonical_sha256(report_unsigned)
    changed_path = tmp_path / "changed-bootstrap-report.json"
    changed_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )

    payload = _client(changed_path).get("/intraday-factor-robustness").json()

    assert payload["report_id"] is None
    assert payload["promotion"] == "NOT_EVALUATED"
    assert payload["external_order_transmission"] is False
