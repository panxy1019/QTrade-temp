from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

from qtrade.audit.log import AuditEvent, AuditLog
from qtrade.domain.models import AccountSnapshot, Position, Side, Trade


class AccountingError(ValueError):
    pass


class Ledger:
    def __init__(self, opening_cash: Decimal, audit: AuditLog) -> None:
        if opening_cash < 0:
            raise ValueError("opening cash cannot be negative")
        self.account = AccountSnapshot(cash=opening_cash)
        self._audit = audit
        self._trade_ids: set[str] = set()

    def book(self, trade: Trade) -> bool:
        if trade.trade_id in self._trade_ids:
            return False
        self._apply(self.account, trade)
        self._trade_ids.add(trade.trade_id)
        self._audit.record(
            AuditEvent(
                event_type="TRADE_BOOKED",
                service="accounting",
                message=f"trade_id={trade.trade_id}; quantity={trade.quantity}",
                order_id=trade.internal_order_id,
                timestamp=trade.timestamp,
            )
        )
        return True

    def project(self, trade: Trade) -> AccountSnapshot:
        """Validate and apply a trade to a detached account snapshot."""
        projected = deepcopy(self.account)
        self._apply(projected, trade)
        return projected

    def commit_projection(self, projected: AccountSnapshot, trade: Trade) -> bool:
        """Install an already validated/durable projection without performing I/O."""
        if trade.trade_id in self._trade_ids:
            return False
        self.account.cash = projected.cash
        self.account.positions = deepcopy(projected.positions)
        self._trade_ids.add(trade.trade_id)
        return True

    @staticmethod
    def _apply(account: AccountSnapshot, trade: Trade) -> None:
        if trade.quantity <= 0 or trade.price <= 0 or trade.fee < 0:
            raise AccountingError("invalid trade")
        position = account.positions.setdefault(trade.symbol, Position(symbol=trade.symbol))
        notional = trade.price * trade.quantity
        if trade.side is Side.BUY:
            total_cash = notional + trade.fee
            if total_cash > account.cash:
                raise AccountingError("trade would make cash negative")
            old_cost = position.average_cost * position.quantity
            position.quantity += trade.quantity
            position.average_cost = (old_cost + notional + trade.fee) / position.quantity
            account.cash -= total_cash
        else:
            if trade.quantity > position.quantity:
                raise AccountingError("trade would make position negative")
            position.quantity -= trade.quantity
            position.realized_pnl += (
                trade.price - position.average_cost
            ) * trade.quantity - trade.fee
            account.cash += notional - trade.fee
            if position.quantity == 0:
                position.average_cost = Decimal("0")
