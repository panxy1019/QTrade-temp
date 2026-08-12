from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from qtrade.accounting import Ledger, SellabilityLedger
from qtrade.audit import AuditLog
from qtrade.data import Bar, DataManifest, QualityStatus
from qtrade.domain import Instrument, OrderRequest, RuntimeMode, Side
from qtrade.execution import FeeSchedule, PaperGateway
from qtrade.instruments import TradingRule
from qtrade.oms import OrderManager
from qtrade.risk import RiskContext, RiskEngine, RiskLimits
from qtrade.service import TradingService


class TargetStrategy(Protocol):
    def on_bar(self, bar: Bar, current_quantity: int) -> int: ...


@dataclass(frozen=True, slots=True)
class BacktestResult:
    final_cash: Decimal
    final_position: int
    trades: int
    total_fees: Decimal
    data_version: str


class BacktestEngine:
    def __init__(
        self,
        *,
        instrument: Instrument,
        opening_cash: Decimal,
        fees: FeeSchedule,
        limits: RiskLimits,
        trading_rule: TradingRule,
        slippage: Decimal = Decimal("0"),
    ) -> None:
        if slippage < 0:
            raise ValueError("slippage cannot be negative")
        self._instrument = instrument
        if trading_rule.venue != instrument.exchange:
            raise ValueError("trading rule venue does not match instrument")
        self._trading_rule = trading_rule
        self._slippage = slippage
        self.audit = AuditLog()
        self.ledger = Ledger(opening_cash, self.audit)
        self.oms = OrderManager(self.audit)
        self.gateway = PaperGateway(self.oms, self.ledger, self.audit, fees)
        self.service = TradingService(
            RiskEngine(limits, self.audit),
            self.oms,
            self.gateway,
            RuntimeMode.BACKTEST,
        )
        self._trades = 0
        self._fees = Decimal("0")
        self._sellability = SellabilityLedger()
        self._has_run = False

    def run(
        self, bars: list[Bar], strategy: TargetStrategy, *, manifest: DataManifest
    ) -> BacktestResult:
        if self._has_run:
            raise ValueError("BacktestEngine is single-use; create a new instance per run")
        if manifest.quality_status is not QualityStatus.PASSED:
            raise ValueError("backtest requires a PASSED data manifest")
        self._has_run = True
        previous_timestamp = None
        for bar in bars:
            if bar.symbol != self._instrument.symbol or bar.exchange != self._instrument.exchange:
                raise ValueError("bar instrument does not match configured instrument")
            if previous_timestamp is not None and bar.timestamp <= previous_timestamp:
                raise ValueError("bars must be unique and strictly chronological")
            previous_timestamp = bar.timestamp
        pending_target: int | None = None
        for sequence, bar in enumerate(bars):
            current = self.ledger.account.position_quantity(bar.symbol)
            delta = 0 if pending_target is None else pending_target - current
            if delta != 0 and pending_target is not None:
                target_index = sequence + self._trading_rule.sellability_lag_days
                sellable_on = (
                    date.max if target_index >= len(bars) else bars[target_index].trading_day
                )
                self._execute(
                    sequence,
                    bar,
                    pending_target,
                    type(strategy).__name__,
                    sellable_on,
                )
            current = self.ledger.account.position_quantity(bar.symbol)
            pending_target = strategy.on_bar(bar, current)
        return BacktestResult(
            final_cash=self.ledger.account.cash,
            final_position=self.ledger.account.position_quantity(self._instrument.symbol),
            trades=self._trades,
            total_fees=self._fees,
            data_version=manifest.data_version,
        )

    def _execute(
        self, sequence: int, bar: Bar, target: int, strategy_id: str, sellable_on: date
    ) -> None:
        current = self.ledger.account.position_quantity(bar.symbol)
        delta = target - current
        side = Side.BUY if delta > 0 else Side.SELL
        quantity = abs(delta)
        price = bar.open + self._slippage if side is Side.BUY else bar.open - self._slippage
        request = OrderRequest(
            idempotency_key=f"BACKTEST-{sequence}-{bar.symbol}-{target}",
            strategy_id=strategy_id,
            portfolio_id="backtest",
            symbol=bar.symbol,
            side=side,
            price=price,
            quantity=quantity,
            created_at=bar.timestamp,
        )
        context = RiskContext(
            account=self.ledger.account,
            instruments={bar.symbol: self._instrument},
            market_prices={bar.symbol: bar.open},
            market_timestamp=bar.timestamp,
            gateway_connected=True,
            reconciliation_ok=True,
            now=bar.timestamp,
            sellable_quantities={
                bar.symbol: self._sellability.sellable_quantity(bar.symbol, bar.trading_day)
            },
        )
        order, _ = self.service.submit(request, context, now=bar.timestamp)
        if order.rejection_reason is not None:
            return
        trade, created = self.gateway.fill(
            order,
            fill_key=f"BACKTEST-FILL-{sequence}",
            price=price,
            quantity=quantity,
            now=bar.timestamp,
        )
        if created:
            if side is Side.BUY:
                self._sellability.acquire(
                    bar.symbol,
                    quantity,
                    bar.trading_day,
                    sellable_on,
                )
            else:
                self._sellability.dispose(bar.symbol, quantity, bar.trading_day)
            self._trades += 1
            self._fees += trade.fee
