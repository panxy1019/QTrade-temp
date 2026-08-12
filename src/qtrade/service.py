"""Mandatory risk → OMS → paper execution application pipeline."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from datetime import datetime

from qtrade.domain import RuntimeMode
from qtrade.domain.models import CancelCommand, Order, OrderRequest, OrderStatus
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
        submission_observer: Callable[[Order, datetime], None] | None = None,
        cancel_observer: Callable[[Order], None] | None = None,
        observer_failure_handler: Callable[[], None] | None = None,
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
        self._submission_observer = submission_observer
        self._cancel_observer = cancel_observer
        self._observer_failure_handler = observer_failure_handler
        self._observed_cancel_keys: set[str] = set()

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
                self._observe_submission(order, now)
                return order, True
            self._oms.transition(order, OrderStatus.RISK_PASSED, now=now)
            self._reservations.reserve(
                order.internal_order_id, request, self._risk.required_buy_cash(request)
            )
            try:
                submitted = self._gateway.submit(order, now=now)
            except ConnectionError:
                self._oms.transition(order, OrderStatus.SUBMISSION_UNKNOWN, now=now)
                self._observe_submission(order, now)
                raise
            except Exception:
                self._reservations.release(order.internal_order_id)
                self._observe_submission(order, now)
                raise
            finally:
                if self._state_change_handler is not None:
                    self._state_change_handler()
            if submitted.status is OrderStatus.REJECTED:
                self._reservations.release(order.internal_order_id)
                if self._state_change_handler is not None:
                    self._state_change_handler()
            self._observe_submission(order, now)
            return order, True

    def cancel(self, command: CancelCommand, *, now: datetime | None = None) -> Order:
        """Cancel through the single write entrance, even while trading is stopped.

        Cancels deliberately do not use the submission guard: PAUSED and HALTED
        prevent new exposure but must not prevent reducing outstanding exposure.
        The dispatch gate still serializes cancellation with kill transitions and
        new-order dispatch.
        """
        with self._risk.dispatch_gate:
            order = self._oms.get(command.internal_order_id)
            cancelled, _ = self._gateway.cancel_with_result(
                order,
                idempotency_key=command.idempotency_key,
                now=now,
            )
            if self._state_change_handler is not None:
                self._state_change_handler()
            if command.idempotency_key not in self._observed_cancel_keys:
                self._observe_cancel(cancelled)
                self._observed_cancel_keys.add(command.idempotency_key)
            return cancelled

    def _observe_submission(self, order: Order, now: datetime | None) -> None:
        if self._submission_observer is not None:
            try:
                self._submission_observer(order, now or order.updated_at)
            except Exception:
                if self._observer_failure_handler is not None:
                    with suppress(Exception):
                        self._observer_failure_handler()

    def _observe_cancel(self, order: Order) -> None:
        if self._cancel_observer is not None:
            try:
                self._cancel_observer(order)
            except Exception:
                if self._observer_failure_handler is not None:
                    with suppress(Exception):
                        self._observer_failure_handler()
