"""Reproducible chronological experiment contracts and evaluation."""

from qtrade.research.artifacts import ExperimentArtifactStore
from qtrade.research.experiment import ExperimentRecord, TimeSplit
from qtrade.research.governance import (
    BoundedResearchLoop,
    LeakageReview,
    ResearchHypothesis,
    ResearchIteration,
    ReviewOutcome,
    RobustnessResult,
    RobustnessScenario,
    RobustnessSuite,
)
from qtrade.research.metrics import (
    FactorMetrics,
    FactorObservation,
    PerformanceMetrics,
    evaluate_factor,
    evaluate_factor_periods,
    evaluate_returns,
)
from qtrade.research.runner import (
    ExperimentOutput,
    ExperimentRunner,
    ExperimentRunResult,
    ExperimentRunSpec,
)
from qtrade.research.walk_forward import (
    WalkForwardConfig,
    build_walk_forward_splits,
)

__all__ = [
    "ExperimentArtifactStore",
    "ExperimentRecord",
    "BoundedResearchLoop",
    "FactorMetrics",
    "FactorObservation",
    "PerformanceMetrics",
    "TimeSplit",
    "LeakageReview",
    "ResearchHypothesis",
    "ResearchIteration",
    "ReviewOutcome",
    "RobustnessResult",
    "RobustnessScenario",
    "RobustnessSuite",
    "WalkForwardConfig",
    "evaluate_factor",
    "evaluate_factor_periods",
    "evaluate_returns",
    "ExperimentOutput",
    "ExperimentRunner",
    "ExperimentRunResult",
    "ExperimentRunSpec",
    "build_walk_forward_splits",
]
