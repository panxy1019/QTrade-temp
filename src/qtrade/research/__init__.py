"""Reproducible chronological experiment contracts and evaluation."""

from qtrade.research.artifacts import ExperimentArtifactStore
from qtrade.research.experiment import ExperimentRecord, TimeSplit
from qtrade.research.factor_search import FactorDiagnostic, diagnose_price_factors
from qtrade.research.factors import PriceFactorSpec, skipped_momentum
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
    RankedFactorAllocation,
    ResearchTrade,
    evaluate_backtest_window,
    write_experiment_artifacts,
)
from qtrade.research.preview import BacktestPreviewSpec, run_backtest_preview
from qtrade.research.robust_factor_search import (
    BootstrapSummary,
    CandidateRobustnessEvaluation,
    CandidateRobustnessSummary,
    CandidateWindowResult,
    RobustnessGateInputs,
    RobustnessGateResult,
    ValidationWindow,
    deterministic_universe_subsets,
    evaluate_candidate_grid,
    evaluate_robustness_gate,
    nearest_parameter_neighbor_ids,
    rank_candidate_summaries,
    stationary_bootstrap_total_returns,
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
    "FactorDiagnostic",
    "PriceFactorSpec",
    "PerformanceMetrics",
    "DailyMultiAssetBacktester",
    "EqualWeightBuyHold",
    "MomentumAllocation",
    "MultiAssetBacktestResult",
    "RankedFactorAllocation",
    "ResearchTrade",
    "evaluate_backtest_window",
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
    "diagnose_price_factors",
    "evaluate_returns",
    "write_experiment_artifacts",
    "BacktestPreviewSpec",
    "run_backtest_preview",
    "BootstrapSummary",
    "CandidateRobustnessEvaluation",
    "CandidateRobustnessSummary",
    "CandidateWindowResult",
    "RobustnessGateInputs",
    "RobustnessGateResult",
    "ValidationWindow",
    "deterministic_universe_subsets",
    "evaluate_candidate_grid",
    "evaluate_robustness_gate",
    "nearest_parameter_neighbor_ids",
    "rank_candidate_summaries",
    "stationary_bootstrap_total_returns",
    "ExperimentOutput",
    "ExperimentRunner",
    "ExperimentRunResult",
    "ExperimentRunSpec",
    "build_walk_forward_splits",
    "skipped_momentum",
]
