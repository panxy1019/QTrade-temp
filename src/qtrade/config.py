"""Fail-closed runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from qtrade.domain.models import RuntimeMode


class UnsafeConfiguration(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SafetyConfig:
    runtime_mode: RuntimeMode
    live_trading_enabled: bool = False

    def __post_init__(self) -> None:
        if self.live_trading_enabled:
            raise UnsafeConfiguration("live trading cannot be enabled by this build")

    @classmethod
    def from_environment(cls) -> SafetyConfig:
        raw_enabled = os.getenv("LIVE_TRADING_ENABLED", "false").strip().lower()
        if raw_enabled not in {"false", "0", "no", "off"}:
            raise UnsafeConfiguration("LIVE_TRADING_ENABLED must remain false")
        raw_mode = os.getenv("QTRADE_RUNTIME_MODE", RuntimeMode.PAPER.value).strip().upper()
        try:
            mode = RuntimeMode(raw_mode)
        except ValueError as exc:
            raise UnsafeConfiguration(f"unsupported runtime mode: {raw_mode}") from exc
        return cls(runtime_mode=mode)
