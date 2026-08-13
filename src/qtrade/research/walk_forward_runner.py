from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256

from qtrade.data import Bar
from qtrade.research.experiment import TimeSplit
from qtrade.research.metrics import PerformanceMetrics, evaluate_returns
from qtrade.research.multi_asset import (
    AllocationModel,
    DailyMultiAssetBacktester,
    MultiAssetBacktestResult,
    evaluate_backtest_window,
)


@dataclass(frozen=True, slots=True)
class WalkForwardCandidate:
    candidate_id: str
    model_factory: Callable[[], AllocationModel]
    parameters: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("walk-forward candidate identity is required")
        keys = [key for key, _ in self.parameters]
        if any(not key for key in keys) or len(keys) != len(set(keys)):
            raise ValueError("walk-forward candidate parameters must have unique keys")


@dataclass(frozen=True, slots=True)
class WalkForwardFoldResult:
    fold: int
    split: TimeSplit
    selected_candidate_id: str
    validation_sharpe: Decimal
    validation_scores: tuple[tuple[str, Decimal], ...]
    test_metrics: PerformanceMetrics
    test_turnover: Decimal
    test_transaction_cost: Decimal


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    folds: tuple[WalkForwardFoldResult, ...]
    aggregate_metrics: PerformanceMetrics
    fold_pass_ratio: Decimal
    result_hash: str


def _window_metrics(
    result: MultiAssetBacktestResult,
    start: date,
    end: date,
) -> PerformanceMetrics:
    return evaluate_backtest_window(result, start, end)


class MultiAssetWalkForwardRunner:
    def __init__(
        self,
        candidates: tuple[WalkForwardCandidate, ...],
        *,
        backtester_factory: Callable[[], DailyMultiAssetBacktester],
    ) -> None:
        if not candidates or len({value.candidate_id for value in candidates}) != len(
            candidates
        ):
            raise ValueError("walk-forward candidates must be nonempty and unique")
        self._candidates = candidates
        self._backtester_factory = backtester_factory

    def run(
        self,
        bars: list[Bar],
        splits: tuple[TimeSplit, ...],
    ) -> WalkForwardResult:
        if not splits:
            raise ValueError("walk-forward runner requires at least one split")
        for previous, current in zip(splits[:-1], splits[1:], strict=True):
            if current.train_start <= previous.test_end:
                raise ValueError(
                    "walk-forward folds must be disjoint so held-out tests are never "
                    "reused for selection"
                )
        folds: list[WalkForwardFoldResult] = []
        aggregate_returns: list[Decimal] = []
        aggregate_exposures: list[Decimal] = []
        total_turnover = Decimal("0")
        total_cost = Decimal("0")
        for fold, split in enumerate(splits, start=1):
            selection_bars = [
                bar
                for bar in bars
                if split.train_start <= bar.trading_day <= split.validation_end
            ]
            evaluated: list[tuple[Decimal, str, WalkForwardCandidate]] = []
            for candidate in self._candidates:
                result = self._backtester_factory().run(
                    selection_bars, candidate.model_factory()
                )
                validation = _window_metrics(
                    result, split.validation_start, split.validation_end
                )
                evaluated.append((validation.sharpe, candidate.candidate_id, candidate))
            validation_sharpe, candidate_id, selected_candidate = max(
                evaluated, key=lambda value: (value[0], value[1])
            )
            fold_bars = [
                bar
                for bar in bars
                if split.train_start <= bar.trading_day <= split.test_end
            ]
            selected = self._backtester_factory().run(
                fold_bars, selected_candidate.model_factory()
            )
            test = _window_metrics(selected, split.test_start, split.test_end)
            indices = [
                index
                for index, session in enumerate(selected.sessions)
                if split.test_start <= session <= split.test_end
            ]
            aggregate_returns.extend(selected.returns[index] for index in indices)
            aggregate_exposures.extend(selected.exposure_curve[index] for index in indices)
            total_turnover += test.turnover
            total_cost += test.transaction_costs
            folds.append(
                WalkForwardFoldResult(
                    fold,
                    split,
                    candidate_id,
                    validation_sharpe,
                    tuple(sorted((value[1], value[0]) for value in evaluated)),
                    test,
                    test.turnover,
                    test.transaction_costs,
                )
            )
        exposure = sum(aggregate_exposures, start=Decimal("0")) / len(
            aggregate_exposures
        )
        aggregate = evaluate_returns(
            aggregate_returns,
            exposure=exposure,
            turnover=total_turnover,
            trade_count=sum(fold.test_metrics.number_of_trades for fold in folds),
            transaction_costs=total_cost,
        )
        pass_ratio = Decimal(
            sum(fold.test_metrics.total_return > 0 for fold in folds)
        ) / len(folds)
        payload = {
            "candidates": [
                {
                    "candidate_id": candidate.candidate_id,
                    "parameters": candidate.parameters,
                }
                for candidate in self._candidates
            ],
            "folds": [asdict(fold) for fold in folds],
            "aggregate": asdict(aggregate),
            "fold_pass_ratio": pass_ratio,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return WalkForwardResult(
            tuple(folds),
            aggregate,
            pass_ratio,
            sha256(canonical.encode("utf-8")).hexdigest(),
        )
