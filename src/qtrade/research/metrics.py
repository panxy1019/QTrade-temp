from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
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
    turnover: Decimal
    profit_loss_ratio: Decimal
    exposure: Decimal
    number_of_trades: int
    transaction_costs: Decimal


@dataclass(frozen=True, slots=True)
class FactorMetrics:
    ic: Decimal
    rank_ic: Decimal
    icir: Decimal
    quantile_returns: tuple[Decimal, ...]
    factor_turnover: Decimal
    long_short_spread: Decimal


@dataclass(frozen=True, slots=True)
class FactorObservation:
    instrument_id: str
    factor_value: Decimal
    forward_return: Decimal
    as_of: date
    known_at: datetime

    def __post_init__(self) -> None:
        if not self.instrument_id:
            raise ValueError("factor observation requires instrument identity")
        if self.known_at.tzinfo is None:
            raise ValueError("factor known_at must be timezone-aware")
        if self.known_at.date() > self.as_of:
            raise ValueError("factor observation cannot be future-known")
        if not self.factor_value.is_finite() or not self.forward_return.is_finite():
            raise ValueError("factor observation values must be finite")


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def evaluate_returns(
    returns: list[Decimal],
    *,
    benchmark_returns: list[Decimal] | None = None,
    periods_per_year: int = 252,
    turnover: Decimal = Decimal("0"),
    exposure: Decimal = Decimal("0"),
    trade_pnls: list[Decimal] | None = None,
    transaction_costs: Decimal = Decimal("0"),
) -> PerformanceMetrics:
    if not returns:
        raise ValueError("returns cannot be empty")
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    if any(not item.is_finite() or item <= Decimal("-1") for item in returns):
        raise ValueError("returns must be finite and greater than -1")
    if turnover < 0 or not Decimal("0") <= exposure <= Decimal("1"):
        raise ValueError("turnover must be nonnegative and exposure in [0, 1]")
    if transaction_costs < 0:
        raise ValueError("transaction costs cannot be negative")
    benchmark = [Decimal("0")] * len(returns) if benchmark_returns is None else benchmark_returns
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
    pnl_values = trade_pnls or []
    gains = [item for item in pnl_values if item > 0]
    losses = [abs(item) for item in pnl_values if item < 0]
    profit_loss_ratio = (
        Decimal("0")
        if not gains or not losses
        else (sum(gains, start=Decimal("0")) / len(gains))
        / (sum(losses, start=Decimal("0")) / len(losses))
    )
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
        turnover=turnover,
        profit_loss_ratio=profit_loss_ratio,
        exposure=exposure,
        number_of_trades=len(pnl_values),
        transaction_costs=transaction_costs,
    )


def _pearson(left: list[Decimal], right: list[Decimal]) -> Decimal:
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("metric series must have equal length of at least two")
    left_mean = sum(left, start=Decimal("0")) / len(left)
    right_mean = sum(right, start=Decimal("0")) / len(right)
    covariance = sum(
        ((x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True)),
        start=Decimal("0"),
    )
    left_variance = sum(((x - left_mean) ** 2 for x in left), start=Decimal("0"))
    right_variance = sum(((y - right_mean) ** 2 for y in right), start=Decimal("0"))
    denominator = _decimal(sqrt(float(left_variance * right_variance)))
    return Decimal("0") if denominator == 0 else covariance / denominator


def _ranks(values: list[Decimal]) -> list[Decimal]:
    ordered = sorted((value, index) for index, value in enumerate(values))
    ranks = [Decimal("0")] * len(values)
    position = 0
    while position < len(ordered):
        end = position + 1
        while end < len(ordered) and ordered[end][0] == ordered[position][0]:
            end += 1
        average_rank = Decimal(position + 1 + end) / 2
        for _, original_index in ordered[position:end]:
            ranks[original_index] = average_rank
        position = end
    return ranks


def evaluate_factor(
    factor_values: list[Decimal],
    forward_returns: list[Decimal],
    *,
    previous_factor_values: list[Decimal] | None = None,
    quantiles: int = 5,
) -> FactorMetrics:
    if len(factor_values) != len(forward_returns):
        raise ValueError("factor values and forward returns must align")
    if quantiles < 2 or len(factor_values) < quantiles:
        raise ValueError("quantiles require at least two nonempty groups")
    if any(not item.is_finite() for item in factor_values + forward_returns):
        raise ValueError("factor inputs must be finite")
    ic = _pearson(factor_values, forward_returns)
    rank_ic = _pearson(_ranks(factor_values), _ranks(forward_returns))
    ordered_returns = [
        item[1]
        for item in sorted(
            zip(factor_values, forward_returns, strict=True), key=lambda item: item[0]
        )
    ]
    groups: list[Decimal] = []
    for group in range(quantiles):
        start = group * len(ordered_returns) // quantiles
        end = (group + 1) * len(ordered_returns) // quantiles
        chunk = ordered_returns[start:end]
        groups.append(sum(chunk, start=Decimal("0")) / len(chunk))
    turnover_value = Decimal("0")
    if previous_factor_values is not None:
        if len(previous_factor_values) != len(factor_values):
            raise ValueError("previous factor values must align")
        current_top = set(
            sorted(range(len(factor_values)), key=factor_values.__getitem__)[
                -len(factor_values) // quantiles :
            ]
        )
        previous_top = set(
            sorted(range(len(previous_factor_values)), key=previous_factor_values.__getitem__)[
                -len(previous_factor_values) // quantiles :
            ]
        )
        turnover_value = Decimal(len(current_top - previous_top)) / len(current_top)
    return FactorMetrics(
        ic=ic,
        rank_ic=rank_ic,
        icir=Decimal("0"),
        quantile_returns=tuple(groups),
        factor_turnover=turnover_value,
        long_short_spread=groups[-1] - groups[0],
    )


def evaluate_factor_periods(
    observations: list[list[FactorObservation]], *, quantiles: int = 5
) -> FactorMetrics:
    if len(observations) < 2:
        raise ValueError("ICIR requires at least two aligned periods")
    periods: list[FactorMetrics] = []
    top_buckets: list[set[str]] = []
    for period in observations:
        if not period:
            raise ValueError("factor periods cannot be empty")
        as_of_dates = {item.as_of for item in period}
        identities = [item.instrument_id for item in period]
        if len(as_of_dates) != 1 or len(set(identities)) != len(identities):
            raise ValueError("each factor period needs one as_of and unique instruments")
        ordered = sorted(period, key=lambda item: item.instrument_id)
        periods.append(
            evaluate_factor(
                [item.factor_value for item in ordered],
                [item.forward_return for item in ordered],
                quantiles=quantiles,
            )
        )
        bucket_size = len(ordered) // quantiles
        if bucket_size == 0:
            raise ValueError("quantiles require nonempty top buckets")
        top_buckets.append(
            {
                item.instrument_id
                for item in sorted(
                    ordered, key=lambda item: (item.factor_value, item.instrument_id)
                )[-bucket_size:]
            }
        )
    count = Decimal(len(periods))
    ics = [period.ic for period in periods]
    average_ic = sum(ics, start=Decimal("0")) / count
    ic_deviation = Decimal(str(pstdev(float(value) for value in ics)))
    group_count = len(periods[0].quantile_returns)
    quantile_returns = tuple(
        sum((period.quantile_returns[index] for period in periods), start=Decimal("0"))
        / count
        for index in range(group_count)
    )
    return FactorMetrics(
        ic=average_ic,
        rank_ic=sum((period.rank_ic for period in periods), start=Decimal("0")) / count,
        icir=Decimal("0") if ic_deviation == 0 else average_ic / ic_deviation,
        quantile_returns=quantile_returns,
        factor_turnover=sum(
            (
                Decimal(len(current - previous)) / len(current)
                for previous, current in zip(
                    top_buckets[:-1], top_buckets[1:], strict=True
                )
            ),
            start=Decimal("0"),
        )
        / Decimal(len(top_buckets) - 1),
        long_short_spread=quantile_returns[-1] - quantile_returns[0],
    )
