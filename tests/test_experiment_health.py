from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from qtrade.data import DataManifest, QualityStatus
from qtrade.research import (
    ExperimentOutput,
    ExperimentRunner,
    ExperimentRunSpec,
    TimeSplit,
)
from qtrade.strategies import (
    StrategyHealthInput,
    StrategyHealthMonitor,
    StrategyHealthStatus,
)


def run_spec(experiment_id: str = "exp-1") -> ExperimentRunSpec:
    return ExperimentRunSpec(
        experiment_id=experiment_id,
        strategy_id="ETF_MOM_V1",
        git_commit="d68cbb6",
        data_version="etf-v1",
        universe=("510300", "510500"),
        factor_set=("momentum_20",),
        label="forward_5d_return",
        split=TimeSplit(
            date(2023, 1, 1),
            date(2023, 12, 31),
            date(2024, 1, 1),
            date(2024, 6, 30),
            date(2024, 7, 1),
            date(2024, 12, 31),
        ),
        model="rank",
        parameters=(("lookback", "20"),),
        cost_model_id="cn-etf-cost-v1",
        transaction_cost=Decimal("0.001"),
        seed=7,
    )


def manifest(status: QualityStatus = QualityStatus.PASSED) -> DataManifest:
    return DataManifest(
        "etf-v1", "fixture", "1", datetime(2026, 8, 12, tzinfo=UTC), "abc", status
    )


def test_experiment_runner_requires_passed_matching_data_and_is_immutable(tmp_path):
    runner = ExperimentRunner(tmp_path)

    def evaluator(spec):
        assert spec.seed == 7
        return ExperimentOutput(
            (("sharpe", Decimal("1.2")), ("maximum_drawdown", Decimal("-0.1"))),
            ("equity.parquet", "trades.parquet"),
        )

    completed = datetime(2026, 8, 12, 18, tzinfo=UTC)
    result = runner.run(
        run_spec(), manifest=manifest(), evaluator=evaluator, completed_at=completed
    )
    assert result.strategy_id == "ETF_MOM_V1"
    assert result.artifact_path.is_file()
    assert len(result.spec_hash) == 64 and len(result.result_hash) == 64
    with pytest.raises(FileExistsError, match="immutable"):
        runner.run(run_spec(), manifest=manifest(), evaluator=evaluator, completed_at=completed)
    with pytest.raises(ValueError, match="PASSED"):
        runner.run(run_spec("exp-2"), manifest=manifest(QualityStatus.FAILED), evaluator=evaluator)
    with pytest.raises(ValueError, match="data_version"):
        runner.run(
            replace(run_spec("exp-3"), data_version="wrong"),
            manifest=manifest(),
            evaluator=evaluator,
        )


def health(**changes):
    base = StrategyHealthInput(
        strategy_id="ETF_MOM_V1",
        as_of=datetime(2026, 8, 12, tzinfo=UTC),
        sample_count=30,
        rolling_return=Decimal("0.05"),
        rolling_sharpe=Decimal("1"),
        rolling_volatility=Decimal("0.15"),
        rolling_drawdown=Decimal("-0.1"),
        turnover=Decimal("2"),
        transaction_cost=Decimal("0.01"),
        exposure=Decimal("0.9"),
        signal_drift=Decimal("0.1"),
        factor_ic=Decimal("0.03"),
        factor_drift=Decimal("0.1"),
        execution_deviation=Decimal("0.005"),
    )
    return replace(base, **changes)


def test_strategy_health_monitor_classifies_and_deduplicates_research_tasks():
    monitor = StrategyHealthMonitor()
    healthy, task = monitor.evaluate(health())
    assert healthy.status is StrategyHealthStatus.HEALTHY and task is None
    watch, task = monitor.evaluate(health(sample_count=3))
    assert watch.status is StrategyHealthStatus.WATCH and task is None
    degraded_value = health(rolling_sharpe=Decimal("-0.2"), factor_drift=Decimal("0.5"))
    degraded, task = monitor.evaluate(degraded_value)
    assert degraded.status is StrategyHealthStatus.DEGRADED
    assert task is not None and "FACTOR_DRIFT" in task.reason_codes
    _, duplicate = monitor.evaluate(degraded_value)
    assert duplicate is None
    halted, task = monitor.evaluate(health(data_stale=True))
    assert halted.status is StrategyHealthStatus.HALTED and task is None
