from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import sqrt
from statistics import mean, pstdev


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    total_return: Decimal
    annualized_return: Decimal
    annualized_volatility: Decimal
    sharpe: Decimal
    sortino: Decimal
    maximum_drawdown: Decimal
    calmar: Decimal
    win_rate: Decimal
    benchmark_return: Decimal
    excess_return: Decimal
    information_ratio: Decimal
    number_of_periods: int


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def evaluate_returns(
    returns: list[Decimal],
    *,
    benchmark_returns: list[Decimal] | None = None,
    periods_per_year: int = 252,
) -> PerformanceMetrics:
    if not returns:
        raise ValueError("returns cannot be empty")
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    benchmark = benchmark_returns or [Decimal("0")] * len(returns)
    if len(benchmark) != len(returns):
        raise ValueError("benchmark length must match returns")
    wealth = Decimal("1")
    benchmark_wealth = Decimal("1")
    peak = Decimal("1")
    max_drawdown = Decimal("0")
    for item, benchmark_item in zip(returns, benchmark, strict=True):
        wealth *= Decimal("1") + item
        benchmark_wealth *= Decimal("1") + benchmark_item
        peak = max(peak, wealth)
        max_drawdown = min(max_drawdown, wealth / peak - Decimal("1"))
    total = wealth - Decimal("1")
    benchmark_total = benchmark_wealth - Decimal("1")
    annual = _decimal(float(wealth) ** (periods_per_year / len(returns)) - 1)
    float_returns = [float(item) for item in returns]
    annual_vol = _decimal(pstdev(float_returns) * sqrt(periods_per_year))
    average = _decimal(mean(float_returns) * periods_per_year)
    sharpe = Decimal("0") if annual_vol == 0 else average / annual_vol
    downside = [min(0.0, item) for item in float_returns]
    downside_deviation = _decimal(
        sqrt(mean(item * item for item in downside)) * sqrt(periods_per_year)
    )
    sortino = Decimal("0") if downside_deviation == 0 else average / downside_deviation
    drawdown_magnitude = abs(max_drawdown)
    calmar = Decimal("0") if drawdown_magnitude == 0 else annual / drawdown_magnitude
    active = [float(item - bench) for item, bench in zip(returns, benchmark, strict=True)]
    tracking_error = _decimal(pstdev(active) * sqrt(periods_per_year))
    active_return = _decimal(mean(active) * periods_per_year)
    information_ratio = Decimal("0") if tracking_error == 0 else active_return / tracking_error
    wins = sum(item > 0 for item in returns)
    return PerformanceMetrics(
        total_return=total,
        annualized_return=annual,
        annualized_volatility=annual_vol,
        sharpe=sharpe,
        sortino=sortino,
        maximum_drawdown=max_drawdown,
        calmar=calmar,
        win_rate=Decimal(wins) / len(returns),
        benchmark_return=benchmark_total,
        excess_return=total - benchmark_total,
        information_ratio=information_ratio,
        number_of_periods=len(returns),
    )
