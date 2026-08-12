from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from qtrade.data import Bar
from qtrade.market_data.models import CanonicalTick
from qtrade.market_data.quality import TickQualityGate


class RawTickArchive:
    def __init__(self, path: Path) -> None:
        self._path = path.resolve()

    def append(self, provider: str, payload: dict[str, object], received_at: datetime) -> None:
        if not provider or received_at.tzinfo is None:
            raise ValueError("raw tick archive identity and receipt time are required")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "provider": provider,
            "received_at": received_at.isoformat(),
            "payload": payload,
        }
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(envelope, sort_keys=True, default=str) + "\n")


class CanonicalTickStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def write(
        self,
        ticks: tuple[CanonicalTick, ...],
        *,
        quality_gate: TickQualityGate | None = None,
    ) -> tuple[Path, str]:
        if not ticks:
            raise ValueError("canonical tick recording cannot be empty")
        issues = (quality_gate or TickQualityGate()).validate(ticks)
        if issues:
            raise ValueError(
                "canonical ticks failed quality gate: "
                + ",".join(sorted({issue.code for issue in issues}))
            )
        versions = {tick.data_version for tick in ticks}
        if len(versions) != 1:
            raise ValueError("canonical tick recording requires one data_version")
        target_dir = self._root / next(iter(versions))
        target_dir.mkdir(parents=True, exist_ok=False)
        target = target_dir / "ticks.parquet"
        payload: dict[str, list[object]] = {
            field: [] for field in asdict(ticks[0])
        }
        for tick in ticks:
            for field, value in asdict(tick).items():
                serialized = (
                    value.isoformat()
                    if isinstance(value, datetime)
                    else str(value)
                    if isinstance(value, Decimal)
                    else value
                )
                payload[field].append(serialized)
        pq.write_table(pa.table(payload), target, compression="zstd")
        return target, sha256(target.read_bytes()).hexdigest()

    def read(self, path: Path, checksum: str) -> tuple[CanonicalTick, ...]:
        target = path.resolve()
        if sha256(target.read_bytes()).hexdigest() != checksum:
            raise ValueError("canonical tick checksum mismatch")
        rows = pq.read_table(target).to_pylist()
        return tuple(
            CanonicalTick(
                event_id=row["event_id"],
                provider=row["provider"],
                instrument=row["instrument"],
                exchange=row["exchange"],
                timestamp_exchange=datetime.fromisoformat(row["timestamp_exchange"]),
                timestamp_received=datetime.fromisoformat(row["timestamp_received"]),
                sequence=int(row["sequence"]),
                price=Decimal(row["price"]),
                volume=Decimal(row["volume"]),
                turnover=Decimal(row["turnover"]),
                bid_price=None if row["bid_price"] == "None" else Decimal(row["bid_price"]),
                ask_price=None if row["ask_price"] == "None" else Decimal(row["ask_price"]),
                bid_volume=None if row["bid_volume"] == "None" else Decimal(row["bid_volume"]),
                ask_volume=None if row["ask_volume"] == "None" else Decimal(row["ask_volume"]),
                data_version=row["data_version"],
                ingestion_time=datetime.fromisoformat(row["ingestion_time"]),
                reconnect_id=row["reconnect_id"],
            )
            for row in rows
        )


@dataclass(frozen=True, slots=True)
class AvailableBar:
    bar: Bar
    available_at: datetime
    first_sequence: int
    last_sequence: int


class TickBarAggregator:
    def __init__(self, interval: timedelta = timedelta(minutes=1)) -> None:
        if interval <= timedelta(0):
            raise ValueError("bar interval must be positive")
        self._interval = interval

    def aggregate(self, ticks: tuple[CanonicalTick, ...]) -> tuple[AvailableBar, ...]:
        if not ticks:
            return ()
        ordered = sorted(ticks, key=lambda tick: (tick.timestamp_exchange, tick.sequence))
        groups: dict[tuple[str, str, datetime], list[CanonicalTick]] = {}
        seconds = int(self._interval.total_seconds())
        for tick in ordered:
            epoch = int(tick.timestamp_exchange.timestamp())
            start = datetime.fromtimestamp(epoch - epoch % seconds, tz=UTC)
            groups.setdefault((tick.instrument, tick.exchange, start), []).append(tick)
        result: list[AvailableBar] = []
        ordered_groups = sorted(groups.items(), key=lambda item: item[0][2])
        for (instrument, exchange, start), values in ordered_groups:
            prices = [item.price for item in values]
            result.append(
                AvailableBar(
                    Bar(
                        instrument,
                        exchange,
                        start,
                        start.date(),
                        prices[0],
                        max(prices),
                        min(prices),
                        prices[-1],
                        values[-1].volume - values[0].volume,
                    ),
                    max(item.timestamp_received for item in values),
                    values[0].sequence,
                    values[-1].sequence,
                )
            )
        return tuple(result)


class TickReplay:
    def __init__(self, ticks: tuple[CanonicalTick, ...]) -> None:
        issues = TickQualityGate().validate(ticks)
        if issues:
            raise ValueError("replay input failed quality gate")
        self._ticks = tuple(sorted(ticks, key=lambda tick: (tick.sequence, tick.event_id)))

    def run(self, handler: Callable[[CanonicalTick], None]) -> None:
        for tick in self._ticks:
            handler(tick)
