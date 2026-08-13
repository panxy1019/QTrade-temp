from decimal import Decimal

import pytest

from qtrade.api.app import _validated_backtest_risk


def test_backtest_risk_recomputes_turnover_and_cost_policy() -> None:
    curve = [{"session": "2026-01-05", "equity": "100", "drawdown": "0"}]
    trades: list[dict[str, object]] = [
        {
            "symbol": "510300",
            "side": "BUY",
            "quantity": 1,
            "price": "10",
            "notional": "10",
            "cost": "0.01",
        }
    ]
    risk: dict[str, object] = {
        "maximum_drawdown": "0",
        "transaction_costs": "0.01",
        "turnover": "0",
    }
    with pytest.raises(ValueError, match="invalid backtest risk artifact"):
        _validated_backtest_risk(risk, curve, trades, Decimal("0.001"))

    risk["turnover"] = "0.1"
    trades[0]["cost"] = "0"
    risk["transaction_costs"] = "0"
    with pytest.raises(ValueError, match="invalid backtest cost policy"):
        _validated_backtest_risk(risk, curve, trades, Decimal("0.001"))
