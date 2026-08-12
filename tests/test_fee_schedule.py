from decimal import Decimal

import pytest

from qtrade.domain import Side
from qtrade.execution import CommissionPolicy, FeeSchedule


def test_all_in_commission_does_not_double_count_included_components():
    schedule = FeeSchedule(
        "broker-statement-v1",
        commission_rate=Decimal("0.0003"),
        sell_tax_rate=Decimal("0.0005"),
        minimum_commission=Decimal("5"),
        commission_policy=CommissionPolicy.ALL_IN,
        transfer_rate=Decimal("0.00001"),
        source_url="contract://broker-statement-v1",
    )
    assert schedule.calculate(Side.BUY, Decimal("10"), 1000) == Decimal("5.10")
    assert schedule.calculate(Side.SELL, Decimal("10"), 1000) == Decimal("10.10")


def test_decomposed_commission_adds_explicit_components_once():
    schedule = FeeSchedule(
        "ashare-2023-08-28",
        commission_rate=Decimal("0.0002459"),
        sell_tax_rate=Decimal("0.0005"),
        commission_policy=CommissionPolicy.DECOMPOSED,
        exchange_handling_rate=Decimal("0.0000341"),
        regulatory_rate=Decimal("0.00002"),
        transfer_rate=Decimal("0.00001"),
        source_url="https://www.csrc.gov.cn/csrc/c100028/c7426794/content.shtml",
    )
    assert schedule.calculate(Side.BUY, Decimal("10"), 1000) == Decimal("3.10")
    assert schedule.calculate(Side.SELL, Decimal("10"), 1000) == Decimal("8.10")


def test_decomposed_minimum_applies_only_to_broker_commission():
    schedule = FeeSchedule(
        "small-trade",
        commission_rate=Decimal("0.0001"),
        sell_tax_rate=Decimal("0"),
        minimum_commission=Decimal("5"),
        commission_policy=CommissionPolicy.DECOMPOSED,
        exchange_handling_rate=Decimal("0.0001"),
        regulatory_rate=Decimal("0.0001"),
    )
    breakdown = schedule.breakdown(Side.BUY, Decimal("1"), 1000)
    assert breakdown.broker_commission == Decimal("5.00")
    assert breakdown.exchange_handling == Decimal("0.10")
    assert breakdown.regulatory == Decimal("0.10")
    assert breakdown.total == Decimal("5.20")


def test_all_in_policy_rejects_separately_added_included_components():
    with pytest.raises(ValueError, match="all-in commission"):
        FeeSchedule(
            "invalid",
            Decimal("0.0003"),
            Decimal("0"),
            commission_policy=CommissionPolicy.ALL_IN,
            exchange_handling_rate=Decimal("0.0000341"),
        )
