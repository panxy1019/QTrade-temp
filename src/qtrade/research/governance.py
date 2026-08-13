from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256

from qtrade.research.runner import ExperimentOutput, ExperimentRunSpec


@dataclass(frozen=True, slots=True)
class ResearchHypothesis:
    hypothesis_id: str
    motivation: str
    economic_intuition: str
    expected_regimes: tuple[str, ...]
    expected_failure_modes: tuple[str, ...]
    required_data: tuple[str, ...]
    evaluation_criteria: tuple[str, ...]

    def __post_init__(self) -> None:
        text = (self.hypothesis_id, self.motivation, self.economic_intuition)
        lists = (
            self.expected_regimes,
            self.expected_failure_modes,
            self.required_data,
            self.evaluation_criteria,
        )
        if not all(value.strip() for value in text) or any(not value for value in lists):
            raise ValueError("research hypothesis fields are required")


@dataclass(frozen=True, slots=True)
class RobustnessScenario:
    scenario_id: str
    parameter_overrides: tuple[tuple[str, str], ...] = ()
    cost_multiplier: Decimal = Decimal("1")
    regime: str = "ALL"
    universe_override: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if not self.scenario_id or not self.regime:
            raise ValueError("robustness scenario identity is required")
        if not self.cost_multiplier.is_finite() or self.cost_multiplier <= 0:
            raise ValueError("robustness cost multiplier must be finite and positive")
        keys = [key for key, _ in self.parameter_overrides]
        if len(keys) != len(set(keys)):
            raise ValueError("robustness parameter overrides must be unique")
        if self.universe_override is not None and (
            not self.universe_override
            or len(set(self.universe_override)) != len(self.universe_override)
        ):
            raise ValueError("robustness universe override must be nonempty and unique")


@dataclass(frozen=True, slots=True)
class RobustnessResult:
    scenario_metrics: tuple[tuple[str, tuple[tuple[str, Decimal], ...]], ...]
    worst_sharpe: Decimal
    dispersion: Decimal
    any_negative_sharpe: bool
    result_hash: str


class RobustnessSuite:
    def __init__(self, scenarios: tuple[RobustnessScenario, ...], *, maximum_scenarios: int = 20):
        if not scenarios or len(scenarios) > maximum_scenarios:
            raise ValueError("robustness suite must be predeclared and bounded")
        ids = [scenario.scenario_id for scenario in scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("robustness scenario identities must be unique")
        self._scenarios = scenarios

    def run(
        self,
        spec: ExperimentRunSpec,
        evaluator: Callable[[ExperimentRunSpec, RobustnessScenario], ExperimentOutput],
    ) -> RobustnessResult:
        results: list[tuple[str, tuple[tuple[str, Decimal], ...]]] = []
        sharpes: list[Decimal] = []
        for scenario in self._scenarios:
            parameters = dict(spec.parameters)
            parameters.update(dict(scenario.parameter_overrides))
            varied = replace(
                spec,
                parameters=tuple(sorted(parameters.items())),
                transaction_cost=spec.transaction_cost * scenario.cost_multiplier,
                universe=scenario.universe_override or spec.universe,
            )
            output: ExperimentOutput = evaluator(varied, scenario)
            metrics = dict(output.metrics)
            if "sharpe" not in metrics:
                raise ValueError("robustness evaluation requires sharpe")
            sharpes.append(metrics["sharpe"])
            results.append((scenario.scenario_id, output.metrics))
        worst = min(sharpes)
        dispersion = max(sharpes) - min(sharpes)
        any_negative = any(value < Decimal("0") for value in sharpes)
        payload = {
            "scenarios": [asdict(scenario) for scenario in self._scenarios],
            "results": results,
            "worst_sharpe": str(worst),
            "dispersion": str(dispersion),
            "any_negative_sharpe": any_negative,
        }
        digest = sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        return RobustnessResult(tuple(results), worst, dispersion, any_negative, digest)


class ReviewOutcome(StrEnum):
    PASS = "PASS"
    REJECT = "REJECT"
    NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass(frozen=True, slots=True)
class LeakageReview:
    reviewer_id: str
    outcome: ReviewOutcome
    findings: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.reviewer_id:
            raise ValueError("independent reviewer identity is required")
        if self.outcome is ReviewOutcome.PASS and self.findings:
            raise ValueError("passing leakage review cannot contain findings")


@dataclass(frozen=True, slots=True)
class ResearchIteration:
    iteration_id: str
    hypothesis: ResearchHypothesis
    candidate_specs: tuple[ExperimentRunSpec, ...]
    leakage_review: LeakageReview


class BoundedResearchLoop:
    def __init__(self, *, maximum_iterations: int = 5, maximum_candidates: int = 10) -> None:
        if maximum_iterations <= 0 or maximum_candidates <= 0:
            raise ValueError("research loop budgets must be positive")
        self._maximum_iterations = maximum_iterations
        self._maximum_candidates = maximum_candidates
        self._iterations: list[ResearchIteration] = []
        self._hypotheses: set[str] = set()
        self._test_periods: set[tuple[date, date]] = set()

    def record(self, iteration: ResearchIteration) -> None:
        if len(self._iterations) >= self._maximum_iterations:
            raise RuntimeError("research iteration budget exhausted")
        if sum(len(item.candidate_specs) for item in self._iterations) + len(
            iteration.candidate_specs
        ) > self._maximum_candidates:
            raise RuntimeError("research candidate budget exhausted")
        if iteration.hypothesis.hypothesis_id in self._hypotheses:
            raise ValueError("research hypothesis already evaluated")
        if not iteration.candidate_specs:
            raise ValueError("research iteration requires candidates")
        if iteration.leakage_review.outcome is not ReviewOutcome.PASS:
            raise ValueError("research iteration requires independent leakage review pass")
        test_periods = {
            (candidate.split.test_start, candidate.split.test_end)
            for candidate in iteration.candidate_specs
        }
        if any(
            not (end < existing_start or start > existing_end)
            for start, end in test_periods
            for existing_start, existing_end in self._test_periods
        ):
            raise ValueError("held-out test period cannot be reused for candidate selection")
        self._hypotheses.add(iteration.hypothesis.hypothesis_id)
        self._test_periods.update(test_periods)
        self._iterations.append(iteration)

    @property
    def history(self) -> tuple[ResearchIteration, ...]:
        return tuple(self._iterations)
