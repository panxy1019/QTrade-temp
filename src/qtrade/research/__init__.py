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
from qtrade.research.multi_asset import (
    DailyMultiAssetBacktester,
    EqualWeightBuyHold,
    MomentumAllocation,
    MultiAssetBacktestResult,
    ResearchTrade,
    write_experiment_artifacts,
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
from qtrade.research.walk_forward_runner import (
    MultiAssetWalkForwardRunner,
    WalkForwardCandidate,
    WalkForwardFoldResult,
    WalkForwardResult,
)

__all__ = [
    "ExperimentArtifactStore",
    "ExperimentRecord",
    "BoundedResearchLoop",
    "FactorMetrics",
    "FactorObservation",
    "PerformanceMetrics",
    "DailyMultiAssetBacktester",
    "EqualWeightBuyHold",
    "MomentumAllocation",
    "MultiAssetBacktestResult",
    "ResearchTrade",
    "TimeSplit",
    "LeakageReview",
    "ResearchHypothesis",
    "ResearchIteration",
    "ReviewOutcome",
    "RobustnessResult",
    "RobustnessScenario",
    "RobustnessSuite",
    "WalkForwardConfig",
    "MultiAssetWalkForwardRunner",
    "WalkForwardCandidate",
    "WalkForwardFoldResult",
    "WalkForwardResult",
    "evaluate_factor",
    "evaluate_factor_periods",
    "evaluate_returns",
    "write_experiment_artifacts",
    "ExperimentOutput",
    "ExperimentRunner",
    "ExperimentRunResult",
    "ExperimentRunSpec",
    "build_walk_forward_splits",
]
