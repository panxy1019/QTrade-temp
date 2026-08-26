import json
from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient

from qtrade.api.app import bootstrap_context, create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    PROJECT_ROOT / "deployment/evidence/long-five-minute-market-data/manifest.json"
)


def _client() -> TestClient:
    context = bootstrap_context()
    context.long_five_minute_manifest_path = MANIFEST_PATH
    return TestClient(create_app(context))


def test_intraday_factor_backtest_exposes_complete_read_only_research_chain() -> None:
    response = _client().post(
        "/intraday-factor-backtest",
        json={
            "factor": "opening_three_ten_minute_bullish",
            "start": "2026-07-28",
            "end": "2026-08-24",
            "cost_bps": 5,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["classification"] == (
        "AD_HOC_INTRADAY_FACTOR_RESEARCH_CURRENT_NON_PIT"
    )
    assert payload["factor_definition"]["factor_id"] == (
        "opening-three-ten-minute-bullish-v1"
    )
    assert payload["factor_definition"]["expression"] == (
        "bullish_10m_0930_0940 AND bullish_10m_0940_0950 "
        "AND bullish_10m_0950_1000"
    )
    assert payload["selected_session_count"] == 20
    assert payload["source_bar_count"] == 21 * 75 * 48
    assert payload["factor_validation"]["number_of_periods"] == 20
    assert payload["factor_validation"]["minimum_instruments"] == 75
    assert payload["event_summary"]["observation_count"] == 20 * 75
    assert payload["event_summary"]["signal_count"] > 0
    assert set(payload["correlation_validation"]["correlations"]) == {
        "opening_30m_return",
        "opening_30m_range_position",
    }
    robustness = payload["robustness_validation"]
    assert robustness["classification"] == (
        "REUSED_CHRONOLOGICAL_WALK_FORWARD_DIAGNOSTIC_NOT_INDEPENDENT"
    )
    assert robustness["candidate_thresholds_bps"] == ["0", "5", "10", "20"]
    assert robustness["initial_training_session_count"] == 10
    assert robustness["stitched_test_session_count"] == 10
    assert len(robustness["folds"]) == 2
    assert [fold["test_session_count"] for fold in robustness["folds"]] == [5, 5]
    assert all(
        fold["training_end"] < fold["test_start"]
        and fold["selection_known_at"].endswith("09:35:00+08:00")
        for fold in robustness["folds"]
    )
    assert robustness["stitched_strategy_metrics"]["number_of_periods"] == 10
    assert len(robustness["threshold_sensitivity"]) == 4
    assert robustness["criteria"]["causal_selection_known_before_test_signal"] is True
    assert payload["strategy"]["entry_rule"] == "NEXT_5M_OPEN_10_05"
    assert payload["strategy"]["exit_rule"] == "NEXT_SESSION_OPEN_T_PLUS_ONE"
    assert payload["backtest"]["strategy_metrics"]["number_of_periods"] == 20
    assert set(payload["backtest"]["cost_stress"]) == {"0", "5", "10", "20"}
    assert len(payload["backtest"]["curve"]) == 20
    assert len(payload["backtest"]["sectors"]) == 15
    assert len(payload["backtest"]["symbols"]) == 75
    assert [stage["id"] for stage in payload["workflow"]] == [
        "FACTOR_DEFINITION",
        "FACTOR_VALIDATION",
        "CORRELATION_VALIDATION",
        "ROBUSTNESS_VALIDATION",
        "STRATEGY_CONSTRUCTION",
        "CAUSAL_BACKTEST",
        "PAPER",
        "LIVE",
    ]
    assert payload["l2_factor_readiness"] == {
        "factor_id": "order-book-buying-intensity-v1",
        "formula": "(bid_size_1+...+bid_size_5)/(ask_size_1+...+ask_size_5)",
        "required_data": "AUTHORIZED_L2_FIVE_LEVEL_ORDER_BOOK",
        "readiness": "UNCONFIGURED",
        "reason": "NO_AUTHORIZED_L2_PROVIDER",
        "derived_from_ohlcv": False,
    }
    assert payload["causal_contract"]["same_bar_execution"] is False
    assert payload["causal_contract"]["t_plus_one_compliant"] is True
    assert payload["promotion"] == "NOT_ELIGIBLE"
    assert payload["paper_readiness"] == "NOT_ELIGIBLE"
    assert payload["live_readiness"] == "DISABLED_REQUIRES_SEPARATE_AUTHORIZATION"
    assert payload["state_mutation"] is False
    assert payload["external_order_transmission"] is False
    preview_hash = payload.pop("preview_hash")
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert preview_hash == sha256(canonical.encode("utf-8")).hexdigest()


def test_intraday_factor_backtest_rejects_unclosed_or_invalid_scope() -> None:
    client = _client()
    cases = [
        (
            {
                "factor": "opening_three_ten_minute_bullish",
                "start": "2026-07-28",
                "end": "2026-08-25",
            },
            "next verified trading session",
        ),
        (
            {
                "factor": "opening_three_ten_minute_bullish",
                "start": "2026-08-20",
                "end": "2026-08-24",
            },
            "at least 20 sessions",
        ),
        (
            {
                "factor": "opening_three_ten_minute_bullish",
                "start": "2026-07-28",
                "end": "2026-08-24",
                "cost_bps": 101,
            },
            "between 0 and 100",
        ),
    ]
    for request, detail in cases:
        response = client.post("/intraday-factor-backtest", json=request)
        assert response.status_code == 422
        assert detail in response.json()["detail"]


def test_intraday_factor_backtest_fails_closed_without_verified_data() -> None:
    response = TestClient(create_app(bootstrap_context())).post(
        "/intraday-factor-backtest",
        json={
            "factor": "opening_three_ten_minute_bullish",
            "start": "2026-07-28",
            "end": "2026-08-24",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "verified long five-minute dataset unavailable"
    )


def test_intraday_factor_backtest_accepts_a_bounded_custom_direction_rule() -> None:
    response = _client().post(
        "/intraday-factor-backtest",
        json={
            "factor": "custom_opening_ten_minute_direction_rule",
            "start": "2026-07-28",
            "end": "2026-08-24",
            "cost_bps": 5,
            "minimum_body_return_bps": 0,
            "directions": ["BULLISH", "BEARISH", "IGNORE"],
            "combination": "ALL",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["factor_definition"] == {
        "factor_id": "opening-ten-minute-direction-rule-v1",
        "name": "自定义开盘十分钟方向规则",
        "expression": "bullish_10m_0930_0940 AND bearish_10m_0940_0950",
        "source_interval": "NATIVE_5M_AGGREGATED_TO_10M",
        "source_timestamp_convention": "BAR_END_TIME",
        "source_bars": "09:35,09:40,09:45,09:50,09:55,10:00",
        "known_at": "10:00_BAR_CLOSE",
        "minimum_body_return_bps": "0",
        "directions": ["BULLISH", "BEARISH", "IGNORE"],
        "combination": "ALL",
        "allowed_features": [
            "TEN_MINUTE_BODY_0930_0940",
            "TEN_MINUTE_BODY_0940_0950",
            "TEN_MINUTE_BODY_0950_1000",
        ],
        "arbitrary_code_execution": False,
    }
    assert payload["parameters"]["directions"] == [
        "BULLISH",
        "BEARISH",
        "IGNORE",
    ]
    assert payload["causal_contract"]["same_bar_execution"] is False
    assert payload["paper_readiness"] == "NOT_ELIGIBLE"
    assert payload["state_mutation"] is False
    assert payload["external_order_transmission"] is False


def test_intraday_factor_backtest_rejects_empty_or_mutated_rule_contracts() -> None:
    client = _client()
    empty = client.post(
        "/intraday-factor-backtest",
        json={
            "factor": "custom_opening_ten_minute_direction_rule",
            "start": "2026-07-28",
            "end": "2026-08-24",
            "directions": ["IGNORE", "IGNORE", "IGNORE"],
        },
    )
    assert empty.status_code == 422
    assert "at least one completed ten-minute bar" in empty.text

    mutated_fixed = client.post(
        "/intraday-factor-backtest",
        json={
            "factor": "opening_three_ten_minute_bullish",
            "start": "2026-07-28",
            "end": "2026-08-24",
            "directions": ["BULLISH", "BEARISH", "BULLISH"],
        },
    )
    assert mutated_fixed.status_code == 422
    assert "fixed three-bullish factor cannot override" in mutated_fixed.text
