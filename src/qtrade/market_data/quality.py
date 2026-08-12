from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from qtrade.market_data.models import CanonicalTick, TickQualityIssue


class TickQualityGate:
    def __init__(
        self,
        *,
        maximum_staleness: timedelta = timedelta(seconds=5),
        maximum_clock_skew: timedelta = timedelta(seconds=2),
        state_path: Path | None = None,
    ) -> None:
        if maximum_staleness <= timedelta(0) or maximum_clock_skew < timedelta(0):
            raise ValueError("tick quality time limits are invalid")
        self._maximum_staleness = maximum_staleness
        self._maximum_clock_skew = maximum_clock_skew
        self._seen: set[str] = set()
        self._previous: dict[tuple[str, str, str], CanonicalTick] = {}
        self._reconnect_id: dict[tuple[str, str], str] = {}
        self._state_path = None if state_path is None else state_path.resolve()
        if self._state_path is not None and self._state_path.is_file():
            self._restore_state()

    def validate(self, ticks: tuple[CanonicalTick, ...]) -> tuple[TickQualityIssue, ...]:
        issues: list[TickQualityIssue] = []
        for tick in ticks:
            if tick.event_id in self._seen:
                issues.append(TickQualityIssue("DUPLICATE", "duplicate event id", tick.event_id))
            self._seen.add(tick.event_id)
            if tick.timestamp_received < tick.timestamp_exchange - self._maximum_clock_skew:
                issues.append(
                    TickQualityIssue(
                        "CLOCK_JUMP", "receipt precedes exchange time", tick.event_id
                    )
                )
            if tick.timestamp_received - tick.timestamp_exchange > self._maximum_staleness:
                issues.append(TickQualityIssue("STALE_QUOTE", "tick is stale", tick.event_id))
            key = (tick.provider, tick.instrument, tick.reconnect_id)
            prior = self._previous.get(key)
            if prior is not None:
                if tick.timestamp_exchange < prior.timestamp_exchange:
                    issues.append(
                        TickQualityIssue(
                            "TIMESTAMP_DISORDER", "exchange time moved backwards", tick.event_id
                        )
                    )
                if tick.sequence <= prior.sequence:
                    issues.append(
                        TickQualityIssue(
                            "SEQUENCE_DISORDER", "sequence is not increasing", tick.event_id
                        )
                    )
                elif tick.sequence != prior.sequence + 1:
                    issues.append(
                        TickQualityIssue(
                            "MISSING_SEQUENCE", "sequence gap detected", tick.event_id
                        )
                    )
            self._previous[key] = tick
            source = (tick.provider, tick.instrument)
            prior_reconnect = self._reconnect_id.get(source)
            if (
                prior_reconnect is not None
                and prior_reconnect != tick.reconnect_id
                and tick.sequence != 0
            ):
                issues.append(
                    TickQualityIssue(
                        "RECONNECT_GAP",
                        "new reconnect stream did not restart at sequence zero",
                        tick.event_id,
                    )
                )
            self._reconnect_id[source] = tick.reconnect_id
        self._persist_state()
        return tuple(issues)

    def _persist_state(self) -> None:
        if self._state_path is None:
            return
        payload = {
            "seen": sorted(self._seen),
            "previous": [
                {
                    "provider": key[0],
                    "instrument": key[1],
                    "reconnect_id": key[2],
                    "event_id": tick.event_id,
                    "sequence": tick.sequence,
                    "timestamp_exchange": tick.timestamp_exchange.isoformat(),
                }
                for key, tick in sorted(self._previous.items())
            ],
            "reconnects": [
                {"provider": key[0], "instrument": key[1], "reconnect_id": value}
                for key, value in sorted(self._reconnect_id.items())
            ],
        }
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        temporary.replace(self._state_path)

    def _restore_state(self) -> None:
        assert self._state_path is not None
        payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        self._seen = {str(value) for value in payload.get("seen", [])}
        for row in payload.get("previous", []):
            tick = CanonicalTick(
                str(row["event_id"]),
                str(row["provider"]),
                str(row["instrument"]),
                "UNKNOWN",
                datetime.fromisoformat(str(row["timestamp_exchange"])),
                datetime.fromisoformat(str(row["timestamp_exchange"])),
                int(row["sequence"]),
                Decimal("1"),
                Decimal("0"),
                Decimal("0"),
                None,
                None,
                None,
                None,
                "cursor",
                datetime.fromisoformat(str(row["timestamp_exchange"])),
                str(row["reconnect_id"]),
            )
            key = (tick.provider, tick.instrument, tick.reconnect_id)
            self._previous[key] = tick
        self._reconnect_id = {
            (str(row["provider"]), str(row["instrument"])): str(row["reconnect_id"])
            for row in payload.get("reconnects", [])
        }
