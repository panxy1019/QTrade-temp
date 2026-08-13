from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from qtrade.research import (
    BoundedResearchLoop,
    ExperimentOutput,
    ExperimentRunSpec,
    LeakageReview,
    ResearchHypothesis,
    ResearchIteration,
    ReviewOutcome,
    RobustnessScenario,
    RobustnessSuite,
    TimeSplit,
)


def run_spec(experiment_id: str = "exp-1") -> ExperimentRunSpec:
    return ExperimentRunSpec(
        experiment_id,
        "ETF_MOM_V1",
        "d68cbb6",
        "etf-v1",
        ("510300", "510500"),
        ("momentum_20",),
        "forward_5d_return",
        TimeSplit(
            date(2023, 1, 1),
            date(2023, 12, 31),
            date(2024, 1, 1),
            date(2024, 6, 30),
            date(2024, 7, 1),
            date(2024, 12, 31),
        ),
        "rank",
        (("lookback", "20"),),
        "cn-etf-cost-v1",
        Decimal("0.001"),
        7,
    )


def hypothesis(identity: str = "hyp-1") -> ResearchHypothesis:
    return ResearchHypothesis(
        identity,
        "test momentum persistence after cross-sectional ranking",
        "slow institutional rebalancing may create return continuation",
        ("trending",),
        ("reversal", "crowding"),
        ("point-in-time ETF bars",),
        ("positive OOS return", "stable walk-forward Sharpe"),
    )


def test_robustness_suite_is_bounded_deterministic_and_detects_negative_sharpe():
    suite = RobustnessSuite(
        (
            RobustnessScenario("base"),
            RobustnessScenario(
                "cost-stress", (("lookback", "40"),), Decimal("2")
            ),
        )
    )

    seen = []

    def evaluator(spec, scenario):
        seen.append((scenario.regime, spec.universe))
        sharpe = Decimal("1.0") if dict(spec.parameters)["lookback"] == "20" else Decimal("-0.1")
        return ExperimentOutput((("sharpe", sharpe),), (Path("unused"),))

    result = suite.run(run_spec(), evaluator)
    assert result.worst_sharpe == Decimal("-0.1")
    assert result.dispersion == Decimal("1.1")
    assert result.any_negative_sharpe is True
    assert seen == [("ALL", ("510300", "510500")), ("ALL", ("510300", "510500"))]
    assert len(result.result_hash) == 64
    with pytest.raises(ValueError, match="bounded"):
        RobustnessSuite(tuple(RobustnessScenario(str(index)) for index in range(21)))


def test_negative_sharpe_flag_does_not_claim_parameter_cliff():
    suite = RobustnessSuite((RobustnessScenario("base"), RobustnessScenario("nearby")))

    def evaluator(spec, scenario):
        return ExperimentOutput((("sharpe", Decimal("-0.1")),), (Path("unused"),))

    result = suite.run(run_spec(), evaluator)
    assert result.dispersion == 0
    assert result.any_negative_sharpe is True


def test_robustness_scenario_binds_regime_universe_and_definition_hash():
    spec = run_spec()

    def evaluator(varied, scenario):
        return ExperimentOutput(
            (
                ("sharpe", Decimal("1")),
                ("universe_size", Decimal(len(varied.universe))),
                ("regime_seen", Decimal("1") if scenario.regime == "BEAR" else Decimal("0")),
            ),
            (Path("unused"),),
        )

    base = RobustnessSuite(
        (RobustnessScenario("subset", regime="BEAR", universe_override=("510300",)),)
    ).run(spec, evaluator)
    changed = RobustnessSuite(
        (RobustnessScenario("subset", regime="BULL", universe_override=("510300",)),)
    ).run(spec, evaluator)
    assert dict(base.scenario_metrics[0][1])["universe_size"] == 1
    assert dict(base.scenario_metrics[0][1])["regime_seen"] == 1
    assert base.result_hash != changed.result_hash


def test_research_loop_enforces_review_budgets_uniqueness_and_test_holdout():
    loop = BoundedResearchLoop(maximum_iterations=2, maximum_candidates=2)
    passed = LeakageReview("independent-reviewer", ReviewOutcome.PASS, ())
    loop.record(ResearchIteration("iteration-1", hypothesis(), (run_spec(),), passed))
    with pytest.raises(ValueError, match="already evaluated"):
        loop.record(ResearchIteration("duplicate", hypothesis(), (run_spec("dup"),), passed))

    reused = replace(
        run_spec("second"),
        split=replace(
            run_spec().split,
            train_start=date(2022, 1, 1),
            train_end=date(2022, 12, 31),
        ),
    )
    with pytest.raises(ValueError, match="held-out test"):
        loop.record(ResearchIteration("reuse-test", hypothesis("hyp-2"), (reused,), passed))
    overlapping = replace(
        run_spec("overlap"),
        split=replace(
            run_spec().split,
            test_start=date(2024, 12, 1),
            test_end=date(2025, 6, 30),
        ),
    )
    with pytest.raises(ValueError, match="held-out test"):
        loop.record(
            ResearchIteration("overlap-test", hypothesis("hyp-overlap"), (overlapping,), passed)
        )
    with pytest.raises(ValueError, match="leakage review"):
        BoundedResearchLoop().record(
            ResearchIteration(
                "failed-review",
                hypothesis("hyp-3"),
                (
                    replace(
                        run_spec("third"),
                        split=replace(
                            run_spec().split,
                            test_start=date(2025, 1, 1),
                            test_end=date(2025, 12, 31),
                        ),
                    ),
                ),
                LeakageReview("reviewer", ReviewOutcome.REJECT, ("future membership",)),
            )
        )
