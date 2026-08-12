"""Simple trustworthy baseline strategies."""

from qtrade.strategies.baselines import BuyAndHoldStrategy, MovingAverageTrendStrategy
from qtrade.strategies.families import (
    CrossSectionalFactorRanking,
    EtfMomentumRotation,
    EtfTrendRisk,
    MarketSeries,
)
from qtrade.strategies.health import (
    ResearchTask,
    StrategyHealthInput,
    StrategyHealthMonitor,
    StrategyHealthPolicy,
    StrategyHealthSnapshot,
    StrategyHealthStatus,
)
from qtrade.strategies.promotion import (
    PromotionDecision,
    PromotionEvidence,
    PromotionGate,
    PromotionOutcome,
    PromotionThresholds,
)
from qtrade.strategies.registry import (
    StrategyLineage,
    StrategyRecord,
    StrategyRegistry,
    StrategyRole,
    StrategySpec,
    StrategyStatus,
    StrategyTransition,
)

__all__ = [
    "BuyAndHoldStrategy",
    "CrossSectionalFactorRanking",
    "EtfMomentumRotation",
    "EtfTrendRisk",
    "MarketSeries",
    "MovingAverageTrendStrategy",
    "PromotionDecision",
    "PromotionEvidence",
    "PromotionGate",
    "PromotionOutcome",
    "PromotionThresholds",
    "ResearchTask",
    "StrategyLineage",
    "StrategyHealthInput",
    "StrategyHealthMonitor",
    "StrategyHealthPolicy",
    "StrategyHealthSnapshot",
    "StrategyHealthStatus",
    "StrategyRecord",
    "StrategyRegistry",
    "StrategyRole",
    "StrategySpec",
    "StrategyStatus",
    "StrategyTransition",
]
