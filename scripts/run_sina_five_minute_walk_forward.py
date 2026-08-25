"""Run an expanding-window 5-minute robustness study on synchronized A-shares.

The study is diagnostic only. It supplements the shorter 1-minute experiment;
it does not claim that 5-minute evidence proves realtime 1-minute readiness.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from decimal import Decimal
from math import sqrt
from pathlib import Path
from typing import Literal

from qtrade.research import MinuteBacktestResult, run_minute_overlay_backtest
from qtrade.runtime import MinuteOverlayPolicy

try:
    from scripts.run_sina_minute_overlay_backtest import (
        _canonical_sha256,
        _load_dataset,
        _metrics,
        _policy,
        _stock_sector_groups,
        _write_json,
    )
except ModuleNotFoundError:
    from run_sina_minute_overlay_backtest import (  # type: ignore[no-redef]
        _canonical_sha256,
        _load_dataset,
        _metrics,
        _policy,
        _stock_sector_groups,
        _write_json,
    )

INITIAL_TRAIN_SESSIONS = 16
TEST_SESSIONS_PER_FOLD = 6
FOLD_COUNT = 4

FIVE_MINUTE_POLICIES: tuple[tuple[str, MinuteOverlayPolicy], ...] = (
    (
        "five-balanced-v1",
        MinuteOverlayPolicy(2, 4, 4, Decimal("0.0055"), Decimal("0.02"), Decimal("0.5"), 2),
    ),
    (
        "five-slow-v1",
        MinuteOverlayPolicy(3, 6, 6, Decimal("0.0055"), Decimal("0.02"), Decimal("0.5"), 3),
    ),
    (
        "five-vol-tight-v1",
        MinuteOverlayPolicy(2, 4, 4, Decimal("0.0035"), Decimal("0.02"), Decimal("0.5"), 2),
    ),
    (
        "five-vol-loose-v1",
        MinuteOverlayPolicy(2, 4, 4, Decimal("0.0075"), Decimal("0.02"), Decimal("0.5"), 2),
    ),
    (
        "five-drawdown-tight-v1",
        MinuteOverlayPolicy(2, 4, 4, Decimal("0.0055"), Decimal("0.01"), Decimal("0.5"), 3),
    ),
    (
        "five-reduction-hard-v1",
        MinuteOverlayPolicy(2, 4, 4, Decimal("0.0055"), Decimal("0.02"), Decimal("0.25"), 2),
    ),
    (
        "five-reduction-soft-v1",
        MinuteOverlayPolicy(2, 4, 4, Decimal("0.0055"), Decimal("0.02"), Decimal("0.75"), 2),
    ),
)


def _walk_forward_folds(trading_days: tuple[date, ...]) -> tuple[dict[str, object], ...]:
    expected = INITIAL_TRAIN_SESSIONS + TEST_SESSIONS_PER_FOLD * FOLD_COUNT
    if len(trading_days) != expected:
        raise ValueError(f"5-minute walk-forward requires exactly {expected} sessions")
    folds = []
    for index in range(FOLD_COUNT):
        test_start = INITIAL_TRAIN_SESSIONS + index * TEST_SESSIONS_PER_FOLD
        test_end = test_start + TEST_SESSIONS_PER_FOLD
        folds.append(
            {
                "fold": index + 1,
                "train_days": trading_days[:test_start],
                "test_days": trading_days[test_start:test_end],
            }
        )
    return tuple(folds)


def _selected_curve(
    result: MinuteBacktestResult,
    *,
    kind: Literal["base", "overlay"],
    days: set[date],
    symbols: set[str] | None = None,
) -> tuple[tuple[datetime, ...], tuple[float, ...]]:
    if symbols is None:
        values = result.base_equity if kind == "base" else result.overlay_equity
    else:
        selected = tuple(symbol for symbol in result.symbols if symbol in symbols)
        if not selected:
            raise ValueError("5-minute curve symbol selection cannot be empty")
        curves = result.base_by_symbol if kind == "base" else result.overlay_by_symbol
        values = tuple(
            sum(curves[symbol][position] for symbol in selected) / len(selected)
            for position in range(len(result.timestamps))
        )
    indexes = [
        position
        for position, trading_day in enumerate(result.trading_days)
        if trading_day in days
    ]
    if not indexes or any(
        right != left + 1 for left, right in zip(indexes, indexes[1:], strict=False)
    ):
        raise ValueError("5-minute fold must be a non-empty contiguous window")
    denominator = 1.0 if indexes[0] == 0 else values[indexes[0] - 1]
    return (
        tuple(result.timestamps[position] for position in indexes),
        tuple(values[position] / denominator for position in indexes),
    )


def _curve_metrics(values: tuple[float, ...]) -> dict[str, object]:
    if not values:
        raise ValueError("5-minute aggregate curve cannot be empty")
    normalized = (1.0, *values)
    peak = normalized[0]
    drawdown = 0.0
    returns = []
    for previous, current in zip(normalized, normalized[1:], strict=False):
        returns.append(current / previous - 1)
        peak = max(peak, current)
        drawdown = min(drawdown, current / peak - 1)
    average = sum(returns) / len(returns)
    variance = sum((value - average) ** 2 for value in returns) / len(returns)
    return {
        "total_return": values[-1] - 1,
        "maximum_drawdown": drawdown,
        "realized_minute_volatility": sqrt(variance),
        "point_count": len(values),
    }


def _stitch_selected_folds(
    folds: tuple[dict[str, object], ...],
    selected_ids: tuple[str, ...],
    results: dict[str, MinuteBacktestResult],
    *,
    kind: Literal["base", "overlay"],
    symbols: set[str] | None = None,
) -> tuple[tuple[datetime, ...], tuple[float, ...]]:
    timestamps: list[datetime] = []
    values: list[float] = []
    wealth = 1.0
    for fold, policy_id in zip(folds, selected_ids, strict=True):
        raw_test_days = fold["test_days"]
        assert isinstance(raw_test_days, tuple)
        fold_timestamps, normalized = _selected_curve(
            results[policy_id],
            kind=kind,
            days=set(raw_test_days),
            symbols=symbols,
        )
        timestamps.extend(fold_timestamps)
        values.extend(wealth * value for value in normalized)
        wealth = values[-1]
    if any(right <= left for left, right in zip(timestamps, timestamps[1:], strict=False)):
        raise ValueError("5-minute stitched fold timestamps are not increasing")
    return tuple(timestamps), tuple(values)


def _aggregate_selected(
    folds: tuple[dict[str, object], ...],
    selected_ids: tuple[str, ...],
    results: dict[str, MinuteBacktestResult],
    *,
    symbols: set[str] | None = None,
) -> tuple[
    dict[str, object],
    dict[str, object],
    tuple[datetime, ...],
    tuple[float, ...],
    tuple[float, ...],
]:
    timestamps, base = _stitch_selected_folds(
        folds, selected_ids, results, kind="base", symbols=symbols
    )
    overlay_timestamps, overlay = _stitch_selected_folds(
        folds, selected_ids, results, kind="overlay", symbols=symbols
    )
    if overlay_timestamps != timestamps:
        raise ValueError("5-minute stitched base and overlay timestamps differ")
    return _curve_metrics(base), _curve_metrics(overlay), timestamps, base, overlay


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset-root", type=Path, default=Path("runtime/data/sina-minute")
    )
    parser.add_argument("--data-version", required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("runtime/research/sina-five-minute-walk-forward-v1")
    )
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite 5-minute report: {args.output}")

    evidence, manifest, bars = _load_dataset(
        args.dataset_root,
        args.data_version,
        expected_classification="noncommercial_recent_historical_5m_non_pit_not_eligible",
        expected_interval="5m",
    )
    trading_days = tuple(date.fromisoformat(value) for value in evidence["trading_days"])
    folds = _walk_forward_folds(trading_days)
    policy_by_id = dict(FIVE_MINUTE_POLICIES)
    five_bps_results = {
        policy_id: run_minute_overlay_backtest(
            bars, policy_id=policy_id, policy=policy, cost_bps=5
        )
        for policy_id, policy in FIVE_MINUTE_POLICIES
    }

    candidate_rows = []
    fold_rows = []
    selected_ids = []
    for fold in folds:
        train_days = set(fold["train_days"])
        test_days = set(fold["test_days"])
        rows = []
        for policy_id, policy in FIVE_MINUTE_POLICIES:
            result = five_bps_results[policy_id]
            metrics = result.metrics(days=train_days)
            base = metrics["base"]
            overlay = metrics["overlay"]
            score = overlay.total_return - base.total_return + 2 * (
                abs(base.maximum_drawdown) - abs(overlay.maximum_drawdown)
            )
            rows.append(
                {
                    "fold": fold["fold"],
                    "policy_id": policy_id,
                    "policy_hash": result.policy_hash,
                    "policy": _policy(policy),
                    "selection_score": score,
                    "train_base": _metrics(base),
                    "train_overlay": _metrics(overlay),
                }
            )
        rows.sort(key=lambda row: (-float(str(row["selection_score"])), str(row["policy_id"])))
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
        candidate_rows.extend(rows)
        selected_id = str(rows[0]["policy_id"])
        selected_ids.append(selected_id)
        test_metrics = five_bps_results[selected_id].metrics(days=test_days)
        fold_rows.append(
            {
                "fold": fold["fold"],
                "train_days": [value.isoformat() for value in fold["train_days"]],
                "test_days": [value.isoformat() for value in fold["test_days"]],
                "selected_policy_id": selected_id,
                "selected_policy_hash": five_bps_results[selected_id].policy_hash,
                "test_base": _metrics(test_metrics["base"]),
                "test_overlay": _metrics(test_metrics["overlay"]),
                "test_excess_return": (
                    test_metrics["overlay"].total_return
                    - test_metrics["base"].total_return
                ),
            }
        )
    selected_sequence = tuple(selected_ids)

    result_cache: dict[tuple[str, float], MinuteBacktestResult] = {
        (policy_id, 5.0): result for policy_id, result in five_bps_results.items()
    }

    def results_at(cost_bps: float) -> dict[str, MinuteBacktestResult]:
        selected_results = {}
        for policy_id in set(selected_sequence):
            key = (policy_id, cost_bps)
            if key not in result_cache:
                result_cache[key] = run_minute_overlay_backtest(
                    bars,
                    policy_id=policy_id,
                    policy=policy_by_id[policy_id],
                    cost_bps=cost_bps,
                )
            selected_results[policy_id] = result_cache[key]
        return selected_results

    cost_rows = []
    for cost_bps in (0.0, 5.0, 10.0, 20.0):
        base_metrics, overlay_metrics, _, _, _ = _aggregate_selected(
            folds, selected_sequence, results_at(cost_bps)
        )
        cost_rows.append(
            {
                "cost_bps": cost_bps,
                "base": base_metrics,
                "overlay": overlay_metrics,
                "excess_return": (
                    float(overlay_metrics["total_return"])
                    - float(base_metrics["total_return"])
                ),
            }
        )

    requested_etfs = set(evidence["universe"]["etfs"])
    all_symbols = set(next(iter(five_bps_results.values())).symbols)
    etfs = all_symbols & requested_etfs
    stocks = all_symbols - etfs
    subgroup_rows = []
    for name, subset in (("ETF", etfs), ("STOCK", stocks)):
        base_metrics, overlay_metrics, _, _, _ = _aggregate_selected(
            folds, selected_sequence, five_bps_results, symbols=subset
        )
        subgroup_rows.append(
            {
                "group": name,
                "symbol_count": len(subset),
                "base": base_metrics,
                "overlay": overlay_metrics,
            }
        )

    aggregate_base, aggregate_overlay, timestamps, base_curve, overlay_curve = (
        _aggregate_selected(folds, selected_sequence, five_bps_results)
    )
    sector_rows = []
    for sector_code, sector_name, excluded_symbols in _stock_sector_groups(
        evidence, all_symbols
    ):
        retained = all_symbols - set(excluded_symbols)
        _, overlay_metrics, _, _, _ = _aggregate_selected(
            folds, selected_sequence, five_bps_results, symbols=retained
        )
        sector_rows.append(
            {
                "excluded_sector_code": sector_code,
                "excluded_sector_name": sector_name,
                "excluded_symbols": list(excluded_symbols),
                "excluded_symbol_count": len(excluded_symbols),
                "retained_symbol_count": len(retained),
                "overlay": overlay_metrics,
                "return_delta_vs_all": (
                    float(overlay_metrics["total_return"])
                    - float(aggregate_overlay["total_return"])
                ),
            }
        )

    daily_rows = []
    for fold, policy_id in zip(folds, selected_sequence, strict=True):
        result = five_bps_results[policy_id]
        for trading_day in fold["test_days"]:
            daily_metrics = result.metrics(days={trading_day})
            daily_rows.append(
                {
                    "fold": fold["fold"],
                    "trading_day": trading_day.isoformat(),
                    "selected_policy_id": policy_id,
                    "base": _metrics(daily_metrics["base"]),
                    "overlay": _metrics(daily_metrics["overlay"]),
                }
            )

    args.output.mkdir(parents=True)
    artifact_hashes = {
        "candidates.json": _write_json(args.output / "candidates.json", candidate_rows),
        "folds.json": _write_json(args.output / "folds.json", fold_rows),
        "cost_stress.json": _write_json(args.output / "cost_stress.json", cost_rows),
        "subgroups.json": _write_json(args.output / "subgroups.json", subgroup_rows),
        "leave_one_sector_out.json": _write_json(
            args.output / "leave_one_sector_out.json", sector_rows
        ),
        "daily.json": _write_json(args.output / "daily.json", daily_rows),
        "curve.json": _write_json(
            args.output / "curve.json",
            [
                {
                    "timestamp": timestamp.isoformat(),
                    "base_equity": base,
                    "overlay_equity": overlay,
                }
                for timestamp, base, overlay in zip(
                    timestamps, base_curve, overlay_curve, strict=True
                )
            ],
        ),
    }
    report = {
        "schema_version": 1,
        "report_id": "sina-five-minute-expanding-walk-forward-v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "dataset": {
            "data_version": manifest.data_version,
            "checksum": manifest.checksum,
            "evidence_sha256": _canonical_sha256(evidence),
            "bar_interval": evidence["bar_interval"],
            "bar_count": evidence["bar_count"],
            "symbol_count": evidence["symbol_count"],
            "etf_count": evidence["etf_count"],
            "stock_count": evidence["stock_count"],
            "sector_count": evidence["sector_count"],
            "session_count": evidence["session_count"],
            "classification": evidence["classification"],
        },
        "causal_contract": {
            "signal_observed_at": "FIVE_MINUTE_BAR_CLOSE_T",
            "exposure_change_at": "NEXT_FIVE_MINUTE_BAR_OPEN",
            "same_bar_signal_execution": False,
            "candidate_state": "ALL_CANDIDATES_CAUSALLY_SHADOW_WARMED",
            "maximum_exposure": max(
                result.maximum_exposure for result in five_bps_results.values()
            ),
            "external_order_transmission": False,
        },
        "walk_forward": {
            "initial_train_sessions": INITIAL_TRAIN_SESSIONS,
            "test_sessions_per_fold": TEST_SESSIONS_PER_FOLD,
            "fold_count": FOLD_COUNT,
            "candidate_count": len(FIVE_MINUTE_POLICIES),
            "test_windows_disjoint": True,
            "selected_policy_sequence": list(selected_sequence),
            "selection_objective": (
                "overlay_return_minus_base_return_plus_twice_drawdown_improvement"
            ),
        },
        "aggregate": {
            "cost_bps": 5,
            "base": aggregate_base,
            "overlay": aggregate_overlay,
            "excess_return": (
                float(aggregate_overlay["total_return"])
                - float(aggregate_base["total_return"])
            ),
            "positive_test_fold_count": sum(
                float(row["test_overlay"]["total_return"]) > 0 for row in fold_rows
            ),
        },
        "robustness": {
            "cost_scenarios": len(cost_rows),
            "subgroups": len(subgroup_rows),
            "leave_one_sector_out_scenarios": len(sector_rows),
            "test_daily_partitions": len(daily_rows),
        },
        "artifacts": artifact_hashes,
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": [
            "FIVE_MINUTE_INTERVAL_DOES_NOT_PROVE_ONE_MINUTE_READINESS",
            "ONLY_FORTY_RECENT_SESSIONS",
            "CURRENT_NON_POINT_IN_TIME_UNIVERSE",
            "PUBLIC_WEB_SOURCE_NOT_AUTHORIZED_REALTIME_FEED",
            "NO_PAPER_OR_SHADOW_SESSION_EVIDENCE",
        ],
        "realtime_readiness": False,
        "real_data_paper_readiness": False,
        "external_order_transmission": False,
    }
    signed_report = {**report, "evidence_sha256": _canonical_sha256(report)}
    _write_json(args.output / "report.json", signed_report)
    print(json.dumps(signed_report, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
