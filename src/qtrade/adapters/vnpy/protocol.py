from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any


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
