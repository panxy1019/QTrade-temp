from datetime import UTC, datetime

import pytest

from qtrade.broker_bridge import BrokerCapabilities, BrokerStatus
from qtrade.broker_bridge.models import BrokerConnectionState, ReadOnlyBrokerBridge


def test_read_only_broker_capabilities_fail_closed():
    with pytest.raises(ValueError, match="cannot transmit"):
        BrokerCapabilities(
            "1",
            "XTP",
            "2.2.32.2.3",
            "2.2.32.2",
            True,
            True,
            True,
            True,
            external_order_transmission=True,
        )
    with pytest.raises(ValueError, match="cannot transmit"):
        BrokerCapabilities(
            "1", "TORA", "2026.8.2", "4.1.6", True, True, True, True, can_cancel=True
        )


def test_read_only_protocol_has_queries_but_no_mutation_commands():
    members = set(ReadOnlyBrokerBridge.__dict__)
    assert {"query_accounts", "query_positions", "query_orders", "query_trades"} <= members
    assert not any(
        "send" in member or "cancel" in member or "amend" in member
        for member in members
    )
    status = BrokerStatus(
        "XTP",
        BrokerConnectionState.RECOVERING,
        3,
        datetime(2026, 8, 12, tzinfo=UTC),
        None,
    )
    assert status.state is BrokerConnectionState.RECOVERING
