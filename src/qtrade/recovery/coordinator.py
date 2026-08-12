from __future__ import annotations

from enum import StrEnum

from qtrade.audit import AuditEvent, AuditLog


class RecoveryState(StrEnum):
    COLD = "COLD"
    STATE_LOADED = "STATE_LOADED"
    CONNECTED = "CONNECTED"
    RECONCILED = "RECONCILED"
    VERIFIED = "VERIFIED"


class RecoveryCoordinator:
    """Enforces load → connect → reconcile → verify before components may resume."""

    def __init__(self, audit: AuditLog) -> None:
        self._audit = audit
        self._state = RecoveryState.COLD

    @property
    def state(self) -> RecoveryState:
        return self._state

    @property
    def resume_allowed(self) -> bool:
        return self._state is RecoveryState.VERIFIED

    def mark_state_loaded(self) -> None:
        self._advance(RecoveryState.COLD, RecoveryState.STATE_LOADED)

    def mark_connected(self) -> None:
        self._advance(RecoveryState.STATE_LOADED, RecoveryState.CONNECTED)

    def mark_reconciled(self, *, ok: bool) -> None:
        if not ok:
            self._audit.record(
                AuditEvent(
                    event_type="RECOVERY_RECONCILIATION_FAILED",
                    service="recovery",
                    message="reconciliation failed; recovery remains blocked",
                    severity="ERROR",
                )
            )
            return
        self._advance(RecoveryState.CONNECTED, RecoveryState.RECONCILED)

    def mark_verified(self) -> None:
        self._advance(RecoveryState.RECONCILED, RecoveryState.VERIFIED)

    def _advance(self, expected: RecoveryState, target: RecoveryState) -> None:
        if self._state is not expected:
            raise ValueError(f"recovery must be {expected.value} before {target.value}")
        self._state = target
        self._audit.record(
            AuditEvent(
                event_type="RECOVERY_STATE_CHANGED",
                service="recovery",
                message=f"state={target.value}",
            )
        )
