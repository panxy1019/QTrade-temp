from datetime import UTC, datetime

import pytest

from qtrade.adapters.vnpy import CanonicalEvent, ShadowEventBridge, VnpyProcessCapabilities


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


def event(sequence: int, event_id: str, event_type: str = "TICK") -> CanonicalEvent:
    return CanonicalEvent.create(
        protocol_version="1",
        event_id=event_id,
        sequence=sequence,
        event_type=event_type,
        source="vnpy-test",
        occurred_at=datetime(2026, 8, 12, tzinfo=UTC),
        received_at=datetime(2026, 8, 12, tzinfo=UTC),
        payload={"symbol": "510300", "price": "10.0"},
    )


def test_shadow_bridge_checks_capability_before_connector_start():
    calls = 0

    def connector():
        nonlocal calls
        calls += 1
        return ()

    with pytest.raises(ValueError, match="cannot transmit"):
        ShadowEventBridge(
            VnpyProcessCapabilities("1", "4.4.0", "unsafe", True), connector
        )
    assert calls == 0


def test_shadow_bridge_accepts_read_events_dedupes_and_rejects_commands():
    batches = iter(((event(1, "a"), event(2, "b")), (event(2, "b"), event(3, "c"))))
    bridge = ShadowEventBridge(
        VnpyProcessCapabilities("1", "4.4.0", "market-data-only", False),
        lambda: next(batches),
    )
    assert [item.event_id for item in bridge.poll()] == ["a", "b"]
    assert [item.event_id for item in bridge.poll()] == ["c"]
    unsafe = ShadowEventBridge(
        VnpyProcessCapabilities("1", "4.4.0", "market-data-only", False),
        lambda: (event(1, "order", "SEND_ORDER"),),
    )
    with pytest.raises(ValueError, match="not permitted"):
        unsafe.poll()


def test_shadow_bridge_rejects_out_of_order_or_mutated_events():
    out_of_order = ShadowEventBridge(
        VnpyProcessCapabilities("1", "4.4.0", "market-data-only", False),
        lambda: (event(2, "a"), event(1, "b")),
    )
    with pytest.raises(ValueError, match="strictly increasing"):
        out_of_order.poll()
    mutated = event(1, "tampered")
    mutated.payload["price"] = "999"
    bridge = ShadowEventBridge(
        VnpyProcessCapabilities("1", "4.4.0", "market-data-only", False),
        lambda: (mutated,),
    )
    with pytest.raises(ValueError, match="integrity"):
        bridge.poll()
