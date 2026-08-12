from dataclasses import replace
from datetime import date
from decimal import Decimal

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


def test_robustness_suite_is_bounded_deterministic_and_detects_cliff():
    suite = RobustnessSuite(
        (
            RobustnessScenario("base"),
            RobustnessScenario(
                "cost-stress", (("lookback", "40"),), Decimal("2")
            ),
        )
    )

    def evaluator(spec):
        sharpe = Decimal("1.0") if dict(spec.parameters)["lookback"] == "20" else Decimal("-0.1")
        return ExperimentOutput((("sharpe", sharpe),), ())

    result = suite.run(run_spec(), evaluator)
    assert result.worst_sharpe == Decimal("-0.1")
    assert result.dispersion == Decimal("1.1")
    assert result.cliff_detected is True
    assert len(result.result_hash) == 64
    with pytest.raises(ValueError, match="bounded"):
        RobustnessSuite(tuple(RobustnessScenario(str(index)) for index in range(21)))


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
