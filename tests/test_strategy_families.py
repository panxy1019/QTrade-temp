from decimal import Decimal

from qtrade.portfolio import PortfolioConstraints
from qtrade.strategies import (
    CrossSectionalFactorRanking,
    EtfMomentumRotation,
    EtfTrendRisk,
    MarketSeries,
)


def test_etf_momentum_rotation_is_multi_horizon_deterministic_and_constrained():
    strategy = EtfMomentumRotation(
        (2, 3),
        top_k=2,
        constraints=PortfolioConstraints(
            top_k=3, max_weight=Decimal("0.4"), cash_reserve_fraction=Decimal("0.1")
        ),
    )
    series = (
        MarketSeries("510300", (Decimal("10"), Decimal("10"), Decimal("11"), Decimal("12"))),
        MarketSeries("510500", (Decimal("10"), Decimal("11"), Decimal("11"), Decimal("11"))),
        MarketSeries("159915", (Decimal("10"), Decimal("9"), Decimal("8"), Decimal("7"))),
    )
    scores = strategy.scores(series)
    assert scores["510300"] > scores["510500"] > scores["159915"]
    targets = strategy.targets(
        series,
        prices={"510300": Decimal("12"), "510500": Decimal("11"), "159915": Decimal("7")},
        lot_sizes={"510300": 100, "510500": 100, "159915": 100},
        equity=Decimal("100000"),
    )
    assert [item.symbol for item in targets] == ["510300", "510500"]
    assert all(item.quantity % 100 == 0 for item in targets)


def test_etf_trend_risk_filters_downtrend_and_scales_high_volatility():
    strategy = EtfTrendRisk(
        trend_window=3,
        volatility_window=3,
        target_volatility=Decimal("0.08"),
        maximum_quantity=1000,
    )
    assert (
        strategy.target(
            (Decimal("12"), Decimal("11"), Decimal("10"), Decimal("9")), lot_size=100
        )
        == 0
    )
    smooth = strategy.target(
        (Decimal("10"), Decimal("10.1"), Decimal("10.2"), Decimal("10.3")), lot_size=100
    )
    volatile = strategy.target(
        (Decimal("10"), Decimal("11"), Decimal("9"), Decimal("12")), lot_size=100
    )
    assert smooth == 1000
    assert 0 < volatile < smooth


def test_cross_sectional_factor_ranking_uses_portfolio_constraints():
    strategy = CrossSectionalFactorRanking(
        (("momentum", Decimal("1")), ("volatility", Decimal("-0.5"))),
        PortfolioConstraints(
            top_k=2, max_weight=Decimal("0.45"), cash_reserve_fraction=Decimal("0.1")
        ),
    )
    factors = {
        "600000": {"momentum": Decimal("0.3"), "volatility": Decimal("0.1")},
        "000001": {"momentum": Decimal("0.2"), "volatility": Decimal("0.4")},
        "600519": {"momentum": Decimal("0.25"), "volatility": Decimal("0.1")},
    }
    assert strategy.scores(factors)["600000"] > strategy.scores(factors)["000001"]
    targets = strategy.targets(
        factors,
        prices={symbol: Decimal("10") for symbol in factors},
        lot_sizes={symbol: 100 for symbol in factors},
        equity=Decimal("100000"),
    )
    assert [item.symbol for item in targets] == ["600000", "600519"]
