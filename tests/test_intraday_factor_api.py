import json
from decimal import Decimal
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
    assert 0 < payload["event_summary"]["clustered_signal_session_count"] <= 20
    assert payload["event_summary"]["newey_west_lag"] >= 0
    assert float(payload["event_summary"]["newey_west_standard_error"]) >= 0
    assert float(payload["event_summary"]["confidence_interval_95_lower"]) <= float(
        payload["event_summary"]["mean_session_conditional_excess_return"]
    ) <= float(payload["event_summary"]["confidence_interval_95_upper"])
    assert "minimum_ten_clustered_signal_sessions" in payload["research_gate"][
        "criteria"
    ]
    assert "newey_west_t_statistic_above_1_96" in payload["research_gate"][
        "criteria"
    ]
    stability = payload["stability_validation"]
    assert stability["classification"] == (
        "CONTIGUOUS_SESSION_BLOCK_FACTOR_STABILITY_DIAGNOSTIC"
    )
    assert stability["block_count"] == 4
    assert len(stability["blocks"]) == 4
    assert sum(block["session_count"] for block in stability["blocks"]) == 20
    assert all(
        left["end"] < right["start"]
        for left, right in zip(
            stability["blocks"][:-1],
            stability["blocks"][1:],
            strict=True,
        )
    )
    assert stability["contribution_share_threshold"] == "0.60"
    assert "positive_block_ratio_at_least_75pct" in stability["criteria"]
    assert "temporal_stability_passed" in payload["research_gate"]["criteria"]
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
    assert "exit_factor_validation" not in payload
    assert "exit_factor_effectiveness_passed" not in payload["research_gate"][
        "criteria"
    ]
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


def test_intraday_factor_backtest_accepts_a_causal_bearish_exit_rule() -> None:
    exit_rule = (
        "NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_ELSE_14_55_TIME_STOP"
    )
    response = _client().post(
        "/intraday-factor-backtest",
        json={
            "factor": "opening_three_ten_minute_bullish",
            "start": "2026-07-28",
            "end": "2026-08-24",
            "cost_bps": 5,
            "exit_rule": exit_rule,
            "exit_minimum_body_return_bps": 5,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_bar_count"] == 21 * 75 * 48
    assert payload["parameters"]["exit_rule"] == exit_rule
    assert payload["parameters"]["exit_minimum_body_return_bps"] == "5"
    strategy = payload["strategy"]
    assert strategy["exit_rule"] == exit_rule
    assert strategy["exit_condition"] == (
        "bearish_10m_0930_0940 AND bearish_10m_0940_0950 "
        "AND bearish_10m_0950_1000"
    )
    assert strategy["exit_condition_known_at"] == (
        "NEXT_SESSION_10_00_BAR_CLOSE_T_PLUS_ONE"
    )
    assert strategy["conditional_exit_price_source"] == (
        "OPEN_OF_NEXT_SESSION_BAR_LABELLED_10_05_INTERVAL_START_10_00"
    )
    assert strategy["fallback_exit_price_source"] == (
        "OPEN_OF_NEXT_SESSION_BAR_LABELLED_15_00_INTERVAL_START_14_55"
    )
    assert strategy["conditional_exit_count"] + strategy[
        "fallback_time_stop_count"
    ] == payload["event_summary"]["signal_count"]
    exit_validation = payload["exit_factor_validation"]
    assert exit_validation["classification"] == (
        "TRIGGERED_EXIT_TIMING_ADVANTAGE_DIAGNOSTIC"
    )
    assert exit_validation["entry_signal_count"] == payload["event_summary"][
        "signal_count"
    ]
    assert exit_validation["trigger_count"] == strategy[
        "conditional_exit_count"
    ] == 28
    assert exit_validation["clustered_trigger_session_count"] > 0
    assert exit_validation["advantage_definition"] == (
        "CONDITIONAL_EXIT_OPEN_DIVIDED_BY_TIME_STOP_OPEN_MINUS_ONE"
    )
    assert exit_validation["criteria"]["minimum_twenty_exit_triggers"] is True
    assert exit_validation["criteria"][
        "minimum_ten_clustered_exit_trigger_sessions"
    ] is (exit_validation["clustered_trigger_session_count"] >= 10)
    assert exit_validation["passed"] is False
    assert payload["research_gate"]["criteria"][
        "exit_factor_effectiveness_passed"
    ] is False
    assert payload["causal_contract"]["same_bar_execution"] is False
    assert payload["causal_contract"]["t_plus_one_compliant"] is True
    assert payload["promotion"] == "NOT_ELIGIBLE"
    assert payload["paper_readiness"] == "NOT_ELIGIBLE"
    assert payload["live_readiness"] == (
        "DISABLED_REQUIRES_SEPARATE_AUTHORIZATION"
    )
    assert payload["state_mutation"] is False
    assert payload["external_order_transmission"] is False


def test_intraday_factor_backtest_rejects_invalid_exit_contracts() -> None:
    client = _client()
    ignored_threshold = client.post(
        "/intraday-factor-backtest",
        json={
            "factor": "opening_three_ten_minute_bullish",
            "start": "2026-07-28",
            "end": "2026-08-24",
            "exit_minimum_body_return_bps": 1,
        },
    )
    assert ignored_threshold.status_code == 422
    assert "must be zero for NEXT_SESSION_OPEN" in ignored_threshold.text

    unknown_rule = client.post(
        "/intraday-factor-backtest",
        json={
            "factor": "opening_three_ten_minute_bullish",
            "start": "2026-07-28",
            "end": "2026-08-24",
            "exit_rule": "EVAL",
        },
    )
    assert unknown_rule.status_code == 422


def test_intraday_factor_discovery_locks_one_of_104_candidates_before_test() -> None:
    response = _client().post(
        "/intraday-factor-discovery",
        json={
            "start": "2026-06-30",
            "end": "2026-08-24",
            "cost_bps": 5,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["classification"] == (
        "BOUNDED_INTRADAY_FACTOR_DISCOVERY_REUSED_HISTORY_NOT_INDEPENDENT"
    )
    assert payload["candidate_contract"] == {
        "direction_values": ["BULLISH", "BEARISH", "IGNORE"],
        "active_slot_requirement": "AT_LEAST_ONE",
        "combination": "ALL",
        "thresholds_bps": ["0", "5", "10", "20"],
        "candidate_count": 104,
        "arbitrary_code_execution": False,
        "future_features_used": False,
    }
    assert payload["split"]["training_session_count"] == 20
    assert payload["split"]["test_session_count"] == 20
    assert payload["split"]["training_end"] < payload["split"]["test_start"]
    assert payload["split"]["selection_known_at"].endswith("09:35:00+08:00")
    assert payload["split"]["first_test_signal_known_at"].endswith(
        "10:00:00+08:00"
    )
    assert payload["split"]["selection_uses_test_returns"] is False
    assert len(payload["candidates"]) == 104
    audit = payload["overfitting_audit"]
    assert audit["method"] == "COMBINATORIALLY_SYMMETRIC_CROSS_VALIDATION"
    assert audit["block_count"] == 8
    assert audit["split_count"] == len(audit["splits"]) == 70
    assert audit["selection_metric"] == (
        "TRAINING_GATE_THEN_NEWEY_WEST_T_THEN_SESSION_CONDITIONAL_EXCESS"
    )
    assert audit["rank_components"] == [
        "GATE_PASSED",
        "NEWEY_WEST_T_STATISTIC",
        "SESSION_CONDITIONAL_EXCESS",
        "LOWER_BODY_THRESHOLD",
    ]
    assert audit["rank_tie_method"] == "AVERAGE_RANK"
    assert len(audit["blocks"]) == 8
    assert sum(block["session_count"] for block in audit["blocks"]) == 40
    assert audit["blocks"][0]["start"] == "2026-06-30"
    assert audit["blocks"][-1]["end"] == "2026-08-24"
    assert audit["reuses_original_locked_test_blocks"] is True
    assert audit["candidate_selection_uses_only_each_cscv_training_partition"] is True
    assert audit["changes_original_locked_candidate"] is False
    assert audit["independent_confirmation"] is False
    assert audit["promotion_authority"] is False
    assert Decimal(audit["probability_of_backtest_overfitting"]) == (
        Decimal(audit["below_median_split_count"]) / audit["split_count"]
    )
    assert all(
        set(split["training_block_ids"]).isdisjoint(split["test_block_ids"])
        and sorted(split["training_block_ids"] + split["test_block_ids"])
        == list(range(1, 9))
        for split in audit["splits"]
    )
    cost_stress = payload["locked_test_cost_stress"]
    assert cost_stress["costs_bps"] == ["0", "5", "10", "20"]
    assert cost_stress["selected_candidate_id"] == payload["selected"][
        "candidate_id"
    ]
    assert cost_stress["test_start"] == payload["split"]["test_start"]
    assert cost_stress["test_end"] == payload["split"]["test_end"]
    assert cost_stress["signal_count"] == payload["test_validation"][
        "signal_count"
    ]
    assert cost_stress["candidate_reselected"] is False
    assert cost_stress["cost_rows_used_for_selection"] is False
    assert set(cost_stress["rows"]) == {"0", "5", "10", "20"}
    assert all(
        row["strategy_metrics"]["number_of_periods"] == 20
        and row["strategy_metrics"]["number_of_trades"]
        == payload["test_validation"]["signal_count"]
        for row in cost_stress["rows"].values()
    )
    assert cost_stress["passed"] is all(cost_stress["criteria"].values())
    assert payload["discovery_gate"] == {
        "criteria": {
            "chronological_locked_test_passed": payload["test_validation"][
                "passed"
            ],
            "selection_bias_audit_passed": audit["passed"],
            "locked_test_cost_stress_passed": cost_stress["passed"],
            "locked_test_sector_stress_passed": payload[
                "locked_test_sector_stress"
            ]["passed"],
            "locked_test_symbol_stress_passed": payload[
                "locked_test_symbol_stress"
            ]["passed"],
            "locked_test_regime_stress_passed": payload[
                "locked_test_regime_stress"
            ]["passed"],
        },
        "passed": False,
        "promotion_authority": False,
    }
    sector_stress = payload["locked_test_sector_stress"]
    assert sector_stress["selected_candidate_id"] == payload["selected"][
        "candidate_id"
    ]
    assert sector_stress["test_start"] == payload["split"]["test_start"]
    assert sector_stress["test_end"] == payload["split"]["test_end"]
    assert sector_stress["candidate_reselected"] is False
    assert sector_stress["sector_rows_used_for_selection"] is False
    assert len(sector_stress["sector_only"]) == 15
    assert len(sector_stress["leave_one_sector_out"]) == 15
    assert {
        (row["sector_code"], row["sector_name"])
        for row in sector_stress["sector_only"]
    } == {
        (row["excluded_sector_code"], row["excluded_sector_name"])
        for row in sector_stress["leave_one_sector_out"]
    }
    assert sum(row["observation_count"] for row in sector_stress["sector_only"]) == (
        payload["test_validation"]["observation_count"]
    )
    assert sector_stress["passed"] is all(sector_stress["criteria"].values())
    symbol_stress = payload["locked_test_symbol_stress"]
    assert symbol_stress["selected_candidate_id"] == payload["selected"][
        "candidate_id"
    ]
    assert symbol_stress["test_start"] == payload["split"]["test_start"]
    assert symbol_stress["test_end"] == payload["split"]["test_end"]
    assert symbol_stress["candidate_reselected"] is False
    assert symbol_stress["symbol_rows_used_for_selection"] is False
    assert len(symbol_stress["symbols"]) == 75
    assert len({row["symbol"] for row in symbol_stress["symbols"]}) == 75
    assert sum(row["observation_count"] for row in symbol_stress["symbols"]) == (
        payload["test_validation"]["observation_count"]
    )
    assert sum(row["signal_count"] for row in symbol_stress["symbols"]) == payload[
        "test_validation"
    ]["signal_count"]
    assert len(symbol_stress["top_five_absolute_return_symbols"]) == 5
    assert symbol_stress["passed"] is all(symbol_stress["criteria"].values())
    regime_stress = payload["locked_test_regime_stress"]
    assert regime_stress["selected_candidate_id"] == payload["selected"][
        "candidate_id"
    ]
    assert regime_stress["test_start"] == payload["split"]["test_start"]
    assert regime_stress["test_end"] == payload["split"]["test_end"]
    assert regime_stress["candidate_reselected"] is False
    assert regime_stress["regime_rows_used_for_selection"] is False
    assert regime_stress["regime_definition"] == (
        "PREVIOUS_SESSION_EQUAL_WEIGHT_UNIVERSE_GROSS_RETURN_SIGN"
    )
    assert regime_stress["regime_result_known_at"] == "CURRENT_SESSION_09_35"
    assert regime_stress["current_signal_known_at"] == "CURRENT_SESSION_10_00"
    assert {row["regime_id"] for row in regime_stress["regimes"]} == {
        "PREVIOUS_SESSION_NEGATIVE",
        "PREVIOUS_SESSION_NON_NEGATIVE",
    }
    assert sum(row["session_count"] for row in regime_stress["regimes"]) == 20
    assert sum(
        row["observation_count"] for row in regime_stress["regimes"]
    ) == payload["test_validation"]["observation_count"]
    assert sum(row["signal_count"] for row in regime_stress["regimes"]) == payload[
        "test_validation"
    ]["signal_count"]
    assert all(row["known_before_signal"] for row in regime_stress["regimes"])
    assert regime_stress["passed"] is all(regime_stress["criteria"].values())
    assert payload["selected"]["candidate_id"] in {
        candidate["candidate_id"] for candidate in payload["candidates"]
    }
    assert payload["selected"]["directions"] != ["IGNORE"] * 3
    assert payload["test_validation"]["observation_count"] == 20 * 75
    assert payload["source_bar_count"] == 41 * 75 * 48
    assert payload["symbol_count"] == 75
    assert payload["sector_count"] == 15
    assert payload["promotion"] == "NOT_ELIGIBLE"
    assert payload["paper_readiness"] == "NOT_ELIGIBLE"
    assert payload["live_readiness"] == (
        "DISABLED_REQUIRES_SEPARATE_AUTHORIZATION"
    )
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


def test_intraday_factor_discovery_requires_forty_sessions() -> None:
    response = _client().post(
        "/intraday-factor-discovery",
        json={
            "start": "2026-07-28",
            "end": "2026-08-24",
        },
    )

    assert response.status_code == 422
    assert "at least 40 sessions" in response.text


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
