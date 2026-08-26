from urllib.parse import quote

from fastapi.testclient import TestClient

from qtrade.api.vercel import app


def _api(path: str) -> str:
    return f"/api?route={quote(path, safe='')}"


def test_vercel_entry_is_read_only_and_loads_packaged_evidence() -> None:
    client = TestClient(app)

    assert client.get(_api("/health")).status_code == 200
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
