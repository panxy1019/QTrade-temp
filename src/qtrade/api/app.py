from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from qtrade.accounting import Ledger
from qtrade.audit import AuditLog
from qtrade.control import PlatformRuntime
from qtrade.domain import RuntimeMode
from qtrade.execution import FeeSchedule, PaperGateway
from qtrade.monitoring import MetricsRegistry
from qtrade.oms import OrderManager
from qtrade.risk import RiskEngine, RiskLimits


class Role(StrEnum):
    READ = "read"
    CONTROL = "control"
    TRADE = "trade"


class ControlRequest(BaseModel):
    reason: str = "manual control"


@dataclass(slots=True)
class AppContext:
    runtime: PlatformRuntime
    metrics: MetricsRegistry
    oms: OrderManager
    ledger: Ledger
    audit: AuditLog


def bootstrap_context(mode: RuntimeMode = RuntimeMode.PAPER) -> AppContext:
    audit = AuditLog()
    metrics = MetricsRegistry()
    ledger = Ledger(opening_cash=Decimal("0"), audit=audit)
    oms = OrderManager(audit)
    risk = RiskEngine(
        RiskLimits(
            max_order_value=Decimal("0"),
            max_position_quantity=0,
            max_gross_exposure=Decimal("0"),
        ),
        audit,
    )
    PaperGateway(oms, ledger, audit, FeeSchedule("bootstrap", Decimal("0"), Decimal("0")))
    return AppContext(PlatformRuntime(mode, risk, audit, metrics), metrics, oms, ledger, audit)


def _require_role(required: Role) -> Callable[[str], str]:
    allowed = {
        Role.READ: {Role.READ, Role.CONTROL, Role.TRADE},
        Role.CONTROL: {Role.CONTROL, Role.TRADE},
        Role.TRADE: {Role.TRADE},
    }

    def dependency(x_qtrade_role: str = Header(default="read")) -> str:
        try:
            role = Role(x_qtrade_role.lower())
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="unknown role") from exc
        if role not in allowed[required]:
            raise HTTPException(status_code=403, detail="insufficient role")
        return role.value

    return dependency


def create_app(context: AppContext | None = None) -> FastAPI:
    state = context or bootstrap_context()
    app = FastAPI(title="Q Control Plane", version="0.1.0")

    @app.get("/health")
    def health(_: str = Depends(_require_role(Role.READ))) -> dict[str, Any]:
        return asdict(state.runtime.health())

    @app.get("/system")
    def system(_: str = Depends(_require_role(Role.READ))) -> dict[str, Any]:
        return asdict(state.runtime.status)

    @app.get("/risk")
    def risk(_: str = Depends(_require_role(Role.READ))) -> dict[str, str]:
        return {"state": state.runtime.status.risk_state.value}

    @app.get("/orders")
    def orders(_: str = Depends(_require_role(Role.READ))) -> list[dict[str, object]]:
        return [
            {
                "internal_order_id": order.internal_order_id,
                "symbol": order.request.symbol,
                "status": order.status.value,
                "quantity": order.request.quantity,
                "filled_quantity": order.filled_quantity,
            }
            for order in state.oms.orders
        ]

    @app.get("/positions")
    def positions(_: str = Depends(_require_role(Role.READ))) -> list[dict[str, Any]]:
        return [asdict(position) for position in state.ledger.account.positions.values()]

    @app.get("/account")
    def account(_: str = Depends(_require_role(Role.READ))) -> dict[str, str]:
        return {"cash": str(state.ledger.account.cash)}

    @app.get("/metrics")
    def metrics(_: str = Depends(_require_role(Role.READ))) -> dict[str, object]:
        return state.metrics.snapshot()

    @app.get("/market")
    def market(_: str = Depends(_require_role(Role.READ))) -> dict[str, object]:
        return {"connected": state.metrics.gateway_connected, "mode": state.runtime.status.mode}

    @app.get("/instruments")
    def instruments(_: str = Depends(_require_role(Role.READ))) -> list[object]:
        return []

    @app.get("/strategies")
    def strategies(_: str = Depends(_require_role(Role.READ))) -> list[object]:
        return []

    @app.get("/portfolio")
    def portfolio(_: str = Depends(_require_role(Role.READ))) -> dict[str, object]:
        return {"positions": len(state.ledger.account.positions)}

    @app.get("/trades")
    def trades(_: str = Depends(_require_role(Role.READ))) -> list[object]:
        return []

    @app.get("/pnl")
    def pnl(_: str = Depends(_require_role(Role.READ))) -> dict[str, str]:
        realized = sum(
            (position.realized_pnl for position in state.ledger.account.positions.values()),
            start=Decimal("0"),
        )
        return {"realized": str(realized)}

    @app.get("/audit")
    def audit(_: str = Depends(_require_role(Role.READ))) -> list[dict[str, object]]:
        return [
            {
                "timestamp": event.timestamp.isoformat(),
                "event_type": event.event_type,
                "severity": event.severity,
                "message": event.message,
                "order_id": event.order_id,
            }
            for event in state.audit.events
        ]

    @app.post("/system/start")
    def start(
        _: ControlRequest, actor: str = Depends(_require_role(Role.CONTROL))
    ) -> dict[str, Any]:
        try:
            state.runtime.start(actor=actor)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return asdict(state.runtime.status)

    @app.post("/system/stop")
    def stop(
        _: ControlRequest, actor: str = Depends(_require_role(Role.CONTROL))
    ) -> dict[str, Any]:
        state.runtime.stop(actor=actor)
        return asdict(state.runtime.status)

    @app.post("/system/pause")
    def pause(
        _: ControlRequest, actor: str = Depends(_require_role(Role.CONTROL))
    ) -> dict[str, Any]:
        state.runtime.pause(actor=actor)
        return asdict(state.runtime.status)

    @app.post("/system/resume")
    def resume(
        _: ControlRequest, actor: str = Depends(_require_role(Role.CONTROL))
    ) -> dict[str, Any]:
        try:
            state.runtime.resume(actor=actor)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return asdict(state.runtime.status)

    @app.post("/system/kill")
    def kill(
        payload: ControlRequest, actor: str = Depends(_require_role(Role.CONTROL))
    ) -> dict[str, Any]:
        state.runtime.kill(actor=actor, reason=payload.reason)
        return asdict(state.runtime.status)

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")

    return app
