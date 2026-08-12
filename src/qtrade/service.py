"""Mandatory risk → OMS → paper execution application pipeline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from qtrade.domain import RuntimeMode
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
        runtime_mode: RuntimeMode,
        reservations: ReservationBook | None = None,
        submission_guard: Callable[[], None] | None = None,
        state_change_handler: Callable[[], None] | None = None,
    ) -> None:
        self._risk = risk
        self._oms = oms
        self._gateway = gateway
        if gateway.external_order_transmission:
            raise ValueError("external order transmission is unavailable in this build")
        self._reservations = reservations or gateway.reservations
        self._submission_guard = submission_guard
        self._runtime_mode = runtime_mode
        if runtime_mode in {RuntimeMode.PAPER, RuntimeMode.SHADOW} and submission_guard is None:
            raise ValueError("PAPER/SHADOW TradingService requires a runtime submission guard")
        self._state_change_handler = state_change_handler

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
            reserved_buy = {
                request.symbol: self._reservations.reserved_buy_quantity(request.symbol)
            }
            reservation_context = replace(
                context,
                reserved_cash=self._reservations.reserved_cash,
                reserved_sell_quantities=reserved_sell,
                reserved_buy_quantities=reserved_buy,
                reserved_buy_notional=self._reservations.reserved_buy_notional,
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
            try:
                submitted = self._gateway.submit(order, now=now)
            except ConnectionError:
                self._oms.transition(order, OrderStatus.SUBMISSION_UNKNOWN, now=now)
                raise
            except Exception:
                self._reservations.release(order.internal_order_id)
                raise
            finally:
                if self._state_change_handler is not None:
                    self._state_change_handler()
            if submitted.status is OrderStatus.REJECTED:
                self._reservations.release(order.internal_order_id)
                if self._state_change_handler is not None:
                    self._state_change_handler()
            return order, True
