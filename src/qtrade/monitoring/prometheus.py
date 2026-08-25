from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

_RUNTIME_METRICS = (
    ("market_data_lag_seconds", "gauge", "Age of the latest accepted market observation."),
    ("event_queue_depth", "gauge", "Current internal event queue depth."),
    ("orders_total", "counter", "Total unique PAPER order attempts."),
    ("rejects_total", "counter", "Total rejected PAPER order attempts."),
    ("fills_total", "counter", "Total newly applied PAPER fills."),
    ("cancels_total", "counter", "Total newly applied PAPER cancellations."),
    ("orders_per_minute", "gauge", "Unique PAPER order attempts in the last minute."),
    ("reject_rate", "gauge", "Rejected PAPER order attempts divided by order attempts."),
    ("fill_rate", "gauge", "Applied PAPER fills divided by order attempts."),
    ("last_order_latency_seconds", "gauge", "Latency of the latest PAPER order dispatch."),
    ("monitoring_degraded", "gauge", "Whether post-effect monitoring is degraded."),
    ("gateway_connected", "gauge", "Whether the configured PAPER gateway is connected."),
    ("account_sync_age_seconds", "gauge", "Age of the latest account reconciliation."),
)


def render_prometheus_metrics(
    snapshot: Mapping[str, object],
    *,
    runtime_mode: str,
    risk_state: str,
    reconciliation_status: str,
    runtime_running: bool,
    healthy: bool,
) -> str:
    lines: list[str] = []
    for key, metric_type, help_text in _RUNTIME_METRICS:
        name = f"qtrade_{key}"
        lines.extend(
            (
                f"# HELP {name} {help_text}",
                f"# TYPE {name} {metric_type}",
                f"{name} {_prometheus_number(snapshot.get(key))}",
            )
        )
    lines.extend(
        (
            "# HELP qtrade_runtime_running Whether the PAPER or SHADOW runtime is running.",
            "# TYPE qtrade_runtime_running gauge",
            f"qtrade_runtime_running {int(runtime_running)}",
            "# HELP qtrade_runtime_healthy Whether runtime health gates currently pass.",
            "# TYPE qtrade_runtime_healthy gauge",
            f"qtrade_runtime_healthy {int(healthy)}",
            "# HELP qtrade_runtime_info Current bounded runtime identity and safety state.",
            "# TYPE qtrade_runtime_info gauge",
            "qtrade_runtime_info{"
            f'mode="{_escape_label(runtime_mode)}",'
            f'risk_state="{_escape_label(risk_state)}",'
            f'reconciliation_status="{_escape_label(reconciliation_status)}"'
            "} 1",
        )
    )
    return "\n".join(lines) + "\n"


def _prometheus_number(value: object) -> str:
    if isinstance(value, bool):
        return str(int(value))
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("runtime metric is not numeric") from exc
    if not number.is_finite():
        raise ValueError("runtime metric is not finite")
    return str(number)


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
