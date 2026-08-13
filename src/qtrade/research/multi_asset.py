from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Protocol

from qtrade.data import Bar, BarQualityGate
from qtrade.research.metrics import PerformanceMetrics, evaluate_returns


@dataclass(frozen=True, slots=True)
class ResearchTrade:
    trading_day: date
    symbol: str
    side: str
    quantity: int
    price: Decimal
    cost: Decimal


@dataclass(frozen=True, slots=True)
class MultiAssetBacktestResult:
    sessions: tuple[date, ...]
    equity_curve: tuple[Decimal, ...]
    returns: tuple[Decimal, ...]
    exposure_curve: tuple[Decimal, ...]
    turnover_curve: tuple[Decimal, ...]
    cost_curve: tuple[Decimal, ...]
    trades: tuple[ResearchTrade, ...]
    terminal_positions: tuple[tuple[str, int], ...]
    turnover: Decimal
    total_cost: Decimal
    average_exposure: Decimal
    metrics: PerformanceMetrics
    maximum_stale_sessions: int


class AllocationModel(Protocol):
    def weights(
        self,
        history: dict[str, tuple[Decimal, ...]],
        *,
        session_index: int,
    ) -> dict[str, Decimal] | None: ...


class EqualWeightBuyHold:
    def __init__(self, symbols: tuple[str, ...], *, reserve: Decimal = Decimal("0")) -> None:
        if not symbols or len(set(symbols)) != len(symbols):
            raise ValueError("buy-and-hold symbols must be nonempty and unique")
        if not Decimal("0") <= reserve < Decimal("1"):
            raise ValueError("reserve must be in [0, 1)")
        self._symbols = symbols
        self._reserve = reserve

    def weights(
        self,
        history: dict[str, tuple[Decimal, ...]],
        *,
        session_index: int,
    ) -> dict[str, Decimal] | None:
        if session_index != 0:
            return None
        eligible = [symbol for symbol in self._symbols if symbol in history]
        if not eligible:
            return {}
        weight = (Decimal("1") - self._reserve) / len(eligible)
        return {symbol: weight for symbol in eligible}


class MomentumAllocation:
    def __init__(
        self,
        horizons: tuple[int, ...],
        *,
        top_k: int,
        rebalance_every: int = 20,
        reserve: Decimal = Decimal("0.05"),
        trend_window: int | None = None,
        volatility_window: int | None = None,
        target_annual_volatility: Decimal | None = None,
    ) -> None:
        if not horizons or any(value <= 0 for value in horizons):
            raise ValueError("momentum horizons must be positive")
        if top_k <= 0 or rebalance_every <= 0:
            raise ValueError("top_k and rebalance interval must be positive")
        if not Decimal("0") <= reserve < Decimal("1"):
            raise ValueError("reserve must be in [0, 1)")
        if trend_window is not None and trend_window <= 0:
            raise ValueError("trend window must be positive")
        if (volatility_window is None) != (target_annual_volatility is None):
            raise ValueError("volatility window and target must be configured together")
        if volatility_window is not None and volatility_window <= 1:
            raise ValueError("volatility window must exceed one")
        if target_annual_volatility is not None and target_annual_volatility <= 0:
            raise ValueError("target volatility must be positive")
        self._horizons = horizons
        self._top_k = top_k
        self._rebalance_every = rebalance_every
        self._reserve = reserve
        self._trend_window = trend_window
        self._volatility_window = volatility_window
        self._target_volatility = target_annual_volatility

    def weights(
        self,
        history: dict[str, tuple[Decimal, ...]],
        *,
        session_index: int,
    ) -> dict[str, Decimal] | None:
        required = max(
            max(self._horizons) + 1,
            self._trend_window or 1,
            (self._volatility_window or 0) + 1,
        )
        if session_index % self._rebalance_every != 0:
            return None
        ranked: list[tuple[str, Decimal, Decimal]] = []
        for symbol, closes in history.items():
            if len(closes) < required:
                continue
            if self._trend_window is not None:
                average = sum(closes[-self._trend_window :], start=Decimal("0"))
                average /= self._trend_window
                if closes[-1] < average:
                    continue
            score = sum(
                (closes[-1] / closes[-horizon - 1] - Decimal("1") for horizon in self._horizons),
                start=Decimal("0"),
            ) / len(self._horizons)
            if score <= 0:
                continue
            scale = Decimal("1")
            if self._volatility_window is not None and self._target_volatility is not None:
                values = [
                    closes[index] / closes[index - 1] - Decimal("1")
                    for index in range(
                        len(closes) - self._volatility_window,
                        len(closes),
                    )
                ]
                average_return = sum(values, start=Decimal("0")) / len(values)
                variance = sum(
                    ((value - average_return) ** 2 for value in values),
                    start=Decimal("0"),
                ) / len(values)
                annual_volatility = Decimal(str(float(variance * 252) ** 0.5))
                if annual_volatility > 0:
                    scale = min(Decimal("1"), self._target_volatility / annual_volatility)
            ranked.append((symbol, score, scale))
        selected = sorted(ranked, key=lambda value: (-value[1], value[0]))[: self._top_k]
        if not selected:
            return {}
        base = (Decimal("1") - self._reserve) / len(selected)
        return {symbol: base * scale for symbol, _, scale in selected}


class DailyMultiAssetBacktester:
    """Causal close-decision/next-open research simulator with whole-lot rebalancing."""

    def __init__(
        self,
        *,
        initial_cash: Decimal = Decimal("1000000"),
        lot_size: int = 100,
        transaction_cost_rate: Decimal = Decimal("0.001"),
        slippage_rate: Decimal = Decimal("0.0005"),
        maximum_stale_sessions: int = 1,
    ) -> None:
        values = (initial_cash, transaction_cost_rate, slippage_rate)
        if any(not value.is_finite() for value in values) or initial_cash <= 0:
            raise ValueError("backtest numeric configuration must be finite")
        if lot_size <= 0 or transaction_cost_rate < 0 or slippage_rate < 0:
            raise ValueError("lot size and cost configuration are invalid")
        if maximum_stale_sessions < 0:
            raise ValueError("maximum stale sessions cannot be negative")
        self._initial_cash = initial_cash
        self._lot_size = lot_size
        self._cost_rate = transaction_cost_rate
        self._slippage_rate = slippage_rate
        self._maximum_stale_sessions = maximum_stale_sessions

    def run(self, bars: list[Bar], model: AllocationModel) -> MultiAssetBacktestResult:
        if BarQualityGate().validate(bars):
            raise ValueError("multi-asset backtest requires quality-passed bars")
        grouped: dict[date, dict[str, Bar]] = defaultdict(dict)
        for bar in bars:
            if bar.symbol in grouped[bar.trading_day]:
                raise ValueError("one daily bar per symbol/session is required")
            grouped[bar.trading_day][bar.symbol] = bar
        sessions = tuple(sorted(grouped))
        if len(sessions) < 2:
            raise ValueError("multi-asset backtest requires at least two sessions")

        cash = self._initial_cash
        positions: dict[str, int] = {}
        last_closes: dict[str, Decimal] = {}
        stale_sessions: dict[str, int] = {}
        history: dict[str, list[Decimal]] = defaultdict(list)
        pending: dict[str, Decimal] | None = None
        trades: list[ResearchTrade] = []
        equities: list[Decimal] = []
        returns: list[Decimal] = []
        exposures: list[Decimal] = []
        turnover_curve: list[Decimal] = []
        cost_curve: list[Decimal] = []
        total_turnover = Decimal("0")
        total_cost = Decimal("0")
        previous_equity = self._initial_cash

        for session_index, trading_day in enumerate(sessions):
            daily = grouped[trading_day]
            session_turnover = Decimal("0")
            session_cost = Decimal("0")
            if pending is not None:
                equity_at_open = cash + sum(
                    Decimal(quantity)
                    * (daily[symbol].open if symbol in daily else last_closes[symbol])
                    for symbol, quantity in positions.items()
                    if symbol in daily or symbol in last_closes
                )
                desired = {
                    symbol: int(
                        (
                            equity_at_open * weight / (daily[symbol].open * self._lot_size)
                        ).to_integral_value(rounding=ROUND_DOWN)
                    )
                    * self._lot_size
                    for symbol, weight in pending.items()
                    if symbol in daily and weight >= 0
                }
                for symbol in sorted(set(positions) | set(desired)):
                    current = positions.get(symbol, 0)
                    target = desired.get(symbol, 0)
                    if target >= current or symbol not in daily:
                        continue
                    quantity = current - target
                    price = daily[symbol].open * (Decimal("1") - self._slippage_rate)
                    notional = price * quantity
                    cost = notional * self._cost_rate
                    cash += notional - cost
                    positions[symbol] = target
                    total_turnover += notional
                    total_cost += cost
                    session_turnover += notional
                    session_cost += cost
                    trades.append(
                        ResearchTrade(trading_day, symbol, "SELL", quantity, price, cost)
                    )
                for symbol in sorted(desired):
                    current = positions.get(symbol, 0)
                    requested = max(0, desired[symbol] - current)
                    if requested == 0:
                        continue
                    price = daily[symbol].open * (Decimal("1") + self._slippage_rate)
                    unit_cash = price * self._lot_size * (Decimal("1") + self._cost_rate)
                    affordable_lots = int((cash / unit_cash).to_integral_value(rounding=ROUND_DOWN))
                    quantity = min(requested, affordable_lots * self._lot_size)
                    if quantity == 0:
                        continue
                    notional = price * quantity
                    cost = notional * self._cost_rate
                    cash -= notional + cost
                    positions[symbol] = current + quantity
                    total_turnover += notional
                    total_cost += cost
                    session_turnover += notional
                    session_cost += cost
                    trades.append(
                        ResearchTrade(trading_day, symbol, "BUY", quantity, price, cost)
                    )

            for symbol in tuple(positions):
                if positions[symbol] == 0:
                    stale_sessions.pop(symbol, None)
                elif symbol in daily:
                    stale_sessions[symbol] = 0
                else:
                    stale_sessions[symbol] = stale_sessions.get(symbol, 0) + 1
                    if stale_sessions[symbol] > self._maximum_stale_sessions:
                        raise ValueError(
                            f"holding {symbol} exceeded stale valuation policy"
                        )
            last_closes.update({symbol: bar.close for symbol, bar in daily.items()})
            close_value = sum(
                Decimal(quantity) * last_closes[symbol]
                for symbol, quantity in positions.items()
                if symbol in last_closes
            )
            equity = cash + close_value
            if equity <= 0:
                raise ValueError("research simulation exhausted capital")
            equities.append(equity)
            returns.append(equity / previous_equity - Decimal("1"))
            exposures.append(close_value / equity)
            turnover_curve.append(session_turnover / self._initial_cash)
            cost_curve.append(session_cost / self._initial_cash)
            previous_equity = equity
            for symbol, bar in daily.items():
                history[symbol].append(bar.close)
            pending = model.weights(
                {symbol: tuple(values) for symbol, values in history.items()},
                session_index=session_index,
            )
            if pending is not None and (
                any(not value.is_finite() or value < 0 for value in pending.values())
                or sum(pending.values(), start=Decimal("0")) > Decimal("1")
            ):
                raise ValueError("allocation weights must be finite, nonnegative and sum <= 1")

        turnover = total_turnover / self._initial_cash
        average_exposure = sum(exposures, start=Decimal("0")) / len(exposures)
        metrics = evaluate_returns(
            returns,
            turnover=turnover,
            exposure=average_exposure,
            transaction_costs=total_cost / self._initial_cash,
        )
        return MultiAssetBacktestResult(
            sessions,
            tuple(equities),
            tuple(returns),
            tuple(exposures),
            tuple(turnover_curve),
            tuple(cost_curve),
            tuple(trades),
            tuple(sorted((symbol, quantity) for symbol, quantity in positions.items() if quantity)),
            turnover,
            total_cost,
            average_exposure,
            metrics,
            self._maximum_stale_sessions,
        )


def evaluate_backtest_window(
    result: MultiAssetBacktestResult,
    start: date,
    end: date,
) -> PerformanceMetrics:
    indices = [
        index for index, session in enumerate(result.sessions) if start <= session <= end
    ]
    if not indices:
        raise ValueError("backtest evaluation window has no observations")
    returns = [result.returns[index] for index in indices]
    exposure = sum(
        (result.exposure_curve[index] for index in indices), start=Decimal("0")
    ) / len(indices)
    turnover = sum(
        (result.turnover_curve[index] for index in indices), start=Decimal("0")
    )
    costs = sum((result.cost_curve[index] for index in indices), start=Decimal("0"))
    trade_count = sum(start <= trade.trading_day <= end for trade in result.trades)
    return evaluate_returns(
        returns,
        exposure=exposure,
        turnover=turnover,
        trade_count=trade_count,
        transaction_costs=costs,
    )


def write_experiment_artifacts(
    result: MultiAssetBacktestResult,
    root: Path,
) -> tuple[Path, ...]:
    root.mkdir(parents=True, exist_ok=False)
    peak = Decimal("0")
    drawdowns: list[dict[str, str]] = []
    for session, equity in zip(result.sessions, result.equity_curve, strict=True):
        peak = max(peak, equity)
        drawdowns.append({"session": str(session), "drawdown": str(equity / peak - 1)})
    payloads: dict[str, object] = {
        "equity": [
            {"session": str(session), "equity": str(equity)}
            for session, equity in zip(result.sessions, result.equity_curve, strict=True)
        ],
        "drawdown": drawdowns,
        "trades": [asdict(trade) for trade in result.trades],
        "positions": dict(result.terminal_positions),
        "risk": {
            "average_exposure": result.average_exposure,
            "maximum_drawdown": result.metrics.maximum_drawdown,
            "turnover": result.turnover,
            "transaction_costs": result.total_cost,
            "maximum_stale_sessions": result.maximum_stale_sessions,
        },
        "logs": {
            "engine": "DailyMultiAssetBacktester",
            "timing": "close decision, next open execution",
            "session_count": len(result.sessions),
        },
    }
    paths: list[Path] = []
    for name, payload in payloads.items():
        target = root / f"{name}.json"
        target.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str),
            encoding="utf-8",
        )
        paths.append(target)
    return tuple(paths)
