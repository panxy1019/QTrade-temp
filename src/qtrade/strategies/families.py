from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from qtrade.portfolio import PortfolioConstraints, TargetPosition, construct_targets
from qtrade.research.factors import momentum


@dataclass(frozen=True, slots=True)
class MarketSeries:
    symbol: str
    closes: tuple[Decimal, ...]
    volumes: tuple[Decimal, ...] = ()

    def __post_init__(self) -> None:
        if not self.symbol or not self.closes:
            raise ValueError("market series requires symbol and close history")
        if any(not value.is_finite() or value <= 0 for value in self.closes):
            raise ValueError("close history must be finite and positive")
        if self.volumes and (
            len(self.volumes) != len(self.closes)
            or any(not value.is_finite() or value < 0 for value in self.volumes)
        ):
            raise ValueError("volume history must align and be finite/nonnegative")


class EtfMomentumRotation:
    def __init__(
        self,
        horizons: tuple[int, ...],
        *,
        top_k: int,
        constraints: PortfolioConstraints,
    ) -> None:
        if not horizons or any(item <= 0 for item in horizons) or top_k <= 0:
            raise ValueError("momentum horizons and top_k must be positive")
        self._horizons = horizons
        self._top_k = top_k
        self._constraints = constraints

    def scores(self, series: tuple[MarketSeries, ...]) -> dict[str, Decimal]:
        if len({item.symbol for item in series}) != len(series):
            raise ValueError("market series symbols must be unique")
        return {
            item.symbol: sum(
                (momentum(list(item.closes), horizon) for horizon in self._horizons),
                start=Decimal("0"),
            )
            / len(self._horizons)
            for item in series
        }

    def targets(
        self,
        series: tuple[MarketSeries, ...],
        *,
        prices: dict[str, Decimal],
        lot_sizes: dict[str, int],
        equity: Decimal,
    ) -> tuple[TargetPosition, ...]:
        constraints = PortfolioConstraints(
            top_k=min(self._top_k, self._constraints.top_k),
            max_weight=self._constraints.max_weight,
            cash_reserve_fraction=self._constraints.cash_reserve_fraction,
        )
        return construct_targets(self.scores(series), prices, lot_sizes, equity, constraints)


class EtfTrendRisk:
    def __init__(
        self,
        *,
        trend_window: int,
        volatility_window: int,
        target_volatility: Decimal,
        maximum_quantity: int,
    ) -> None:
        if (
            trend_window <= 0
            or volatility_window <= 1
            or target_volatility <= 0
            or maximum_quantity <= 0
        ):
            raise ValueError("trend/risk parameters must be positive")
        self._trend_window = trend_window
        self._volatility_window = volatility_window
        self._target_volatility = target_volatility
        self._maximum_quantity = maximum_quantity

    def target(self, closes: tuple[Decimal, ...], *, lot_size: int) -> int:
        required = max(self._trend_window, self._volatility_window + 1)
        if lot_size <= 0 or len(closes) < required:
            return 0
        recent = closes[-self._trend_window :]
        trend = sum(recent, start=Decimal("0")) / len(recent)
        if closes[-1] < trend:
            return 0
        returns = [
            closes[index] / closes[index - 1] - Decimal("1")
            for index in range(len(closes) - self._volatility_window, len(closes))
        ]
        average = sum(returns, start=Decimal("0")) / len(returns)
        variance = sum(((value - average) ** 2 for value in returns), start=Decimal("0"))
        variance /= len(returns)
        volatility = Decimal(str(float(variance) ** 0.5))
        scale = (
            Decimal("1")
            if volatility == 0
            else min(Decimal("1"), self._target_volatility / volatility)
        )
        raw = int(Decimal(self._maximum_quantity) * scale)
        return raw // lot_size * lot_size


class CrossSectionalFactorRanking:
    def __init__(
        self,
        factor_weights: tuple[tuple[str, Decimal], ...],
        constraints: PortfolioConstraints,
    ) -> None:
        total_weight = sum((weight for _, weight in factor_weights), start=Decimal("0"))
        if not factor_weights or total_weight == 0:
            raise ValueError("cross-sectional factor weights cannot be empty or net zero")
        self._factor_weights = factor_weights
        self._constraints = constraints

    def scores(self, factors: dict[str, dict[str, Decimal]]) -> dict[str, Decimal]:
        if not factors:
            raise ValueError("factor cross-section cannot be empty")
        required = {name for name, _ in self._factor_weights}
        missing = {
            symbol: sorted(required - values.keys())
            for symbol, values in factors.items()
            if required - values.keys()
        }
        if missing:
            raise ValueError(f"factor cross-section is incomplete: {missing}")
        return {
            symbol: sum(
                (
                    values[factor_name] * weight
                    for factor_name, weight in self._factor_weights
                ),
                start=Decimal("0"),
            )
            for symbol, values in factors.items()
        }

    def targets(
        self,
        factors: dict[str, dict[str, Decimal]],
        *,
        prices: dict[str, Decimal],
        lot_sizes: dict[str, int],
        equity: Decimal,
    ) -> tuple[TargetPosition, ...]:
        return construct_targets(
            self.scores(factors), prices, lot_sizes, equity, self._constraints
        )
