from decimal import Decimal

import pytest

from scripts.run_akshare_stock_sector_transfer import _evaluate_transfer_gate


def test_transfer_gate_requires_every_predeclared_criterion():
    passing = _evaluate_transfer_gate(
        yearly_returns=(Decimal("1"),) * 5 + (Decimal("-1"),) * 2,
        sector_returns=(Decimal("1"),) * 9 + (Decimal("-1"),) * 6,
        leave_one_sector_out_returns=(Decimal("1"),) * 12
        + (Decimal("-1"),) * 3,
        doubled_cost_return=Decimal("0.01"),
        bootstrap_probability_positive=Decimal("0.75"),
        maximum_drawdown=Decimal("-0.35"),
    )
    assert passing["passed"] is True

    failed = _evaluate_transfer_gate(
        yearly_returns=(Decimal("1"),) * 4 + (Decimal("-1"),) * 3,
        sector_returns=(Decimal("1"),) * 9 + (Decimal("-1"),) * 6,
        leave_one_sector_out_returns=(Decimal("1"),) * 12
        + (Decimal("-1"),) * 3,
        doubled_cost_return=Decimal("0.01"),
        bootstrap_probability_positive=Decimal("0.75"),
        maximum_drawdown=Decimal("-0.35"),
    )
    assert failed["passed"] is False
    assert failed["criteria"]["positive_years_at_least_5_of_7"] is False


def test_transfer_gate_rejects_incomplete_stress_shapes():
    with pytest.raises(ValueError, match="seven yearly"):
        _evaluate_transfer_gate(
            yearly_returns=(Decimal("1"),),
            sector_returns=(Decimal("1"),) * 15,
            leave_one_sector_out_returns=(Decimal("1"),) * 15,
            doubled_cost_return=Decimal("1"),
            bootstrap_probability_positive=Decimal("1"),
            maximum_drawdown=Decimal("0"),
        )
