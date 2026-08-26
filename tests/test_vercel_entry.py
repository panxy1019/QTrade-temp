from urllib.parse import quote

from fastapi.testclient import TestClient

from qtrade.api.vercel import app


def _api(path: str) -> str:
    return f"/api?route={quote(path, safe='')}"


def test_strategy_template_catalog_exposes_only_implemented_read_only_baselines() -> None:
    client = TestClient(app)

    response = client.get(_api("/strategy-templates"))

    assert response.status_code == 200
    catalog = response.json()
    assert catalog["classification"] == "READ_ONLY_IMPLEMENTED_BASELINE_STRATEGY_CATALOG"
    assert catalog["template_count"] == 5
    assert catalog["active_template_count"] == 4
    assert catalog["benchmark_template_count"] == 1
    assert catalog["causal_preview_template_count"] == 3
    assert [template["template_id"] for template in catalog["templates"]] == [
        "equal-weight-buy-hold-benchmark-v1",
        "moving-average-trend-v1",
        "etf-momentum-rotation-v1",
        "etf-momentum-trend-volatility-v1",
        "cross-sectional-factor-ranking-v1",
    ]
    assert [template["implementation_ref"] for template in catalog["templates"]] == [
        "qtrade.research.multi_asset.EqualWeightBuyHold",
        "qtrade.strategies.baselines.MovingAverageTrendStrategy",
        "qtrade.research.multi_asset.MomentumAllocation",
        "qtrade.research.multi_asset.MomentumAllocation",
        "qtrade.research.multi_asset.RankedFactorAllocation",
    ]
    assert catalog["templates"][0]["role"] == "BENCHMARK_ONLY"
    assert [
        template["template_id"]
        for template in catalog["templates"]
        if template["causal_preview_supported"]
    ] == [
        "etf-momentum-rotation-v1",
        "etf-momentum-trend-volatility-v1",
        "cross-sectional-factor-ranking-v1",
    ]
    assert all(
        template["validation_status"] == "IMPLEMENTED_NOT_PRODUCTION_VALIDATED"
        for template in catalog["templates"]
    )
    assert catalog["selection_creates_experiment"] is False
    assert catalog["selection_registers_strategy"] is False
    assert catalog["paper_runtime_start"] is False
    assert catalog["live_runtime_start"] is False
    assert catalog["arbitrary_code_execution"] is False
    assert catalog["external_order_transmission"] is False
    assert catalog["next_step"] == "FREEZE_EXPERIMENT_RUN_SPEC_THEN_CAUSAL_BACKTEST"


def test_vercel_entry_is_read_only_and_loads_packaged_evidence() -> None:
    client = TestClient(app)

    assert client.get(_api("/health")).status_code == 200
    deployment = client.get(_api("/deployment")).json()
    assert deployment["classification"] == "PUBLIC_BUILD_PROVENANCE"
    assert deployment["external_order_transmission"] is False
    market = client.get(_api("/market"))
    assert market.status_code == 200
    assert market.json()["historical"]["bar_count"] == 28_770
    assert len(client.get(_api("/historical-bars/latest")).json()) == 30
    assert client.get(_api("/factor-search")).json()["promotion"] == "NOT_ELIGIBLE"
    assert client.get(_api("/factor-robustness")).json()["promotion"] == "NOT_ELIGIBLE"
    assert client.get(_api("/stock-sector-transfer")).json()["promotion"] == "NOT_ELIGIBLE"
    assert len(client.get(_api("/strategies")).json()) == 11
    assert len(client.get(_api("/experiments")).json()["experiments"]) == 11
    assert len(client.get(_api("/walk-forward")).json()["result"]["folds"]) == 3
    assert len(client.get(_api("/robustness")).json()["result"]["scenario_metrics"]) == 14
    minute = client.get(_api("/minute-research")).json()
    assert minute["promotion"] == "NOT_ELIGIBLE"
    assert minute["minute_dataset"]["bar_count"] == 115_430
    assert minute["minute_dataset"]["symbol_count"] == 97
    assert minute["minute_dataset"]["stock_count"] == 75
    assert len(minute["leave_one_sector_out"]) == 15
    assert {row["excluded_symbol_count"] for row in minute["leave_one_sector_out"]} == {
        5
    }
    minute_bars = client.get(_api("/minute-bars/510300")).json()
    assert minute_bars["observation_type"] == "HISTORICAL_MINUTE_BAR_SERIES"
    assert minute_bars["total_count"] == 1_190
    assert all(
        bar["observation_type"] == "HISTORICAL_MINUTE_BAR"
        for bar in minute_bars["bars"]
    )
    five_minute = client.get(_api("/five-minute-research")).json()
    assert five_minute["promotion"] == "NOT_ELIGIBLE"
    assert five_minute["minute_dataset"]["bar_interval"] == "5m"
    assert five_minute["minute_dataset"]["bar_count"] == 193_920
    assert five_minute["minute_dataset"]["symbol_count"] == 101
    assert five_minute["minute_dataset"]["session_count"] == 40
    assert len(five_minute["folds"]) == 4
    assert five_minute["aggregate"]["positive_test_fold_count"] == 1
    assert five_minute["aggregate"]["excess_return"] < 0
    long_five_minute = client.get(_api("/long-five-minute-research")).json()
    assert long_five_minute["promotion"] == "NOT_ELIGIBLE"
    assert long_five_minute["minute_dataset"]["provider"] == "BaoStock"
    assert long_five_minute["minute_dataset"]["bar_count"] == 1_324_800
    assert long_five_minute["minute_dataset"]["symbol_count"] == 75
    assert long_five_minute["minute_dataset"]["session_count"] == 368
    assert len(long_five_minute["folds"]) == 6
    assert long_five_minute["walk_forward"]["unused_trailing_sessions"] == 8
    assert long_five_minute["walk_forward"]["processed_bar_count"] == 1_296_000
    assert (
        long_five_minute["walk_forward"]["trailing_sessions_entered_backtest"]
        is False
    )
    assert long_five_minute["aggregate"]["excess_return"] < 0
    assert long_five_minute["aggregate"]["execution"]["observation_count"] == 864_000
    assert long_five_minute["aggregate"]["execution"]["mean_exposure"] < 0.64
    assert (
        long_five_minute["aggregate"]["execution"]["reduced_exposure_ratio"]
        > 0.8
    )
    assert len(
        {fold["execution"]["observation_count"] for fold in long_five_minute["folds"]}
    ) == 1
    intraday_factor_long = client.get(
        _api("/intraday-factor-robustness")
    ).json()
    assert intraday_factor_long["report_id"] == (
        "opening-triple-bullish-long-window-robustness-v1"
    )
    assert intraday_factor_long["summary"]["observation_count"] == 9_000
    assert intraday_factor_long["summary"]["signal_count"] == 1_001
    assert intraday_factor_long["summary"]["fold_count"] == 6
    assert intraday_factor_long["summary"]["positive_fold_count"] == 1
    assert float(
        intraday_factor_long["summary"]["stitched_strategy_metrics"][
            "total_return"
        ]
    ) < 0
    assert intraday_factor_long["promotion"] == "NOT_ELIGIBLE"
    assert intraday_factor_long["paper_readiness"] == "NOT_ELIGIBLE"
    assert intraday_factor_long["live_readiness"] == (
        "DISABLED_REQUIRES_SEPARATE_AUTHORIZATION"
    )
    assert intraday_factor_long["state_mutation"] is False
    assert intraday_factor_long["external_order_transmission"] is False
    reserved_tail = client.get(_api("/reserved-tail-diagnostic")).json()
    assert reserved_tail["promotion"] == "NOT_ELIGIBLE"
    assert reserved_tail["all_required_gates_passed"] is False
    assert sum(bool(gate["passed"]) for gate in reserved_tail["gates"]) == 2
    assert reserved_tail["test_session_count"] == 8
    assert reserved_tail["five_bps"]["candidate"]["total_return"] < 0
    assert (
        reserved_tail["five_bps"]["candidate"]["total_return"]
        < reserved_tail["five_bps"]["comparator"]["total_return"]
    )
    assert reserved_tail["five_bps"]["candidate_execution"]["mean_exposure"] < 0.85
    assert reserved_tail["sector_win_count"] == 6
    assert len(reserved_tail["daily"]) == 8
    assert len(reserved_tail["curve"]) == 384
    assert reserved_tail["external_order_transmission"] is False
    development = client.get(_api("/development-policy-search")).json()
    assert development["candidate_count"] == 4
    assert development["observed_leader"]["policy_id"] == (
        "dev-floor-098-slow12x48-v1"
    )
    assert development["promotion"] == "NOT_ELIGIBLE"
    assert development["external_order_transmission"] is False
    forward = client.get(_api("/forward-observation")).json()
    assert forward["policy_id"] == "dev-floor-098-slow12x48-v1"
    assert forward["future_session_count"] == 1
    assert forward["minimum_complete_future_sessions"] == 20
    assert forward["observation_status"] == "INCOMPLETE"
    assert [row["passed"] for row in forward["gates"]] == [True] * 5
    assert forward["all_required_gates_passed"] is False
    assert forward["promotion"] == "NOT_ELIGIBLE"
    assert forward["external_order_transmission"] is False
    coverage = client.get(_api("/forward-source-coverage")).json()
    assert coverage["classification"] == (
        "SOURCE_COVERAGE_DIAGNOSTIC_NOT_FORWARD_EVIDENCE"
    )
    assert coverage["target_complete_symbol_count"] == 75
    assert coverage["ready_symbol_count"] == 75
    assert coverage["incomplete_symbol_count"] == 0
    assert coverage["ready_for_formal_collection"] is True
    assert coverage["enters_forward_metrics"] is False
    assert coverage["external_order_transmission"] is False
    coverage_history = client.get(_api("/forward-source-coverage-history")).json()
    assert coverage_history["checkpoint_count"] == 6
    assert coverage_history["all_checkpoints_monotonic"] is True
    assert coverage_history["checkpoints"][-1]["ready_symbol_count"] == 75
    assert coverage_history["checkpoints"][-1]["ready_symbol_count_delta"] == 1
    assert coverage_history["enters_forward_metrics"] is False
    assert coverage_history["external_order_transmission"] is False
    public_audit = client.get(_api("/public-minute-audit")).json()
    assert public_audit["all_valid"] is True
    assert public_audit["sector_count"] == 15
    assert public_audit["first_round"]["request_count"] == 45
    assert public_audit["first_round"]["vercel_cache_counts"] == {"MISS": 45}
    assert public_audit["repeat_round"]["request_count"] == 45
    assert public_audit["repeat_round"]["vercel_cache_counts"] == {"HIT": 45}
    assert public_audit["public_write_boundary"]["request_count"] == 7
    assert public_audit["public_write_boundary"]["all_blocked"] is True
    assert public_audit["external_order_transmission"] is False
    intraday = client.get(_api("/intraday-observation")).json()
    assert intraday["symbol_count"] == 15
    assert intraday["sector_count"] == 15
    assert intraday["session_phase"] in {
        "PRE_OPEN",
        "MORNING_SESSION",
        "MIDDAY_BREAK",
        "AFTERNOON_SESSION",
        "POST_CLOSE",
    }
    assert 0 <= intraday["same_day_symbol_count"] <= 15
    assert intraday["realtime_readiness"] is False
    assert intraday["real_data_paper_readiness"] is False
    assert intraday["promotion"] == "NOT_ELIGIBLE"
    assert intraday["external_order_transmission"] is False
    breadth = client.get(_api("/intraday-breadth-observation")).json()
    assert breadth["symbol_count"] == 75
    assert breadth["sector_count"] == 15
    assert breadth["symbols_per_sector"] == 5
    assert len(breadth["observations"]) == 75
    assert len(breadth["sector_summaries"]) == 15
    assert breadth["realtime_readiness"] is False
    assert breadth["real_data_paper_readiness"] is False
    assert breadth["promotion"] == "NOT_ELIGIBLE"
    assert breadth["external_order_transmission"] is False
    continuity = client.get(_api("/intraday-continuity")).json()
    assert continuity["checkpoint_count"] >= 1
    assert continuity["all_checkpoints_monotonic"] is True
    assert continuity["checkpoints"][-1]["detail"]["symbol_count"] == 15
    assert continuity["checkpoints"][-1]["breadth"]["symbol_count"] == 75
    assert continuity["realtime_readiness"] is False
    assert continuity["real_data_paper_readiness"] is False
    assert continuity["promotion"] == "NOT_ELIGIBLE"
    assert continuity["external_order_transmission"] is False
    five_minute_bars = client.get(_api("/five-minute-bars/601166")).json()
    assert five_minute_bars["observation_type"] == (
        "HISTORICAL_FIVE_MINUTE_BAR_SERIES"
    )
    assert five_minute_bars["interval"] == "5m"
    assert five_minute_bars["total_count"] == 1_920
    assert all(
        bar["observation_type"] == "HISTORICAL_FIVE_MINUTE_BAR"
        for bar in five_minute_bars["bars"]
    )
    long_five_minute_bars = client.get(
        _api("/long-five-minute-bars/600036")
    ).json()
    assert long_five_minute_bars["observation_type"] == (
        "HISTORICAL_FIVE_MINUTE_BAR_SERIES"
    )
    assert long_five_minute_bars["selected_trading_day"] == "2026-08-25"
    assert long_five_minute_bars["total_count"] == 48
    assert long_five_minute_bars["truncated"] is False
    assert {bar["trading_day"] for bar in long_five_minute_bars["bars"]} == {
        "2026-08-25"
    }
    assert all(
        bar["observation_type"] == "HISTORICAL_FIVE_MINUTE_BAR"
        for bar in long_five_minute_bars["bars"]
    )
    early_long_five_minute_bars = client.get(
        f'{_api("/long-five-minute-bars/600036")}&trading_day=2025-01-02'
    ).json()
    assert early_long_five_minute_bars["selected_trading_day"] == "2025-01-02"
    assert early_long_five_minute_bars["total_count"] == 48
    assert {bar["trading_day"] for bar in early_long_five_minute_bars["bars"]} == {
        "2025-01-02"
    }
    unavailable_long_five_minute_bars = client.get(
        f'{_api("/long-five-minute-bars/600036")}&trading_day=2025-01-04'
    ).json()
    assert unavailable_long_five_minute_bars["data_version"] is None
    assert unavailable_long_five_minute_bars["total_count"] == 0
    assert unavailable_long_five_minute_bars["bars"] == []
    analytics = client.get(_api("/backtest-analytics")).json()
    assert analytics["leader"]["experiment_id"].endswith("momentum-vol-slow-20260812-v4")
    assert len(analytics["curve"]) == 632
    preview = client.post(
        _api("/backtest-preview"),
        json={"start": "2025-01-01", "end": "2025-12-31"},
    )
    assert preview.status_code == 200
    assert preview.json()["promotion"] == "NOT_ELIGIBLE"
    assert preview.json()["external_order_transmission"] is False
    assert preview.json()["benchmark"]["strategy_id"] == (
        "equal-weight-buy-hold-benchmark-v1"
    )
    assert preview.json()["benchmark"]["first_decision_known_symbols_only"] is True
    assert "benchmark_return" in preview.json()["metrics"]
    assert "excess_return" in preview.json()["metrics"]
    assert "information_ratio" in preview.json()["metrics"]
    assert all(
        "benchmark_equity" in point and "excess_return" in point
        for point in preview.json()["curve"]
    )
    run_spec_payload = {
        "template_id": "etf-momentum-rotation-v1",
        "experiment_id": "vercel-read-only-draft",
        "git_commit": "0" * 40,
        "split": {
            "train_start": "2022-09-06",
            "train_end": "2024-12-31",
            "validation_start": "2025-01-01",
            "validation_end": "2025-06-30",
            "test_start": "2025-07-01",
            "test_end": "2025-12-31",
        },
        "backtest": {
            "start": "2025-07-01",
            "end": "2025-12-31",
            "factor": "momentum",
            "trend_window": None,
            "volatility_window": None,
            "target_annual_volatility": None,
        },
        "seed": 7,
    }
    run_spec_draft = client.post(
        _api("/experiment-run-spec-draft"), json=run_spec_payload
    )
    assert run_spec_draft.status_code == 200
    assert run_spec_draft.json()["creates_immutable_experiment"] is False
    assert run_spec_draft.json()["gates"]["fresh_unseen_test_evidence"] is False
    assert run_spec_draft.json()["external_order_transmission"] is False
    bound_run = client.post(
        _api("/experiment-run-spec-backtest"), json=run_spec_payload
    )
    assert bound_run.status_code == 200
    assert bound_run.json()["spec_hash"] == run_spec_draft.json()["spec_hash"]
    assert bound_run.json()["gates"]["single_request_spec_result_binding"] is True
    assert bound_run.json()["creates_immutable_experiment"] is False
    assert bound_run.json()["external_order_transmission"] is False
    single_stock = client.post(
        _api("/single-stock-five-minute-backtest"),
        json={
            "symbol": "600036",
            "start": "2026-08-21",
            "end": "2026-08-25",
        },
    )
    assert single_stock.status_code == 200
    assert single_stock.json()["verified_universe_symbol_count"] == 75
    assert single_stock.json()["selected_session_count"] == 3
    assert single_stock.json()["selected_bar_count"] == 144
    assert single_stock.json()["promotion"] == "NOT_ELIGIBLE"
    assert single_stock.json()["state_mutation"] is False
    assert single_stock.json()["external_order_transmission"] is False
    intraday_factor = client.post(
        _api("/intraday-factor-backtest"),
        json={
            "factor": "opening_three_ten_minute_bullish",
            "start": "2026-07-28",
            "end": "2026-08-24",
            "cost_bps": 5,
        },
    )
    assert intraday_factor.status_code == 200
    assert intraday_factor.json()["event_summary"]["signal_count"] == 192
    assert intraday_factor.json()["research_gate"]["passed"] is False
    assert intraday_factor.json()["promotion"] == "NOT_ELIGIBLE"
    assert intraday_factor.json()["paper_readiness"] == "NOT_ELIGIBLE"
    assert intraday_factor.json()["live_readiness"] == (
        "DISABLED_REQUIRES_SEPARATE_AUTHORIZATION"
    )
    assert intraday_factor.json()["state_mutation"] is False
    assert intraday_factor.json()["external_order_transmission"] is False
    correlation = client.post(
        _api("/intraday-factor-correlation"),
        json={
            "start": "2026-07-28",
            "end": "2026-08-24",
            "rules": [
                {
                    "directions": ["BULLISH", "BULLISH", "BULLISH"],
                    "combination": "ALL",
                },
                {
                    "directions": ["BULLISH", "BEARISH", "IGNORE"],
                    "combination": "ANY",
                },
            ],
        },
    )
    assert correlation.status_code == 200
    assert correlation.json()["observation_count"] == 1_500
    assert correlation.json()["state_mutation"] is False
    assert correlation.json()["external_order_transmission"] is False

    blocked = client.post(_api("/system/kill"), json={"reason": "must stay disabled"})
    assert blocked.status_code == 503
    assert client.get(_api("/system")).json()["mode"] == "PAPER"


def test_vercel_entry_caches_only_anonymous_successful_gets() -> None:
    client = TestClient(app)

    public_get = client.get(_api("/market"))
    assert public_get.headers["cache-control"] == "public, max-age=15"
    assert public_get.headers["vercel-cdn-cache-control"] == (
        "public, max-age=60, stale-while-revalidate=300"
    )

    authorized_get = client.get(
        _api("/market"), headers={"Authorization": "Bearer ignored-read-token"}
    )
    assert "cache-control" not in authorized_get.headers
    preview = client.post(
        _api("/backtest-preview"),
        json={"start": "2025-01-01", "end": "2025-12-31"},
    )
    assert "cache-control" not in preview.headers
    blocked = client.post(_api("/system/kill"), json={"reason": "must stay disabled"})
    assert blocked.status_code == 503
    assert "cache-control" not in blocked.headers
