from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from qtrade.research import TimeSplit
from qtrade.strategies import (
    PromotionEvidence,
    PromotionGate,
    PromotionOutcome,
    PromotionThresholds,
    StrategyLineage,
    StrategyRegistry,
    StrategyRole,
    StrategySpec,
    StrategyStatus,
)


def split() -> TimeSplit:
    return TimeSplit(
        date(2023, 1, 1),
        date(2023, 12, 31),
        date(2024, 1, 1),
        date(2024, 6, 30),
        date(2024, 7, 1),
        date(2024, 12, 31),
    )


def spec(
    strategy_id: str,
    version: str,
    *,
    parent: str | None = None,
    now: datetime | None = None,
) -> StrategySpec:
    timestamp = now or datetime(2026, 8, 12, tzinfo=UTC)
    return StrategySpec(
        strategy_id=strategy_id,
        name="ETF 动量轮动",
        family="ETF_MOMENTUM",
        version=version,
        parent_strategy_id=parent,
        git_commit="b502852",
        data_version="cn-etf-v1",
        universe=("510300", "510500"),
        factor_set=("momentum_20", "momentum_60"),
        model="rank",
        portfolio_rule="top_1_equal_weight",
        parameters=(("lookback", "20"), ("top_k", "1")),
        split=split(),
        created_at=timestamp,
        updated_at=timestamp,
    )


def advance_to_validated(registry: StrategyRegistry, strategy_id: str) -> None:
    for target in (
        StrategyStatus.EXPERIMENT,
        StrategyStatus.CANDIDATE,
        StrategyStatus.VALIDATED,
    ):
        registry.transition(strategy_id, target, actor="research", reason="gate passed")


def passing_evidence(evidence_id: str = "evidence-1") -> PromotionEvidence:
    return PromotionEvidence(
        evidence_id=evidence_id,
        oos_return=Decimal("0.12"),
        sharpe=Decimal("1.2"),
        sortino=Decimal("1.4"),
        maximum_drawdown=Decimal("-0.12"),
        calmar=Decimal("1"),
        turnover=Decimal("2"),
        transaction_cost=Decimal("0.01"),
        exposure=Decimal("0.9"),
        stability=Decimal("0.8"),
        parameter_sensitivity=Decimal("0.75"),
        walk_forward_consistency=Decimal("0.8"),
        data_quality_passed=True,
        embargo_validated=True,
        paper_sessions=30,
        shadow_sessions=10,
    )


def test_registry_persists_lifecycle_lineage_and_rejects_unsafe_jump(tmp_path):
    registry = StrategyRegistry(tmp_path / "strategies.db")
    parent = registry.register(spec("ETF_MOM_V1", "1"))
    assert parent.status is StrategyStatus.IDEA
    with pytest.raises(ValueError, match="illegal"):
        registry.transition(
            parent.spec.strategy_id,
            StrategyStatus.PAPER,
            actor="research",
            reason="skip evidence",
        )
    advance_to_validated(registry, parent.spec.strategy_id)
    child_spec = spec(
        "ETF_MOM_VOL_V2",
        "2",
        parent="ETF_MOM_V1",
        now=datetime(2026, 8, 13, tzinfo=UTC),
    )
    lineage = StrategyLineage(
        child_strategy_id=child_spec.strategy_id,
        parent_strategy_id="ETF_MOM_V1",
        reason="reduce drawdown",
        hypothesis="volatility scaling improves risk-adjusted consistency",
        change_summary="add 20-day realized-volatility scaling",
        experiment_result="pending",
        promotion_result="not evaluated",
        created_at=child_spec.created_at,
    )
    registry.register(child_spec, lineage=lineage)
    assert registry.lineage(child_spec.strategy_id) == lineage
    assert len(StrategyRegistry(tmp_path / "strategies.db").list()) == 2
    assert [item.after for item in registry.transitions("ETF_MOM_V1")] == [
        StrategyStatus.EXPERIMENT,
        StrategyStatus.CANDIDATE,
        StrategyStatus.VALIDATED,
    ]


def test_autonomous_transition_stops_at_shadow_and_live_status_does_not_exist(tmp_path):
    registry = StrategyRegistry(tmp_path / "strategies.db")
    registry.register(spec("ETF_MOM_V1", "1"))
    advance_to_validated(registry, "ETF_MOM_V1")
    registry.transition("ETF_MOM_V1", StrategyStatus.PAPER, actor="research", reason="paper")
    registry.transition("ETF_MOM_V1", StrategyStatus.SHADOW, actor="research", reason="shadow")
    with pytest.raises(PermissionError, match="human authorization"):
        registry.transition(
            "ETF_MOM_V1", StrategyStatus.APPROVED, actor="research", reason="automatic"
        )
    with pytest.raises(ValueError):
        StrategyStatus("LIVE")


def test_champion_and_challenger_require_validation_and_multi_metric_gate(tmp_path):
    registry = StrategyRegistry(tmp_path / "strategies.db")
    registry.register(spec("ETF_MOM_V1", "1"))
    registry.register(
        spec("ETF_MOM_V2", "2", parent="ETF_MOM_V1"),
        lineage=StrategyLineage(
            "ETF_MOM_V2",
            "ETF_MOM_V1",
            "improve stability",
            "dual horizon reduces regime sensitivity",
            "combine 20/60-day momentum",
            "walk-forward passed",
            "candidate",
            datetime(2026, 8, 12, tzinfo=UTC),
        ),
    )
    for strategy_id in ("ETF_MOM_V1", "ETF_MOM_V2"):
        advance_to_validated(registry, strategy_id)
    for target in (StrategyStatus.PAPER, StrategyStatus.SHADOW):
        registry.transition("ETF_MOM_V1", target, actor="research", reason="validated")
    registry.transition(
        "ETF_MOM_V1",
        StrategyStatus.APPROVED,
        actor="review-board",
        reason="human approval",
        human_authorized=True,
    )
    decision = registry.evaluate_promotion(
        "ETF_MOM_V1", passing_evidence(), actor="review-board"
    )
    assert decision.outcome is PromotionOutcome.PASS
    champion = registry.assign_research_role(
        "ETF_MOM_V1",
        StrategyRole.CHAMPION,
        actor="review-board",
        reason="multi-metric gate passed",
        evidence_id=decision.evidence_id,
    )
    assert champion.role is StrategyRole.CHAMPION
    challenger = registry.assign_research_role(
        "ETF_MOM_V2",
        StrategyRole.CHALLENGER,
        actor="review-board",
        reason="comparison candidate",
        evidence_id="evidence-2",
    )
    assert challenger.role is StrategyRole.CHALLENGER
    for target in (StrategyStatus.PAPER, StrategyStatus.SHADOW):
        registry.transition("ETF_MOM_V2", target, actor="research", reason="validated")
    registry.transition(
        "ETF_MOM_V2",
        StrategyStatus.APPROVED,
        actor="review-board",
        reason="human approval",
        human_authorized=True,
    )
    second_decision = registry.evaluate_promotion(
        "ETF_MOM_V2", passing_evidence("evidence-2"), actor="review-board"
    )
    with pytest.raises(ValueError, match="already has a champion"):
        registry.assign_research_role(
            "ETF_MOM_V2",
            StrategyRole.CHAMPION,
            actor="review-board",
            reason="invalid second champion",
            evidence_id=second_decision.evidence_id,
        )


def test_champion_rejects_caller_claim_without_persisted_pass(tmp_path):
    registry = StrategyRegistry(tmp_path / "strategies.db")
    registry.register(spec("ETF_MOM_V1", "1"))
    advance_to_validated(registry, "ETF_MOM_V1")
    with pytest.raises(ValueError, match="persisted passing"):
        registry.assign_research_role(
            "ETF_MOM_V1",
            StrategyRole.CHAMPION,
            actor="caller",
            reason="unverified claim",
            evidence_id="not-recorded",
        )


def test_promotion_evidence_identity_is_immutable(tmp_path):
    registry = StrategyRegistry(tmp_path / "strategies.db")
    evidence = passing_evidence()
    registry.register(spec("ETF_MOM_V1", "1"))
    registry.evaluate_promotion("ETF_MOM_V1", evidence, actor="review-board")
    with pytest.raises(ValueError, match="immutable"):
        registry.evaluate_promotion(
            "ETF_MOM_V1",
            PromotionEvidence(
                **{
                    **{name: getattr(evidence, name) for name in evidence.__dataclass_fields__},
                    "sharpe": Decimal("0.9"),
                }
            ),
            actor="review-board",
        )


def test_passing_decision_cannot_be_reused_for_another_strategy(tmp_path):
    registry = StrategyRegistry(tmp_path / "strategies.db")
    registry.register(spec("ETF_MOM_V1", "1"))
    registry.register(
        spec("ETF_MOM_V2", "2", parent="ETF_MOM_V1"),
        lineage=StrategyLineage(
            "ETF_MOM_V2", "ETF_MOM_V1", "change", "hypothesis", "summary",
            "experiment", "promotion", datetime(2026, 8, 12, tzinfo=UTC)
        ),
    )
    registry.evaluate_promotion("ETF_MOM_V1", passing_evidence(), actor="board")
    advance_to_validated(registry, "ETF_MOM_V2")
    for target in (StrategyStatus.PAPER, StrategyStatus.SHADOW):
        registry.transition("ETF_MOM_V2", target, actor="research", reason="validated")
    registry.transition(
        "ETF_MOM_V2", StrategyStatus.APPROVED, actor="board", reason="approved",
        human_authorized=True,
    )
    with pytest.raises(ValueError, match="persisted passing"):
        registry.assign_research_role(
            "ETF_MOM_V2", StrategyRole.CHAMPION, actor="board", reason="reuse",
            evidence_id="evidence-1",
        )


def test_promotion_gate_is_not_sharpe_only_and_can_request_more_observation():
    weak = passing_evidence("weak")
    weak = PromotionEvidence(
        **{
            **{name: getattr(weak, name) for name in weak.__dataclass_fields__},
            "stability": Decimal("0.2"),
            "parameter_sensitivity": Decimal("0.1"),
        }
    )
    decision = PromotionGate().evaluate(weak)
    assert decision.outcome is PromotionOutcome.REJECT
    assert "STABILITY" in decision.reasons and "PARAMETER_SENSITIVITY" in decision.reasons

    thresholds = PromotionThresholds(minimum_paper_sessions=60, minimum_shadow_sessions=20)
    observation = PromotionGate(thresholds).evaluate(passing_evidence("observe"))
    assert observation.outcome is PromotionOutcome.NEEDS_REVIEW
    assert observation.reasons == ("PAPER_SESSIONS", "SHADOW_SESSIONS")


def test_strategy_spec_is_immutable_and_updates_only_via_transition(tmp_path):
    registry = StrategyRegistry(tmp_path / "strategies.db")
    created = datetime(2026, 8, 12, tzinfo=UTC)
    registry.register(spec("ETF_MOM_V1", "1", now=created))
    later = created + timedelta(days=1)
    record = registry.transition(
        "ETF_MOM_V1",
        StrategyStatus.EXPERIMENT,
        actor="research",
        reason="begin experiment",
        occurred_at=later,
    )
    assert record.spec.created_at == created
    assert record.spec.updated_at == later
    with pytest.raises(ValueError, match="immutable specification"):
        registry.register(spec("ETF_MOM_V1", "1", now=later))
