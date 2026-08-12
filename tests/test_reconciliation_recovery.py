from decimal import Decimal

import pytest

from qtrade.audit import AuditLog
from qtrade.monitoring import MetricsRegistry
from qtrade.reconciliation import Reconciler
from qtrade.recovery import RecoveryCoordinator, RecoveryState
from qtrade.risk import KillState, RiskEngine, RiskLimits


def test_reconciliation_mismatch_latches_halt_and_metrics_failure():
    audit = AuditLog()
    metrics = MetricsRegistry()
    risk = RiskEngine(RiskLimits(Decimal("1"), 1, Decimal("1")), audit)
    result = Reconciler(risk, audit, metrics).compare(
        local_cash=Decimal("1"),
        external_cash=Decimal("2"),
        local_positions={},
        external_positions={},
        local_order_ids=set(),
        external_order_ids=set(),
        local_trade_ids=set(),
        external_trade_ids=set(),
    )
    assert result.ok is False
    assert result.differences == ("cash",)
    assert risk.state is KillState.HALTED
    assert metrics.reconciliation_status == "FAILED"


def test_recovery_cannot_skip_steps_or_resume_after_failed_reconciliation():
    audit = AuditLog()
    recovery = RecoveryCoordinator(audit)
    with pytest.raises(ValueError):
        recovery.mark_connected()
    recovery.mark_state_loaded()
    recovery.mark_connected()
    recovery.mark_reconciled(ok=False)
    assert recovery.state is RecoveryState.CONNECTED
    assert recovery.resume_allowed is False
    recovery.mark_reconciled(ok=True)
    recovery.mark_verified()
    assert recovery.resume_allowed is True
