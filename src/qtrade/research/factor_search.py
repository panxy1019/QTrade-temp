from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from qtrade.data import Bar, BarQualityGate
from qtrade.research.factors import PriceFactorSpec
from qtrade.research.metrics import FactorMetrics, FactorObservation, evaluate_factor_periods


@dataclass(frozen=True, slots=True)
class FactorDiagnostic:
    factor_id: str
    periods: int
    instruments_minimum: int
    metrics: FactorMetrics


def diagnose_price_factors(
    bars: list[Bar],
    factors: tuple[PriceFactorSpec, ...],
    *,
    start: date,
    end: date,
    sample_every: int = 20,
    forward_sessions: int = 20,
    quantiles: int = 5,
) -> tuple[FactorDiagnostic, ...]:
    if start >= end:
        raise ValueError("factor discovery window must be positive")
    if not factors or len({factor.factor_id for factor in factors}) != len(factors):
        raise ValueError("factor definitions must be nonempty and unique")
    if sample_every <= 0 or forward_sessions <= 0:
        raise ValueError("factor sampling and forward horizon must be positive")
    if BarQualityGate().validate(bars):
        raise ValueError("factor diagnostics require quality-passed bars")

    sessions = sorted({bar.trading_day for bar in bars})
    by_session: dict[date, dict[str, Bar]] = defaultdict(dict)
    future_closes: dict[tuple[str, date], Decimal] = {}
    for bar in bars:
        if bar.symbol in by_session[bar.trading_day]:
            raise ValueError("factor diagnostics require one bar per symbol/session")
        by_session[bar.trading_day][bar.symbol] = bar
        future_closes[(bar.symbol, bar.trading_day)] = bar.close

    histories: dict[str, list[Decimal]] = defaultdict(list)
    observations: dict[str, list[list[FactorObservation]]] = {
        factor.factor_id: [] for factor in factors
    }
    for session_index, session in enumerate(sessions):
        for symbol, bar in by_session[session].items():
            histories[symbol].append(bar.close)
        if not start <= session <= end or session_index % sample_every:
            continue
        future_index = session_index + forward_sessions
        if future_index >= len(sessions) or sessions[future_index] > end:
            continue
        future_session = sessions[future_index]
        for factor in factors:
            period: list[FactorObservation] = []
            for symbol in sorted(by_session[session]):
                current = by_session[session][symbol].close
                future = future_closes.get((symbol, future_session))
                history = histories[symbol]
                if future is None or len(history) < factor.required_closes:
                    continue
                period.append(
                    FactorObservation(
                        symbol,
                        factor.value(tuple(history)),
                        future / current - 1,
                        session,
                        by_session[session][symbol].timestamp,
                    )
                )
            if len(period) >= quantiles:
                observations[factor.factor_id].append(period)

    diagnostics: list[FactorDiagnostic] = []
    for factor in factors:
        periods = observations[factor.factor_id]
        if len(periods) < 2:
            raise ValueError(f"factor {factor.factor_id} has insufficient discovery periods")
        diagnostics.append(
            FactorDiagnostic(
                factor.factor_id,
                len(periods),
                min(len(period) for period in periods),
                evaluate_factor_periods(periods, quantiles=quantiles),
            )
        )
    return tuple(diagnostics)
