from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from qtrade.audit import AuditEvent, AuditLog
from qtrade.control import ReadinessAttestation
from qtrade.monitoring import MetricsRegistry
from qtrade.risk import KillState, RiskEngine


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    ok: bool
    differences: tuple[str, ...]


class Reconciler:
    def __init__(
        self,
        risk: RiskEngine | None = None,
        audit: AuditLog | None = None,
        metrics: MetricsRegistry | None = None,
    ) -> None:
        self._risk = risk
        self._audit = audit
        self._metrics = metrics

    def compare(
        self,
        *,
        local_cash: Decimal,
        external_cash: Decimal,
        local_positions: dict[str, int],
        external_positions: dict[str, int],
        local_order_ids: set[str],
        external_order_ids: set[str],
        local_trade_ids: set[str],
        external_trade_ids: set[str],
    ) -> ReconciliationResult:
        differences: list[str] = []
        if local_cash != external_cash:
            differences.append("cash")
        if local_positions != external_positions:
            differences.append("positions")
        if local_order_ids != external_order_ids:
            differences.append("orders")
        if local_trade_ids != external_trade_ids:
            differences.append("trades")
        result = ReconciliationResult(not differences, tuple(differences))
        if self._metrics is not None:
            self._metrics.reconciliation_status = "OK" if result.ok else "FAILED"
        if self._audit is not None:
            self._audit.record(
                AuditEvent(
                    event_type="RECONCILIATION_COMPLETED",
                    service="reconciliation",
                    message=f"ok={result.ok}; differences={','.join(result.differences)}",
                    severity="INFO" if result.ok else "ERROR",
                )
            )
        if not result.ok and self._risk is not None:
            self._risk.set_state(
                KillState.HALTED,
                actor="reconciliation",
                reason=f"state mismatch: {','.join(result.differences)}",
            )
        return result

    def issue_attestation(
        self,
        result: ReconciliationResult,
        *,
        gateway_connected: bool,
        uncertain_fills: int,
        now: datetime,
        validity: timedelta = timedelta(minutes=5),
    ) -> ReadinessAttestation:
        if not result.ok:
            raise ValueError("failed reconciliation cannot attest readiness")
        attestation = ReadinessAttestation(
            str(uuid4()),
            now,
            now + validity,
            gateway_connected,
            result.ok,
            uncertain_fills,
        )
        attestation.validate(now)
        return attestation
