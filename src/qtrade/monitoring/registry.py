from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


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

    def snapshot(self) -> dict[str, object]:
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
        return {
            "market_data_lag_seconds": str(self.market_data_lag_seconds),
            "event_queue_depth": self.event_queue_depth,
            "orders_total": self.orders_total,
            "reject_rate": str(reject_rate),
            "fill_rate": str(fill_rate),
            "gateway_connected": self.gateway_connected,
            "account_sync_age_seconds": str(self.account_sync_age_seconds),
            "reconciliation_status": self.reconciliation_status,
            "strategy_heartbeats": dict(self.strategy_heartbeats),
        }
