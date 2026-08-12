"""Simple trustworthy baseline strategies."""

from qtrade.strategies.baselines import BuyAndHoldStrategy, MovingAverageTrendStrategy
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
    "MovingAverageTrendStrategy",
    "PromotionDecision",
    "PromotionEvidence",
    "PromotionGate",
    "PromotionOutcome",
    "PromotionThresholds",
    "StrategyLineage",
    "StrategyRecord",
    "StrategyRegistry",
    "StrategyRole",
    "StrategySpec",
    "StrategyStatus",
    "StrategyTransition",
]
