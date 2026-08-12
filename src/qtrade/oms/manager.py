from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from uuid import uuid4

from qtrade.audit.log import AuditEvent, AuditLog
from qtrade.domain.models import Order, OrderRequest, OrderStatus, utc_now


class InvalidTransition(ValueError):
    pass


class DuplicateOrderConflict(ValueError):
    pass


_ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset({OrderStatus.RISK_PENDING}),
    OrderStatus.RISK_PENDING: frozenset({OrderStatus.RISK_PASSED, OrderStatus.RISK_REJECTED}),
    OrderStatus.RISK_PASSED: frozenset({OrderStatus.SUBMITTED, OrderStatus.SUBMISSION_UNKNOWN}),
    OrderStatus.SUBMITTED: frozenset(
        {OrderStatus.ACCEPTED, OrderStatus.REJECTED, OrderStatus.SUBMISSION_UNKNOWN}
    ),
    OrderStatus.SUBMISSION_UNKNOWN: frozenset(
        {OrderStatus.ACCEPTED, OrderStatus.REJECTED, OrderStatus.CANCEL_PENDING}
    ),
    OrderStatus.ACCEPTED: frozenset(
        {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCEL_PENDING}
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCEL_PENDING}
    ),
    OrderStatus.CANCEL_PENDING: frozenset(
        {OrderStatus.CANCELLED, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED}
    ),
    OrderStatus.RISK_REJECTED: frozenset(),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
}


class OrderManager:
    def __init__(self, audit: AuditLog) -> None:
        self._audit = audit
        self._orders: dict[str, Order] = {}
        self._by_idempotency_key: dict[str, str] = {}

    @property
    def orders(self) -> tuple[Order, ...]:
        return tuple(self._orders.values())

    def get(self, internal_order_id: str) -> Order:
        return self._orders[internal_order_id]

    def restore(self, order: Order) -> None:
        if order.internal_order_id in self._orders:
            raise ValueError("order already restored")
        existing = self._by_idempotency_key.get(order.request.idempotency_key)
        if existing is not None:
            raise DuplicateOrderConflict("restored idempotency key conflicts")
        self._orders[order.internal_order_id] = order
        self._by_idempotency_key[order.request.idempotency_key] = order.internal_order_id

    def create(self, request: OrderRequest, *, now: datetime | None = None) -> tuple[Order, bool]:
        existing_id = self._by_idempotency_key.get(request.idempotency_key)
        if existing_id is not None:
            existing = self._orders[existing_id]
            if replace(request, created_at=existing.request.created_at) != existing.request:
                raise DuplicateOrderConflict("idempotency key reused with a different request")
            return existing, False
        timestamp = now or utc_now()
        order = Order(
            internal_order_id=str(uuid4()),
            request=request,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._orders[order.internal_order_id] = order
        self._by_idempotency_key[request.idempotency_key] = order.internal_order_id
        self._record(order, "ORDER_CREATED", "order created")
        return order, True

    def transition(
        self,
        order: Order,
        target: OrderStatus,
        *,
        now: datetime | None = None,
        reason: str | None = None,
        broker_order_id: str | None = None,
    ) -> Order:
        if target not in _ALLOWED_TRANSITIONS[order.status]:
            raise InvalidTransition(f"{order.status.value} cannot transition to {target.value}")
        order.status = target
        order.updated_at = now or utc_now()
        if reason is not None:
            order.rejection_reason = reason
        if broker_order_id is not None:
            order.broker_order_id = broker_order_id
        self._record(order, "ORDER_TRANSITION", f"status={target.value}; reason={reason or ''}")
        return order

    def apply_fill(self, order: Order, quantity: int, *, now: datetime | None = None) -> Order:
        if quantity <= 0 or quantity > order.remaining_quantity:
            raise ValueError(
                "fill quantity must be positive and no greater than remaining quantity"
            )
        order.filled_quantity += quantity
        target = (
            OrderStatus.FILLED if order.remaining_quantity == 0 else OrderStatus.PARTIALLY_FILLED
        )
        return self.transition(order, target, now=now)

    def project_fill(
        self, order: Order, quantity: int, *, now: datetime | None = None
    ) -> Order:
        """Validate a fill transition on a detached order without auditing."""
        if quantity <= 0 or quantity > order.remaining_quantity:
            raise ValueError(
                "fill quantity must be positive and no greater than remaining quantity"
            )
        projected = deepcopy(order)
        projected.filled_quantity += quantity
        target = (
            OrderStatus.FILLED
            if projected.remaining_quantity == 0
            else OrderStatus.PARTIALLY_FILLED
        )
        if target not in _ALLOWED_TRANSITIONS[projected.status]:
            raise InvalidTransition(
                f"{projected.status.value} cannot transition to {target.value}"
            )
        projected.status = target
        projected.updated_at = now or utc_now()
        return projected

    def commit_projection(self, order: Order, projected: Order) -> None:
        """Install an already validated/durable order projection without audit I/O."""
        if order.internal_order_id != projected.internal_order_id:
            raise ValueError("projected order identity mismatch")
        order.status = projected.status
        order.broker_order_id = projected.broker_order_id
        order.filled_quantity = projected.filled_quantity
        order.rejection_reason = projected.rejection_reason
        order.updated_at = projected.updated_at

    def _record(self, order: Order, event_type: str, message: str) -> None:
        self._audit.record(
            AuditEvent(
                event_type=event_type,
                service="oms",
                message=message,
                strategy_id=order.request.strategy_id,
                portfolio_id=order.request.portfolio_id,
                order_id=order.internal_order_id,
                timestamp=order.updated_at,
            )
        )
