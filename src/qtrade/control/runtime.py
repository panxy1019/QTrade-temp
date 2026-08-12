from __future__ import annotations

from dataclasses import dataclass

from qtrade.audit import AuditEvent, AuditLog
from qtrade.domain import RuntimeMode
from qtrade.monitoring import HealthStatus, MetricsRegistry
from qtrade.risk import KillState, RiskEngine


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    mode: RuntimeMode
    running: bool
    risk_state: KillState


class PlatformRuntime:
    def __init__(
        self,
        mode: RuntimeMode,
        risk: RiskEngine,
        audit: AuditLog,
        metrics: MetricsRegistry,
    ) -> None:
        self._mode = mode
        self._risk = risk
        self._audit = audit
        self._metrics = metrics
        self._running = False

    @property
    def status(self) -> RuntimeStatus:
        return RuntimeStatus(self._mode, self._running, self._risk.state)

    def start(self, *, actor: str) -> None:
        if self._risk.state is KillState.HALTED:
            raise ValueError("cannot start while halted")
        if not self._metrics.gateway_connected:
            raise ValueError("cannot start without a verified gateway connection")
        if self._metrics.reconciliation_status != "OK":
            raise ValueError("cannot start before successful reconciliation")
        self._running = True
        self._record("SYSTEM_START", actor)

    def stop(self, *, actor: str) -> None:
        self._running = False
        self._record("SYSTEM_STOP", actor)

    def pause(self, *, actor: str) -> None:
        self._risk.set_state(KillState.PAUSED, actor=actor, reason="manual API pause")
        self._record("SYSTEM_PAUSE", actor)

    def resume(self, *, actor: str) -> None:
        if self._risk.state is KillState.HALTED:
            raise ValueError("HALTED requires explicit reconciled recovery")
        self._risk.set_state(KillState.NORMAL, actor=actor, reason="manual API resume")
        self._record("SYSTEM_RESUME", actor)

    def kill(self, *, actor: str, reason: str) -> None:
        self._risk.set_state(KillState.HALTED, actor=actor, reason=reason)
        self._running = False
        self._record("KILL_SWITCH", actor, reason)

    def health(self) -> HealthStatus:
        healthy = (
            self._risk.state is not KillState.HALTED
            and self._metrics.gateway_connected
            and self._metrics.reconciliation_status == "OK"
        )
        return HealthStatus(
            healthy=healthy,
            runtime_mode=self._mode.value,
            risk_state=self._risk.state.value,
            gateway_connected=self._metrics.gateway_connected,
            reconciliation_status=self._metrics.reconciliation_status,
        )

    def ensure_submission_allowed(self) -> None:
        if not self._running:
            raise RuntimeError("runtime is not started")
        if self._risk.state is not KillState.NORMAL:
            raise RuntimeError(f"risk state {self._risk.state.value} blocks submission")
        if not self._metrics.gateway_connected:
            raise RuntimeError("gateway is not connected")
        if self._metrics.reconciliation_status != "OK":
            raise RuntimeError("reconciliation is not OK")

    def _record(self, event_type: str, actor: str, reason: str = "") -> None:
        self._audit.record(
            AuditEvent(
                event_type=event_type,
                service="control",
                message=f"actor={actor}; reason={reason}",
                severity="WARNING" if event_type == "KILL_SWITCH" else "INFO",
            )
        )
