"""Mandatory risk → OMS → paper execution application pipeline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from qtrade.domain.models import Order, OrderRequest, OrderStatus
from qtrade.execution.paper import PaperGateway
from qtrade.execution.reservations import ReservationBook
from qtrade.oms.manager import OrderManager
from qtrade.risk.engine import RiskContext, RiskEngine


class TradingService:
    def __init__(
        self,
        risk: RiskEngine,
        oms: OrderManager,
        gateway: PaperGateway,
        reservations: ReservationBook | None = None,
        submission_guard: Callable[[], None] | None = None,
    ) -> None:
        self._risk = risk
        self._oms = oms
        self._gateway = gateway
        if gateway.external_order_transmission:
            raise ValueError("external order transmission is unavailable in this build")
        self._reservations = reservations or gateway.reservations
        self._submission_guard = submission_guard

    def submit(
        self, request: OrderRequest, context: RiskContext, *, now: datetime | None = None
    ) -> tuple[Order, bool]:
        with self._risk.dispatch_gate:
            if self._submission_guard is not None:
                self._submission_guard()
            order, created = self._oms.create(request, now=now)
            if not created:
                return order, False
            self._oms.transition(order, OrderStatus.RISK_PENDING, now=now)
            reserved_sell = {
                request.symbol: self._reservations.reserved_sell_quantity(request.symbol)
            }
            reservation_context = replace(
                context,
                reserved_cash=self._reservations.reserved_cash,
                reserved_sell_quantities=reserved_sell,
            )
            decision = self._risk.assess(request, reservation_context)
            if not decision.passed:
                self._oms.transition(
                    order, OrderStatus.RISK_REJECTED, now=now, reason=decision.reason
                )
                return order, True
            self._oms.transition(order, OrderStatus.RISK_PASSED, now=now)
            self._reservations.reserve(
                order.internal_order_id, request, self._risk.required_buy_cash(request)
            )
            self._gateway.submit(order, now=now)
            return order, True
