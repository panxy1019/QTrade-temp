import json
from hashlib import sha256

from fastapi.testclient import TestClient

from qtrade.api.app import (
    ApiAuthConfig,
    _load_verified_stock_sector_transfer_report,
    bootstrap_context,
    create_app,
)


def _metric(total_return="0.1", maximum_drawdown="-0.1"):
    return {
        "total_return": total_return,
        "sharpe": "0.5",
        "maximum_drawdown": maximum_drawdown,
    }


def _write_report(root):
    yearly = [
        {"year": year, "metrics": _metric()}
        for year in range(2019, 2026)
    ]
    sector_only = [
        {
            "sector_code": f"sector-{sector:02d}",
            "sector_name": f"板块{sector:02d}",
            "symbols": [f"{sector:02d}{offset:04d}" for offset in range(5)],
            "metrics": _metric(),
        }
        for sector in range(15)
    ]
    leave_one = [
        {
            "excluded_sector_code": f"sector-{sector:02d}",
            "excluded_sector_name": f"板块{sector:02d}",
            "retained_symbol_count": 70,
            "metrics": _metric(),
        }
        for sector in range(15)
    ]
    cost = {"base": _metric(), "double": _metric("0.05")}
    bootstrap = {
        "method": "stationary_bootstrap",
        "replications": 1000,
        "expected_block_length": 20,
        "seed": 20260825,
        "observed_total_return": "0.1",
        "p05_total_return": "-0.1",
        "median_total_return": "0.1",
        "p95_total_return": "0.4",
        "probability_positive": "0.8",
    }
    audit = {
        "classification": "POLLUTED_AUDIT_ONLY",
        "excluded_from_gate": True,
        "strategy_metrics": _metric("0.02"),
        "equal_weight_metrics": _metric("0.01"),
        "terminal_excess_return": "0.01",
    }
    payloads = {
        "yearly.json": yearly,
        "sector_only.json": sector_only,
        "leave_one_sector_out.json": leave_one,
        "cost_stress.json": cost,
        "bootstrap.json": bootstrap,
        "polluted_audit_2026.json": audit,
    }
    artifact_hashes = {}
    for filename, payload in payloads.items():
        encoded = json.dumps(payload, sort_keys=True, indent=2).encode()
        (root / filename).write_bytes(encoded)
        artifact_hashes[filename] = sha256(encoded).hexdigest()
    criteria = {
        "positive_years_at_least_5_of_7": True,
        "positive_sector_only_at_least_9_of_15": True,
        "positive_leave_one_sector_out_at_least_12_of_15": True,
        "doubled_cost_total_return_above_zero": True,
        "bootstrap_probability_positive_at_least_0.75": True,
        "maximum_drawdown_at_least_minus_0.35": True,
    }
    report = {
        "classification": "locked_etf_factor_stock_sector_transfer_non_pit",
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": ["CURRENT_2026_CONSTITUENTS_PROJECTED_BACKWARD"],
        "data": {
            "symbol_count": 75,
            "sector_count": 15,
            "stocks_per_sector": 5,
            "adjustment": "hfq",
            "point_in_time_universe": False,
        },
        "locked_strategy": {
            "candidate_id": "reversal-10-top3-20",
            "factor": "reversal",
            "lookback_sessions": 10,
            "top_k": 3,
            "rebalance_every_sessions": 20,
            "reserve": "0.05",
            "chosen_before_stock_history_outcomes": True,
            "parameter_tuning_on_stock_results": False,
        },
        "formal_period": {
            "start": "2019-01-01",
            "end": "2025-12-31",
            "full_metrics": cost["base"],
            "positive_year_count": 7,
            "positive_sector_only_count": 15,
            "positive_leave_one_sector_out_count": 15,
            "doubled_cost_metrics": cost["double"],
            "bootstrap": bootstrap,
        },
        "transfer_gate": {"passed": True, "criteria": criteria},
        "polluted_2026_audit": {
            "excluded_from_gate": True,
            "strategy_metrics": audit["strategy_metrics"],
            "equal_weight_metrics": audit["equal_weight_metrics"],
        },
        "execution_contract": {
            "maximum_stale_sessions": 40,
            "limit_up_down_execution_model": False,
        },
        "artifacts": artifact_hashes,
    }
    canonical = json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    target = root / "report.json"
    target.write_text(
        json.dumps({"sha256": sha256(canonical.encode()).hexdigest(), "report": report}),
        encoding="utf-8",
    )
    return target, report, yearly


def test_stock_sector_transfer_verifier_recomputes_gate_and_rejects_tampering(
    tmp_path,
):
    target, report, yearly = _write_report(tmp_path)
    assert _load_verified_stock_sector_transfer_report(target) is not None
    context = bootstrap_context()
    context.stock_sector_transfer_report_path = target
    api = TestClient(create_app(context, ApiAuthConfig()))
    response = api.get("/stock-sector-transfer").json()
    assert response["transfer_gate"]["passed"] is True
    assert len(response["sector_only"]) == 15

    yearly[0]["metrics"]["total_return"] = "-0.1"
    encoded = json.dumps(yearly, sort_keys=True, indent=2).encode()
    (tmp_path / "yearly.json").write_bytes(encoded)
    report["artifacts"]["yearly.json"] = sha256(encoded).hexdigest()
    canonical = json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    target.write_text(
        json.dumps({"sha256": sha256(canonical.encode()).hexdigest(), "report": report}),
        encoding="utf-8",
    )
    assert _load_verified_stock_sector_transfer_report(target) is None
    assert api.get("/stock-sector-transfer").json()["classification"] == (
        "NO_VERIFIED_STOCK_SECTOR_TRANSFER_RESULT"
    )
