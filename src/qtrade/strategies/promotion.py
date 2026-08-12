from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class PromotionOutcome(StrEnum):
    PASS = "PASS"
    REJECT = "REJECT"
    NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass(frozen=True, slots=True)
class PromotionEvidence:
    evidence_id: str
    oos_return: Decimal
    sharpe: Decimal
    sortino: Decimal
    maximum_drawdown: Decimal
    calmar: Decimal
    turnover: Decimal
    transaction_cost: Decimal
    exposure: Decimal
    stability: Decimal
    parameter_sensitivity: Decimal
    walk_forward_consistency: Decimal
    data_quality_passed: bool
    embargo_validated: bool
    paper_sessions: int
    shadow_sessions: int

    def __post_init__(self) -> None:
        if not self.evidence_id:
            raise ValueError("promotion evidence identity is required")
        values = (
            self.oos_return,
            self.sharpe,
            self.sortino,
            self.maximum_drawdown,
            self.calmar,
            self.turnover,
            self.transaction_cost,
            self.exposure,
            self.stability,
            self.parameter_sensitivity,
            self.walk_forward_consistency,
        )
        if any(not value.is_finite() for value in values):
            raise ValueError("promotion evidence must be finite")
        if self.maximum_drawdown > 0:
            raise ValueError("maximum drawdown must be zero or negative")
        if self.turnover < 0 or self.transaction_cost < 0:
            raise ValueError("turnover and transaction cost cannot be negative")
        if any(
            not Decimal("0") <= value <= Decimal("1")
            for value in (
                self.exposure,
                self.stability,
                self.parameter_sensitivity,
                self.walk_forward_consistency,
            )
        ):
            raise ValueError("normalized promotion evidence must be in [0, 1]")
        if self.paper_sessions < 0 or self.shadow_sessions < 0:
            raise ValueError("session counts cannot be negative")


@dataclass(frozen=True, slots=True)
class PromotionThresholds:
    minimum_oos_return: Decimal = Decimal("0")
    minimum_sharpe: Decimal = Decimal("0.5")
    minimum_sortino: Decimal = Decimal("0.5")
    maximum_drawdown_magnitude: Decimal = Decimal("0.30")
    minimum_calmar: Decimal = Decimal("0.25")
    maximum_turnover: Decimal = Decimal("12")
    maximum_transaction_cost: Decimal = Decimal("0.05")
    maximum_exposure: Decimal = Decimal("1")
    minimum_stability: Decimal = Decimal("0.6")
    minimum_parameter_sensitivity: Decimal = Decimal("0.6")
    minimum_walk_forward_consistency: Decimal = Decimal("0.6")
    minimum_paper_sessions: int = 20
    minimum_shadow_sessions: int = 5


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    evidence_id: str
    outcome: PromotionOutcome
    reasons: tuple[str, ...]


class PromotionGate:
    def __init__(self, thresholds: PromotionThresholds | None = None) -> None:
        self._thresholds = thresholds or PromotionThresholds()

    def evaluate(self, evidence: PromotionEvidence) -> PromotionDecision:
        if not evidence.data_quality_passed:
            return PromotionDecision(
                evidence.evidence_id, PromotionOutcome.REJECT, ("DATA_QUALITY_FAILED",)
            )
        if not evidence.embargo_validated:
            return PromotionDecision(
                evidence.evidence_id, PromotionOutcome.REJECT, ("EMBARGO_NOT_VALIDATED",)
            )
        thresholds = self._thresholds
        failures: list[str] = []
        comparisons = (
            (evidence.oos_return >= thresholds.minimum_oos_return, "OOS_RETURN"),
            (evidence.sharpe >= thresholds.minimum_sharpe, "SHARPE"),
            (evidence.sortino >= thresholds.minimum_sortino, "SORTINO"),
            (
                abs(evidence.maximum_drawdown) <= thresholds.maximum_drawdown_magnitude,
                "MAXIMUM_DRAWDOWN",
            ),
            (evidence.calmar >= thresholds.minimum_calmar, "CALMAR"),
            (evidence.turnover <= thresholds.maximum_turnover, "TURNOVER"),
            (
                evidence.transaction_cost <= thresholds.maximum_transaction_cost,
                "TRANSACTION_COST",
            ),
            (evidence.exposure <= thresholds.maximum_exposure, "EXPOSURE"),
            (evidence.stability >= thresholds.minimum_stability, "STABILITY"),
            (
                evidence.parameter_sensitivity >= thresholds.minimum_parameter_sensitivity,
                "PARAMETER_SENSITIVITY",
            ),
            (
                evidence.walk_forward_consistency
                >= thresholds.minimum_walk_forward_consistency,
                "WALK_FORWARD_CONSISTENCY",
            ),
        )
        failures.extend(code for passed, code in comparisons if not passed)
        missing_observation: list[str] = []
        if evidence.paper_sessions < thresholds.minimum_paper_sessions:
            missing_observation.append("PAPER_SESSIONS")
        if evidence.shadow_sessions < thresholds.minimum_shadow_sessions:
            missing_observation.append("SHADOW_SESSIONS")
        if failures:
            return PromotionDecision(
                evidence.evidence_id, PromotionOutcome.REJECT, tuple(failures)
            )
        if missing_observation:
            return PromotionDecision(
                evidence.evidence_id,
                PromotionOutcome.NEEDS_REVIEW,
                tuple(missing_observation),
            )
        return PromotionDecision(evidence.evidence_id, PromotionOutcome.PASS, ())
