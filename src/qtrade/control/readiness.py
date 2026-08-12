from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class ReadinessAttestation:
    attestation_id: str
    issued_at: datetime
    expires_at: datetime
    gateway_connected: bool
    reconciliation_ok: bool
    uncertain_fills: int

    def validate(self, now: datetime) -> None:
        if now.tzinfo is None or self.issued_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("readiness timestamps must be timezone-aware")
        if now > self.expires_at or now < self.issued_at - timedelta(seconds=5):
            raise ValueError("readiness attestation is expired or not yet valid")
        if not self.gateway_connected or not self.reconciliation_ok:
            raise ValueError("readiness gateway/reconciliation checks failed")
        if self.uncertain_fills:
            raise ValueError("readiness blocked by uncertain fills")
