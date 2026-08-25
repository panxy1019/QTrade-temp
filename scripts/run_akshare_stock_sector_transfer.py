"""Stress-test the locked ETF reversal factor across sectors and A-shares."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from subprocess import check_output
from typing import Any

from qtrade.data import DataManifest, ParquetBarStore, QualityStatus
from qtrade.research import (
    DailyMultiAssetBacktester,
    EqualWeightBuyHold,
    PriceFactorSpec,
    RankedFactorAllocation,
    evaluate_backtest_window,
    stationary_bootstrap_total_returns,
)

_SELECTION_START = date(2019, 1, 1)
_SELECTION_END = date(2025, 12, 31)
_AUDIT_START = date(2026, 1, 1)
_AUDIT_END = date(2026, 8, 21)


def _load_evidence(path: Path) -> tuple[dict[str, Any], DataManifest]:
    signed: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    digest = signed.pop("evidence_sha256", None)
    canonical = json.dumps(
        signed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    if not isinstance(digest, str) or sha256(canonical.encode()).hexdigest() != digest:
        raise ValueError("AKShare stock history evidence hash mismatch")
    if signed.get("adjustment") != "hfq" or signed.get("point_in_time") is not False:
        raise ValueError("stock transfer requires signed non-PIT HFQ evidence")
    universe = signed.get("universe")
    coverage = signed.get("coverage")
    if not isinstance(universe, dict) or not isinstance(coverage, dict):
        raise ValueError("stock history evidence is incomplete")
    symbols = universe.get("symbols")
    stocks = universe.get("stocks")
    if not isinstance(symbols, list) or len(symbols) != 75 or len(set(symbols)) != 75:
        raise ValueError("stock transfer requires exactly 75 unique symbols")
    if not isinstance(stocks, list) or len(stocks) != 75:
        raise ValueError("stock transfer sector metadata is incomplete")
    counts = Counter(str(item.get("sector_code", "")) for item in stocks)
    if len(counts) != 15 or set(counts.values()) != {5}:
        raise ValueError("stock transfer requires 15 sectors with 5 stocks each")
    if set(coverage) != set(symbols) or any(
        not isinstance(coverage[symbol], dict)
        or int(coverage[symbol].get("bar_count", 0)) < 750
        for symbol in symbols
    ):
        raise ValueError("stock transfer coverage gate is not satisfied")
    value = signed.get("manifest")
    if not isinstance(value, dict):
        raise ValueError("stock history manifest is missing")
    manifest = DataManifest(
        value["data_version"],
        value["source"],
        value["schema_version"],
        datetime.fromisoformat(value["ingestion_time"]),
        value["checksum"],
        QualityStatus(value["quality_status"]),
    )
    if manifest.quality_status is not QualityStatus.PASSED:
        raise ValueError("stock transfer requires a PASSED dataset")
    return signed, manifest


def _model() -> RankedFactorAllocation:
    return RankedFactorAllocation(
        ((PriceFactorSpec("reversal", 10), Decimal("1")),),
        top_k=3,
        rebalance_every=20,
        reserve=Decimal("0.05"),
    )


def _backtester(cost_multiplier: Decimal = Decimal("1")) -> DailyMultiAssetBacktester:
    return DailyMultiAssetBacktester(
        transaction_cost_rate=Decimal("0.001") * cost_multiplier,
        slippage_rate=Decimal("0.0005") * cost_multiplier,
        maximum_stale_sessions=40,
    )


def _evaluate_transfer_gate(
    *,
    yearly_returns: tuple[Decimal, ...],
    sector_returns: tuple[Decimal, ...],
    leave_one_sector_out_returns: tuple[Decimal, ...],
    doubled_cost_return: Decimal,
    bootstrap_probability_positive: Decimal,
    maximum_drawdown: Decimal,
) -> dict[str, object]:
    if len(yearly_returns) != 7:
        raise ValueError("transfer gate requires seven yearly returns")
    if len(sector_returns) != 15 or len(leave_one_sector_out_returns) != 15:
        raise ValueError("transfer gate requires fifteen sector stress returns")
    criteria = {
        "positive_years_at_least_5_of_7": sum(value > 0 for value in yearly_returns) >= 5,
        "positive_sector_only_at_least_9_of_15": (
            sum(value > 0 for value in sector_returns) >= 9
        ),
        "positive_leave_one_sector_out_at_least_12_of_15": (
            sum(value > 0 for value in leave_one_sector_out_returns) >= 12
        ),
        "doubled_cost_total_return_above_zero": doubled_cost_return > 0,
        "bootstrap_probability_positive_at_least_0.75": (
            bootstrap_probability_positive >= Decimal("0.75")
        ),
        "maximum_drawdown_at_least_minus_0.35": (
            maximum_drawdown >= Decimal("-0.35")
        ),
    }
    return {"passed": all(criteria.values()), "criteria": criteria}


def _source_state() -> dict[str, object]:
    head = check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    names = check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    ).split(b"\0")
    digest = sha256()
    for raw_name in sorted(value for value in names if value):
        path = Path(raw_name.decode("utf-8"))
        if not path.is_file():
            continue
        digest.update(raw_name)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    status = check_output(["git", "status", "--porcelain"], text=True).splitlines()
    return {
        "git_head": head,
        "source_tree_sha256": digest.hexdigest(),
        "source_tree_dirty": bool(status),
        "dirty_entry_count": len(status),
    }


def _write_json(path: Path, value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2, default=str
    ).encode("utf-8")
    path.write_bytes(encoded)
    return sha256(encoded).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset-root", type=Path, default=Path("runtime/data/akshare-stocks")
    )
    parser.add_argument(
        "--data-version",
        default="akshare-csi300-sw15x5-hfq-20180101-20260821-v1",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runtime/research/akshare-stock-sector-transfer-v1"),
    )
    args = parser.parse_args()

    evidence, manifest = _load_evidence(
        args.dataset_root / args.data_version / "manifest.json"
    )
    bars = ParquetBarStore(args.dataset_root).read(manifest)
    universe = evidence["universe"]
    symbols = tuple(universe["symbols"])
    sector_symbols = {
        str(sector["sector_code"]): tuple(
            stock["symbol"]
            for stock in universe["stocks"]
            if stock["sector_code"] == sector["sector_code"]
        )
        for sector in universe["sectors"]
    }
    sector_names = {
        str(sector["sector_code"]): str(sector["sector_name"])
        for sector in universe["sectors"]
    }
    formal_bars = [bar for bar in bars if bar.trading_day <= _SELECTION_END]
    base_result = _backtester().run(formal_bars, _model())
    full_metrics = evaluate_backtest_window(
        base_result, _SELECTION_START, _SELECTION_END
    )
    yearly: list[dict[str, object]] = []
    yearly_returns: list[Decimal] = []
    for year in range(2019, 2026):
        metrics = evaluate_backtest_window(
            base_result, date(year, 1, 1), date(year, 12, 31)
        )
        yearly_returns.append(metrics.total_return)
        yearly.append({"year": year, "metrics": asdict(metrics)})

    sector_only: list[dict[str, object]] = []
    sector_returns: list[Decimal] = []
    leave_one_sector_out: list[dict[str, object]] = []
    leave_one_sector_out_returns: list[Decimal] = []
    for sector_code, members in sector_symbols.items():
        sector_result = _backtester().run(
            [bar for bar in formal_bars if bar.symbol in members], _model()
        )
        sector_metrics = evaluate_backtest_window(
            sector_result, _SELECTION_START, _SELECTION_END
        )
        sector_returns.append(sector_metrics.total_return)
        sector_only.append(
            {
                "sector_code": sector_code,
                "sector_name": sector_names[sector_code],
                "symbols": members,
                "metrics": asdict(sector_metrics),
            }
        )
        retained = set(symbols) - set(members)
        leave_result = _backtester().run(
            [bar for bar in formal_bars if bar.symbol in retained], _model()
        )
        leave_metrics = evaluate_backtest_window(
            leave_result, _SELECTION_START, _SELECTION_END
        )
        leave_one_sector_out_returns.append(leave_metrics.total_return)
        leave_one_sector_out.append(
            {
                "excluded_sector_code": sector_code,
                "excluded_sector_name": sector_names[sector_code],
                "retained_symbol_count": len(retained),
                "metrics": asdict(leave_metrics),
            }
        )

    doubled_result = _backtester(Decimal("2")).run(formal_bars, _model())
    doubled_metrics = evaluate_backtest_window(
        doubled_result, _SELECTION_START, _SELECTION_END
    )
    selection_returns = tuple(
        base_result.returns[index]
        for index, session in enumerate(base_result.sessions)
        if _SELECTION_START <= session <= _SELECTION_END
    )
    bootstrap = stationary_bootstrap_total_returns(
        selection_returns,
        replications=1000,
        expected_block_length=20,
        seed=20260825,
    )
    gate = _evaluate_transfer_gate(
        yearly_returns=tuple(yearly_returns),
        sector_returns=tuple(sector_returns),
        leave_one_sector_out_returns=tuple(leave_one_sector_out_returns),
        doubled_cost_return=doubled_metrics.total_return,
        bootstrap_probability_positive=bootstrap.probability_positive,
        maximum_drawdown=full_metrics.maximum_drawdown,
    )

    audit_bars = [bar for bar in bars if bar.trading_day <= _AUDIT_END]
    audit_result = _backtester().run(audit_bars, _model())
    audit_metrics = evaluate_backtest_window(audit_result, _AUDIT_START, _AUDIT_END)
    benchmark_bars = [
        bar
        for bar in bars
        if date(2025, 12, 1) <= bar.trading_day <= _AUDIT_END
    ]
    benchmark_result = _backtester().run(
        benchmark_bars, EqualWeightBuyHold(symbols, reserve=Decimal("0.05"))
    )
    benchmark_metrics = evaluate_backtest_window(
        benchmark_result, _AUDIT_START, _AUDIT_END
    )

    args.output_dir.mkdir(parents=True, exist_ok=False)
    artifact_hashes = {
        "yearly.json": _write_json(args.output_dir / "yearly.json", yearly),
        "sector_only.json": _write_json(
            args.output_dir / "sector_only.json", sector_only
        ),
        "leave_one_sector_out.json": _write_json(
            args.output_dir / "leave_one_sector_out.json", leave_one_sector_out
        ),
        "cost_stress.json": _write_json(
            args.output_dir / "cost_stress.json",
            {
                "base": asdict(full_metrics),
                "double": asdict(doubled_metrics),
            },
        ),
        "bootstrap.json": _write_json(
            args.output_dir / "bootstrap.json", asdict(bootstrap)
        ),
        "polluted_audit_2026.json": _write_json(
            args.output_dir / "polluted_audit_2026.json",
            {
                "classification": "POLLUTED_AUDIT_ONLY",
                "excluded_from_gate": True,
                "strategy_metrics": asdict(audit_metrics),
                "equal_weight_metrics": asdict(benchmark_metrics),
                "terminal_excess_return": (
                    audit_metrics.total_return - benchmark_metrics.total_return
                ),
            },
        ),
    }
    positive_year_count = sum(value > 0 for value in yearly_returns)
    positive_sector_only_count = sum(value > 0 for value in sector_returns)
    positive_leave_one_sector_out_count = sum(
        value > 0 for value in leave_one_sector_out_returns
    )
    report = {
        "classification": "locked_etf_factor_stock_sector_transfer_non_pit",
        "data": {
            "data_version": manifest.data_version,
            "checksum": manifest.checksum,
            "bar_count": len(bars),
            "observed_start": min(bar.trading_day for bar in bars),
            "observed_end": max(bar.trading_day for bar in bars),
            "symbol_count": len(symbols),
            "sector_count": len(sector_symbols),
            "stocks_per_sector": 5,
            "adjustment": evidence["adjustment"],
            "point_in_time_universe": False,
        },
        "source_state": _source_state(),
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
            "start": _SELECTION_START,
            "end": _SELECTION_END,
            "full_metrics": asdict(full_metrics),
            "positive_year_count": positive_year_count,
            "positive_sector_only_count": positive_sector_only_count,
            "positive_leave_one_sector_out_count": (
                positive_leave_one_sector_out_count
            ),
            "doubled_cost_metrics": asdict(doubled_metrics),
            "bootstrap": asdict(bootstrap),
        },
        "transfer_gate": gate,
        "polluted_2026_audit": {
            "start": _AUDIT_START,
            "end": _AUDIT_END,
            "excluded_from_gate": True,
            "strategy_metrics": asdict(audit_metrics),
            "equal_weight_metrics": asdict(benchmark_metrics),
        },
        "execution_contract": {
            "timing": "close_decision_next_observed_open",
            "base_transaction_cost_rate": "0.001",
            "base_slippage_rate": "0.0005",
            "maximum_stale_sessions": 40,
            "stale_policy_revision": {
                "made_before_strategy_outcomes": True,
                "observed_maximum_missing_market_sessions": 39,
                "reason": (
                    "Initial 20-session policy rejected a legal historical "
                    "suspension before any result artifact was produced"
                ),
            },
            "lot_size": 100,
            "long_only": True,
            "limit_up_down_execution_model": False,
        },
        "artifacts": artifact_hashes,
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": [
            *( [] if gate["passed"] else ["PREDECLARED_TRANSFER_GATE_FAILED"] ),
            "CURRENT_2026_CONSTITUENTS_PROJECTED_BACKWARD",
            "HFQ_SERIES_NOT_POINT_IN_TIME",
            "SINGLE_PUBLIC_DATA_SOURCE",
            "NO_LIMIT_UP_DOWN_EXECUTION_MODEL",
            "LAST_CLOSE_VALUATION_ALLOWED_FOR_UP_TO_40_MISSING_SESSIONS",
            "NO_PAPER_OR_SHADOW_SESSIONS",
        ],
    }
    canonical = json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    envelope = {"sha256": sha256(canonical.encode()).hexdigest(), "report": report}
    _write_json(args.output_dir / "report.json", envelope)
    print(
        json.dumps(
            {
                "report_sha256": envelope["sha256"],
                "transfer_gate": gate,
                "full_total_return": full_metrics.total_return,
                "maximum_drawdown": full_metrics.maximum_drawdown,
                "positive_year_count": positive_year_count,
                "positive_sector_only_count": positive_sector_only_count,
                "positive_leave_one_sector_out_count": (
                    positive_leave_one_sector_out_count
                ),
                "bootstrap_probability_positive": bootstrap.probability_positive,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
