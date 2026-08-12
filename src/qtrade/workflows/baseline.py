from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from qtrade.backtest import BacktestEngine
from qtrade.data import Bar, DataManifest, ParquetBarStore
from qtrade.domain import Instrument
from qtrade.execution import FeeSchedule
from qtrade.instruments import TradingRule
from qtrade.persistence import ExperimentCatalogEntry, SQLiteMetadataCatalog
from qtrade.research import (
    ExperimentArtifactStore,
    ExperimentRecord,
    TimeSplit,
    evaluate_returns,
)
from qtrade.risk import RiskLimits
from qtrade.strategies import BuyAndHoldStrategy


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    data_version: str
    experiment_id: str
    final_cash: Decimal
    final_position: int
    report_path: Path


class BaselineWorkflow:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()

    def run(
        self,
        *,
        bars: list[Bar],
        manifest: DataManifest,
        instrument: Instrument,
        git_commit: str,
        experiment_id: str,
    ) -> WorkflowResult:
        stored_manifest = ParquetBarStore(self._workspace / "datasets").write(bars, manifest)
        catalog = SQLiteMetadataCatalog(self._workspace / "metadata.db")
        catalog.register_dataset(stored_manifest)
        verified_bars = ParquetBarStore(self._workspace / "datasets").read(
            stored_manifest, symbol=instrument.symbol
        )
        engine = BacktestEngine(
            instrument=instrument,
            opening_cash=Decimal("100000"),
            fees=FeeSchedule("workflow-v1", Decimal("0.0003"), Decimal("0.0005")),
            limits=RiskLimits(Decimal("100000"), 100000, Decimal("100000")),
            trading_rule=TradingRule(
                "workflow-rule",
                instrument.exchange,
                "WORKFLOW_FIXTURE",
                date(2000, 1, 1),
                None,
                instrument.lot_size,
                instrument.lot_size,
                instrument.price_tick,
                None,
                1,
                "fixture://workflow-rule",
                "1",
            ),
        )
        result = engine.run(verified_bars, BuyAndHoldStrategy(100), manifest=stored_manifest)
        returns = [
            verified_bars[index].close / verified_bars[index - 1].close - Decimal("1")
            for index in range(1, len(verified_bars))
        ]
        metrics = evaluate_returns(returns or [Decimal("0")])
        split = self._chronological_split(verified_bars)
        record = ExperimentRecord(
            experiment_id=experiment_id,
            git_commit=git_commit,
            data_version=stored_manifest.data_version,
            universe=(instrument.symbol,),
            factor_set=("buy_and_hold",),
            label="next_bar_return",
            split=split,
            model="BuyAndHoldStrategy",
            hyperparameters={"quantity": 100},
            seed=0,
            transaction_cost=result.total_fees,
            metrics={
                "total_return": metrics.total_return,
                "maximum_drawdown": metrics.maximum_drawdown,
            },
            artifacts=("experiment.json",),
        )
        path, digest = ExperimentArtifactStore(self._workspace / "experiments").save(record)
        catalog.register_experiment(
            ExperimentCatalogEntry(
                experiment_id,
                digest,
                git_commit,
                stored_manifest.data_version,
                str(path.relative_to(self._workspace)),
            )
        )
        return WorkflowResult(
            stored_manifest.data_version,
            experiment_id,
            result.final_cash,
            result.final_position,
            path,
        )

    @staticmethod
    def _chronological_split(bars: list[Bar]) -> TimeSplit:
        if len(bars) < 6:
            raise ValueError("baseline workflow requires at least six bars")
        days = [bar.trading_day for bar in bars]
        first_cut = len(days) // 2
        second_cut = first_cut + (len(days) - first_cut) // 2
        return TimeSplit(
            train_start=days[0],
            train_end=days[first_cut - 1],
            validation_start=days[first_cut],
            validation_end=days[second_cut - 1],
            test_start=days[second_cut],
            test_end=days[-1],
        )
