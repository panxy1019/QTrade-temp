from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from threading import Lock

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from qtrade.data import Bar
from qtrade.market_data.models import CanonicalTick
from qtrade.market_data.quality import TickQualityGate


class RawTickArchive:
    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._lock = Lock()
        self._ordinal = 0
        self._previous_hash = ""
        if self._path.is_file():
            lines = self._path.read_text(encoding="utf-8").splitlines()
            if lines:
                last = json.loads(lines[-1])
                self._ordinal = int(last["ingest_ordinal"]) + 1
                self._previous_hash = str(last["hash"])

    def append(self, provider: str, payload: dict[str, object], received_at: datetime) -> None:
        if not provider or received_at.tzinfo is None:
            raise ValueError("raw tick archive identity and receipt time are required")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            envelope = {
                "ingest_ordinal": self._ordinal,
                "provider": provider,
                "received_at": received_at.isoformat(),
                "payload": payload,
                "previous_hash": self._previous_hash,
            }
            canonical = json.dumps(envelope, sort_keys=True, default=str)
            digest = sha256(canonical.encode()).hexdigest()
            envelope["hash"] = digest
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(envelope, sort_keys=True, default=str) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._ordinal += 1
            self._previous_hash = digest

    def verify_chain(self) -> bool:
        previous_hash = ""
        for expected_ordinal, line in enumerate(
            self._path.read_text(encoding="utf-8").splitlines()
        ):
            row = json.loads(line)
            digest = str(row.pop("hash"))
            if int(row["ingest_ordinal"]) != expected_ordinal:
                return False
            if row["previous_hash"] != previous_hash:
                return False
            canonical = json.dumps(row, sort_keys=True, default=str)
            if sha256(canonical.encode()).hexdigest() != digest:
                return False
            previous_hash = digest
        return True


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
        version = next(iter(versions))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", version):
            raise ValueError("canonical tick data_version must be an opaque identifier")
        if version in {".", ".."}:
            raise ValueError("canonical tick data_version must be an opaque identifier")
        target_dir = (self._root / version).resolve()
        if not target_dir.is_relative_to(self._root):
            raise ValueError("canonical tick target escapes recording root")
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
        temporary = target.with_suffix(".parquet.tmp")
        pq.write_table(pa.table(payload), temporary, compression="zstd")
        checksum = sha256(temporary.read_bytes()).hexdigest()
        temporary.replace(target)
        manifest = target_dir / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "data_version": next(iter(versions)),
                    "row_count": len(ticks),
                    "checksum": checksum,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return target, checksum

    def read(self, path: Path, checksum: str) -> tuple[CanonicalTick, ...]:
        target = path.resolve()
        if not target.is_relative_to(self._root):
            raise ValueError("canonical tick path escapes recording root")
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
                ingest_ordinal=(
                    None if row["ingest_ordinal"] is None else int(row["ingest_ordinal"])
                ),
                session_nonce=row.get("session_nonce", "unattested"),
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
        self._pending: dict[tuple[str, str, datetime], list[CanonicalTick]] = {}
        self._finalized: set[tuple[str, str, datetime]] = set()

    def aggregate(
        self, ticks: tuple[CanonicalTick, ...], *, watermark: datetime
    ) -> tuple[AvailableBar, ...]:
        if watermark.tzinfo is None:
            raise ValueError("bar watermark must be timezone-aware")
        ordered = sorted(ticks, key=lambda tick: (tick.timestamp_exchange, tick.sequence))
        seconds = int(self._interval.total_seconds())
        for tick in ordered:
            epoch = int(tick.timestamp_exchange.timestamp())
            start = datetime.fromtimestamp(epoch - epoch % seconds, tz=UTC)
            key = (tick.instrument, tick.exchange, start)
            if key in self._finalized:
                raise ValueError("late tick arrived after bar finalization")
            self._pending.setdefault(key, []).append(tick)
        result: list[AvailableBar] = []
        closable = {
            key: values
            for key, values in self._pending.items()
            if key[2] + self._interval <= watermark
        }
        ordered_groups = sorted(closable.items(), key=lambda item: item[0][2])
        for (instrument, exchange, start), values in ordered_groups:
            values.sort(key=lambda tick: (tick.timestamp_exchange, tick.sequence))
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
                    max(max(item.timestamp_received for item in values), start + self._interval),
                    values[0].sequence,
                    values[-1].sequence,
                )
            )
            key = (instrument, exchange, start)
            self._finalized.add(key)
            del self._pending[key]
        return tuple(result)


class TickReplay:
    def __init__(self, ticks: tuple[CanonicalTick, ...]) -> None:
        issues = TickQualityGate().validate(ticks)
        if issues:
            raise ValueError("replay input failed quality gate")
        ordinals = [tick.ingest_ordinal for tick in ticks]
        if any(value is not None for value in ordinals):
            if any(value is None for value in ordinals) or len(set(ordinals)) != len(ordinals):
                raise ValueError("replay ingest ordinals must be complete and unique")
            self._ticks = tuple(
                sorted(ticks, key=lambda tick: tick.ingest_ordinal or 0)
            )
        else:
            self._ticks = ticks

    def run(self, handler: Callable[[CanonicalTick], None]) -> None:
        for tick in self._ticks:
            handler(tick)
