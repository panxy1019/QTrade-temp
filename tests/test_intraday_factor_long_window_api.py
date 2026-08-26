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
    assert payload["result_preview_hash"] == (
        "6a21e8fc169b50c9bfb52254b45b4dccf658b38d5b875f3be51b2b5bd5f042a3"
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
        "6a21e8fc169b50c9bfb52254b45b4dccf658b38d5b875f3be51b2b5bd5f042a3"
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
