from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock

from qtrade.market_data.models import CanonicalTick


@dataclass(frozen=True, slots=True)
class MarketDataReadinessAttestation:
    provider: str
    session_nonce: str
    reconnect_id: str
    data_version: str
    issued_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.provider,
                self.session_nonce,
                self.reconnect_id,
                self.data_version,
            )
        ):
            raise ValueError("market-data readiness identity is required")
        if self.issued_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("market-data readiness timestamps must be timezone-aware")
        if self.expires_at <= self.issued_at:
            raise ValueError("market-data readiness must expire after issue")


class MarketDataReadinessGate:
    """Revocable authority issued only after provider and quality reconciliation."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._attestation: MarketDataReadinessAttestation | None = None
        self._reason = "market data has not been attested"

    def attest(self, value: MarketDataReadinessAttestation) -> None:
        with self._lock:
            self._attestation = value
            self._reason = ""

    def invalidate(self, reason: str) -> None:
        if not reason.strip():
            raise ValueError("market-data invalidation requires a reason")
        with self._lock:
            self._attestation = None
            self._reason = reason

    def require(self, tick: CanonicalTick) -> None:
        with self._lock:
            value = self._attestation
            reason = self._reason
        if value is None:
            raise RuntimeError(reason)
        if not value.issued_at <= tick.timestamp_received <= value.expires_at:
            raise RuntimeError("market-data readiness attestation is stale")
        if (
            tick.provider != value.provider
            or tick.reconnect_id != value.reconnect_id
            or tick.data_version != value.data_version
        ):
            raise RuntimeError("tick does not match market-data readiness identity")
