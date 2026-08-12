from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any

from qtrade.domain import RuntimeMode

SUPPORTED_PROTOCOL_VERSION = "1"
_SHADOW_EVENT_TYPES = frozenset(
    {"TICK", "BAR", "CONTRACT", "ACCOUNT", "POSITION", "GATEWAY_STATUS", "HEARTBEAT"}
)


@dataclass(frozen=True, slots=True)
class VnpyProcessCapabilities:
    protocol_version: str
    vnpy_version: str
    gateway_name: str
    external_order_transmission: bool

    def assert_safe_for_shadow(self) -> None:
        if self.external_order_transmission:
            raise ValueError("shadow adapter cannot transmit external orders")


@dataclass(frozen=True, slots=True)
class CanonicalEvent:
    protocol_version: str
    event_id: str
    sequence: int
    event_type: str
    source: str
    occurred_at: datetime
    received_at: datetime
    payload: dict[str, Any]
    payload_hash: str

    @classmethod
    def create(
        cls,
        *,
        protocol_version: str,
        event_id: str,
        sequence: int,
        event_type: str,
        source: str,
        occurred_at: datetime,
        received_at: datetime,
        payload: dict[str, Any],
    ) -> CanonicalEvent:
        if sequence < 0 or occurred_at.tzinfo is None or received_at.tzinfo is None:
            raise ValueError("event sequence/time is invalid")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return cls(
            protocol_version,
            event_id,
            sequence,
            event_type,
            source,
            occurred_at,
            received_at,
            payload,
            sha256(canonical.encode()).hexdigest(),
        )

    def verify(self) -> bool:
        canonical = json.dumps(self.payload, sort_keys=True, separators=(",", ":"), default=str)
        return sha256(canonical.encode()).hexdigest() == self.payload_hash

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, default=str)


class ShadowEventBridge:
    """Read-only boundary for an isolated vn.py process.

    Capabilities are checked before the connector is invoked. Only observation
    events are admitted and this class deliberately has no order-send method.
    """

    def __init__(
        self,
        capabilities: VnpyProcessCapabilities,
        connector: Callable[[], Iterable[CanonicalEvent]],
    ) -> None:
        if capabilities.protocol_version != SUPPORTED_PROTOCOL_VERSION:
            raise ValueError("unsupported vn.py adapter protocol version")
        capabilities.assert_safe_for_shadow()
        self._capabilities = capabilities
        self._connector = connector
        self._last_sequence = -1
        self._event_ids: set[str] = set()

    @property
    def mode(self) -> RuntimeMode:
        return RuntimeMode.SHADOW

    def poll(self) -> tuple[CanonicalEvent, ...]:
        accepted: list[CanonicalEvent] = []
        for event in self._connector():
            if event.protocol_version != self._capabilities.protocol_version:
                raise ValueError("canonical event protocol mismatch")
            if event.event_type not in _SHADOW_EVENT_TYPES:
                raise ValueError("event type is not permitted in SHADOW bridge")
            if not event.event_id or not event.verify():
                raise ValueError("canonical event integrity validation failed")
            if event.event_id in self._event_ids:
                continue
            if event.sequence <= self._last_sequence:
                raise ValueError("canonical event sequence is not strictly increasing")
            self._event_ids.add(event.event_id)
            self._last_sequence = event.sequence
            accepted.append(event)
        return tuple(accepted)
