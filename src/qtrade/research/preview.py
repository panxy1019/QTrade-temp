from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256

from qtrade.data import Bar
from qtrade.research.multi_asset import DailyMultiAssetBacktester, MomentumAllocation

_MAX_PREVIEW_BARS = 100_000
_MAX_PREVIEW_SESSIONS = 2_500


@dataclass(frozen=True, slots=True)
class BacktestPreviewSpec:
    start: date
    end: date
    horizons: tuple[int, ...] = (20, 60, 120)
    top_k: int = 3
    rebalance_every: int = 20
    reserve: Decimal = Decimal("0.05")
    trend_window: int | None = 120
    volatility_window: int | None = 20
    target_annual_volatility: Decimal | None = Decimal("0.15")
    initial_cash: Decimal = Decimal("1000000")
    transaction_cost_rate: Decimal = Decimal("0.0015")
    slippage_rate: Decimal = Decimal("0.0005")

    def __post_init__(self) -> None:
        if self.start >= self.end or (self.end - self.start).days > 3653:
            raise ValueError("preview date range must be positive and at most ten years")
        if (
            not 1 <= len(self.horizons) <= 3
            or tuple(sorted(set(self.horizons))) != self.horizons
            or any(not 1 <= value <= 252 for value in self.horizons)
        ):
            raise ValueError("preview horizons must be 1-3 unique ascending values in [1, 252]")
        if not 1 <= self.top_k <= 10 or not 1 <= self.rebalance_every <= 120:
            raise ValueError("preview top_k or rebalance interval is out of range")
        if not Decimal("0") <= self.reserve <= Decimal("0.5"):
            raise ValueError("preview reserve must be in [0, 0.5]")
        if self.trend_window is not None and not 2 <= self.trend_window <= 252:
            raise ValueError("preview trend window must be in [2, 252]")
        if (self.volatility_window is None) != (self.target_annual_volatility is None):
            raise ValueError("preview volatility window and target must be configured together")
        if self.volatility_window is not None and not 2 <= self.volatility_window <= 252:
            raise ValueError("preview volatility window must be in [2, 252]")
        if self.target_annual_volatility is not None and not (
            Decimal("0.01") <= self.target_annual_volatility <= Decimal("1")
        ):
            raise ValueError("preview target volatility must be in [0.01, 1]")
        if not Decimal("10000") <= self.initial_cash <= Decimal("100000000"):
            raise ValueError("preview initial cash is out of range")
        for value in (
            self.reserve,
            self.initial_cash,
            self.transaction_cost_rate,
            self.slippage_rate,
        ):
            if not value.is_finite():
                raise ValueError("preview numeric parameters must be finite")
        if not Decimal("0") <= self.transaction_cost_rate <= Decimal("0.05"):
            raise ValueError("preview transaction cost is out of range")
        if not Decimal("0") <= self.slippage_rate <= Decimal("0.05"):
            raise ValueError("preview slippage is out of range")


def run_backtest_preview(
    bars: list[Bar], *, data_version: str, data_checksum: str, spec: BacktestPreviewSpec
) -> dict[str, object]:
    if len(data_checksum) != 64:
        raise ValueError("preview requires a SHA-256 dataset checksum")
    selected = [bar for bar in bars if spec.start <= bar.trading_day <= spec.end]
    sessions = sorted({bar.trading_day for bar in selected})
    symbols = {bar.symbol for bar in selected}
    required_sessions = max(
        max(spec.horizons) + 2,
        (spec.trend_window or 1) + 1,
        (spec.volatility_window or 1) + 2,
    )
    if len(selected) > _MAX_PREVIEW_BARS or len(sessions) > _MAX_PREVIEW_SESSIONS:
        raise ValueError("preview input exceeds the bounded research limit")
    if len(sessions) < required_sessions:
        raise ValueError("preview date range does not contain enough trading sessions")
    if spec.top_k > len(symbols):
        raise ValueError("preview top_k exceeds the available universe")

    model = MomentumAllocation(
        spec.horizons,
        top_k=spec.top_k,
        rebalance_every=spec.rebalance_every,
        reserve=spec.reserve,
        trend_window=spec.trend_window,
        volatility_window=spec.volatility_window,
        target_annual_volatility=spec.target_annual_volatility,
    )
    result = DailyMultiAssetBacktester(
        initial_cash=spec.initial_cash,
        transaction_cost_rate=spec.transaction_cost_rate,
        slippage_rate=spec.slippage_rate,
    ).run(selected, model)
    peak = Decimal("0")
    curve: list[dict[str, str]] = []
    for session, equity in zip(result.sessions, result.equity_curve, strict=True):
        peak = max(peak, equity)
        curve.append(
            {
                "session": session.isoformat(),
                "equity": str(equity),
                "drawdown": str(equity / peak - 1),
            }
        )
    metrics = {
        key: str(value)
        for key, value in asdict(result.metrics).items()
        if key
        in {
            "annualized_return",
            "annualized_volatility",
            "calmar",
            "maximum_drawdown",
            "sharpe",
            "sortino",
            "total_return",
            "win_rate",
        }
    }
    metrics.update(
        {
            "average_exposure": str(result.average_exposure),
            "number_of_trades": str(len(result.trades)),
            "transaction_costs": str(result.total_cost),
            "turnover": str(result.turnover),
        }
    )
    parameters = {
        "start": spec.start.isoformat(),
        "end": spec.end.isoformat(),
        "horizons": list(spec.horizons),
        "top_k": spec.top_k,
        "rebalance_every": spec.rebalance_every,
        "reserve": str(spec.reserve),
        "trend_window": spec.trend_window,
        "volatility_window": spec.volatility_window,
        "target_annual_volatility": (
            None
            if spec.target_annual_volatility is None
            else str(spec.target_annual_volatility)
        ),
        "initial_cash": str(spec.initial_cash),
        "transaction_cost_rate": str(spec.transaction_cost_rate),
        "slippage_rate": str(spec.slippage_rate),
    }
    response: dict[str, object] = {
        "classification": "AD_HOC_PREVIEW_CURRENT_UNIVERSE_NOT_PIT",
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": [
            "CURRENT_UNIVERSE_SURVIVORSHIP_BIAS",
            "AD_HOC_PARAMETERS_NOT_PREDECLARED",
            "NO_INDEPENDENT_HELD_OUT_EVIDENCE",
            "NO_PAPER_OR_SHADOW_SESSIONS",
        ],
        "data_version": data_version,
        "data_checksum": data_checksum,
        "parameters": parameters,
        "session_count": len(result.sessions),
        "universe_size": len(symbols),
        "metrics": metrics,
        "curve": curve,
        "terminal_positions": [
            {"symbol": symbol, "quantity": quantity}
            for symbol, quantity in result.terminal_positions
        ],
        "execution_timing": "close decision, next open execution",
    }
    canonical = json.dumps(response, sort_keys=True, separators=(",", ":"))
    response["preview_hash"] = sha256(canonical.encode()).hexdigest()
    return response
