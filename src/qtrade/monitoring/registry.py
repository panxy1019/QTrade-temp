from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Lock


@dataclass(frozen=True, slots=True)
class HealthStatus:
    healthy: bool
    runtime_mode: str
    risk_state: str
    gateway_connected: bool
    reconciliation_status: str


@dataclass(slots=True)
class MetricsRegistry:
    market_data_lag_seconds: Decimal = Decimal("0")
    event_queue_depth: int = 0
    orders_total: int = 0
    rejects_total: int = 0
    fills_total: int = 0
    gateway_connected: bool = False
    account_sync_age_seconds: Decimal = Decimal("0")
    reconciliation_status: str = "UNKNOWN"
    strategy_heartbeats: dict[str, str] = field(default_factory=dict)
    last_order_latency_seconds: Decimal = Decimal("0")
    _order_times: list[datetime] = field(default_factory=list, repr=False)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def record_order(self, *, created_at: datetime, completed_at: datetime) -> None:
        if created_at.tzinfo is None or completed_at.tzinfo is None:
            raise ValueError("metric timestamps must be timezone-aware")
        latency = Decimal(str(max(0.0, (completed_at - created_at).total_seconds())))
        with self._lock:
            self.orders_total += 1
            self.last_order_latency_seconds = latency
            self._order_times.append(completed_at.astimezone(UTC))

    def record_reject(self) -> None:
        with self._lock:
            self.rejects_total += 1

    def record_fill(self) -> None:
        with self._lock:
            self.fills_total += 1

    def snapshot(self, *, now: datetime | None = None) -> dict[str, object]:
        with self._lock:
            fill_rate = (
                Decimal("0")
                if self.orders_total == 0
                else Decimal(self.fills_total) / self.orders_total
            )
            reject_rate = (
                Decimal("0")
                if self.orders_total == 0
                else Decimal(self.rejects_total) / self.orders_total
            )
            cutoff = (now or datetime.now(UTC)) - timedelta(minutes=1)
            self._order_times[:] = [
                timestamp for timestamp in self._order_times if timestamp >= cutoff
            ]
            return {
                "market_data_lag_seconds": str(self.market_data_lag_seconds),
                "event_queue_depth": self.event_queue_depth,
                "orders_total": self.orders_total,
                "rejects_total": self.rejects_total,
                "fills_total": self.fills_total,
                "orders_per_minute": len(self._order_times),
                "reject_rate": str(reject_rate),
                "fill_rate": str(fill_rate),
                "last_order_latency_seconds": str(self.last_order_latency_seconds),
                "gateway_connected": self.gateway_connected,
                "account_sync_age_seconds": str(self.account_sync_age_seconds),
                "reconciliation_status": self.reconciliation_status,
                "strategy_heartbeats": dict(self.strategy_heartbeats),
            }
