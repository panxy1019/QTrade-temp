from __future__ import annotations

from datetime import timedelta

from qtrade.market_data.models import CanonicalTick, TickQualityIssue


class TickQualityGate:
    def __init__(
        self,
        *,
        maximum_staleness: timedelta = timedelta(seconds=5),
        maximum_clock_skew: timedelta = timedelta(seconds=2),
    ) -> None:
        if maximum_staleness <= timedelta(0) or maximum_clock_skew < timedelta(0):
            raise ValueError("tick quality time limits are invalid")
        self._maximum_staleness = maximum_staleness
        self._maximum_clock_skew = maximum_clock_skew

    def validate(self, ticks: tuple[CanonicalTick, ...]) -> tuple[TickQualityIssue, ...]:
        issues: list[TickQualityIssue] = []
        seen: set[str] = set()
        previous: dict[tuple[str, str, str], CanonicalTick] = {}
        for tick in ticks:
            if tick.event_id in seen:
                issues.append(TickQualityIssue("DUPLICATE", "duplicate event id", tick.event_id))
            seen.add(tick.event_id)
            if tick.timestamp_received < tick.timestamp_exchange - self._maximum_clock_skew:
                issues.append(
                    TickQualityIssue(
                        "CLOCK_JUMP", "receipt precedes exchange time", tick.event_id
                    )
                )
            if tick.timestamp_received - tick.timestamp_exchange > self._maximum_staleness:
                issues.append(TickQualityIssue("STALE_QUOTE", "tick is stale", tick.event_id))
            key = (tick.provider, tick.instrument, tick.reconnect_id)
            prior = previous.get(key)
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
            previous[key] = tick
        reconnects: dict[tuple[str, str], list[CanonicalTick]] = {}
        for tick in ticks:
            reconnects.setdefault((tick.provider, tick.instrument), []).append(tick)
        for events in reconnects.values():
            ids = {event.reconnect_id for event in events}
            if len(ids) > 1:
                ordered = sorted(events, key=lambda event: event.timestamp_received)
                for prior, current in zip(ordered[:-1], ordered[1:], strict=True):
                    if prior.reconnect_id != current.reconnect_id and current.sequence != 0:
                        issues.append(
                            TickQualityIssue(
                                "RECONNECT_GAP",
                                "new reconnect stream did not restart at sequence zero",
                                current.event_id,
                            )
                        )
        return tuple(issues)
