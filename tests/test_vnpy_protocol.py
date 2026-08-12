from datetime import UTC, datetime

import pytest

from qtrade.adapters.vnpy import CanonicalEvent, VnpyProcessCapabilities


def test_canonical_event_detects_payload_mutation():
    event = CanonicalEvent.create(
        protocol_version="1",
        event_id="event-1",
        sequence=1,
        event_type="TICK",
        source="vnpy-test",
        occurred_at=datetime(2026, 8, 12, tzinfo=UTC),
        received_at=datetime(2026, 8, 12, tzinfo=UTC),
        payload={"symbol": "510300", "price": "10.0"},
    )
    assert event.verify() is True
    event.payload["price"] = "999"
    assert event.verify() is False


def test_shadow_capabilities_reject_external_transmission():
    safe = VnpyProcessCapabilities("1", "4.4.0", "market-data-only", False)
    safe.assert_safe_for_shadow()
    unsafe = VnpyProcessCapabilities("1", "4.4.0", "broker", True)
    with pytest.raises(ValueError, match="cannot transmit"):
        unsafe.assert_safe_for_shadow()
