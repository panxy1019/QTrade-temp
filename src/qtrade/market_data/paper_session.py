from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from threading import Lock

from qtrade.market_data.models import CanonicalTick
from qtrade.market_data.quality import TickQualityGate
from qtrade.market_data.recording import (
    AvailableBar,
    CanonicalTickStore,
    RawTickArchive,
    TickBarAggregator,
)
from qtrade.runtime.realtime import RealDataPaperResult, RealDataPaperRunner


@dataclass(frozen=True, slots=True)
class RecordedPaperEvent:
    bars: tuple[AvailableBar, ...]
    result: RealDataPaperResult
    canonical_path: Path
    canonical_checksum: str


class RecordedPaperSession:
    """One authorized stream's record-quality-bar-strategy-PAPER composition."""

    def __init__(
        self,
        *,
        root: Path,
        runner: RealDataPaperRunner,
        quality: TickQualityGate | None = None,
        aggregator: TickBarAggregator | None = None,
    ) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._runner = runner
        self._quality = quality or TickQualityGate()
        self._aggregator = aggregator or TickBarAggregator()
        self._raw = RawTickArchive(self._root / "raw.jsonl")
        self._state_path = self._root / "session-state.json"
        self._states = self._load_states()
        self._lock = Lock()
        self._recover()

    def on_tick(self, tick: CanonicalTick, *, watermark: datetime) -> RecordedPaperEvent:
        with self._lock:
            return self._on_tick_locked(tick, watermark=watermark)

    def _on_tick_locked(
        self, tick: CanonicalTick, *, watermark: datetime
    ) -> RecordedPaperEvent:
        try:
            ingest_ordinal = self._raw.append(
                tick.provider,
                {"tick": asdict(tick), "watermark": watermark.isoformat()},
                tick.timestamp_received,
            )
            recorded_tick = replace(tick, ingest_ordinal=ingest_ordinal)
            self._set_state(ingest_ordinal, "RAW_APPENDED")
            return self._process(recorded_tick, watermark, ingest_ordinal)
        except Exception:
            self._runner.report_market_incident(
                "recorded market data quality or persistence failed"
            )
            raise
    def verify_raw_chain(self) -> bool:
        return self._raw.verify_chain()

    def _process(
        self, tick: CanonicalTick, watermark: datetime, ingest_ordinal: int
    ) -> RecordedPaperEvent:
        state = self._states[str(ingest_ordinal)]["state"]
        if state == "RAW_APPENDED":
            issues = self._quality.validate((tick,))
            if issues:
                self._set_state(ingest_ordinal, "REJECTED")
                raise ValueError(
                    "market data quality rejected tick: "
                    + ",".join(sorted({issue.code for issue in issues}))
                )
            self._set_state(ingest_ordinal, "QUALITY_ACCEPTED")
            state = "QUALITY_ACCEPTED"
        path, checksum = self._canonical_segment(tick, ingest_ordinal)
        if state == "QUALITY_ACCEPTED":
            self._set_state(
                ingest_ordinal,
                "CANONICAL_WRITTEN",
                canonical_path=str(path),
                canonical_checksum=checksum,
            )
        bars = self._aggregator.aggregate((tick,), watermark=watermark)
        self._set_state(ingest_ordinal, "RUNNER_PENDING")
        for bar in bars:
            self._runner.on_finalized_bar(bar)
        result = self._runner.on_tick(tick)
        self._set_state(ingest_ordinal, "APPLIED")
        return RecordedPaperEvent(bars, result, path, checksum)

    def _recover(self) -> None:
        rows = self._raw.read()
        for row in rows:
            ordinal = int(str(row["ingest_ordinal"]))
            self._states.setdefault(str(ordinal), {"state": "RAW_APPENDED"})
        if rows:
            self._save_states()
        for row in rows:
            ordinal = int(str(row["ingest_ordinal"]))
            state = str(self._states[str(ordinal)]["state"])
            tick, watermark = _decode_raw_event(row, ordinal)
            if state == "REJECTED":
                continue
            if state == "RUNNER_PENDING":
                self._runner.report_market_incident(
                    "recorded market event has ambiguous runner outcome"
                )
                raise RuntimeError(
                    f"market event {ordinal} requires explicit runtime reconciliation"
                )
            if state == "APPLIED":
                issues = self._quality.validate((tick,))
                if issues:
                    self._runner.report_market_incident(
                        "recorded market event history failed quality rebuild"
                    )
                    raise RuntimeError("recorded market event history is inconsistent")
                self._verify_canonical_segment(tick, ordinal)
                self._aggregator.aggregate((tick,), watermark=watermark)
                continue
            try:
                self._process(tick, watermark, ordinal)
            except ValueError:
                # A durably rejected event remains part of the raw audit trail but
                # does not advance the accepted quality cursor.
                if self._states[str(ordinal)]["state"] != "REJECTED":
                    raise

    def _canonical_segment(
        self, tick: CanonicalTick, ingest_ordinal: int
    ) -> tuple[Path, str]:
        store = CanonicalTickStore(self._root / "canonical")
        state = self._states[str(ingest_ordinal)]
        if state["state"] == "CANONICAL_WRITTEN":
            path = Path(str(state["canonical_path"]))
            checksum = str(state["canonical_checksum"])
            store.read(path, checksum)
            return path, checksum
        segment = f"ingest-{ingest_ordinal:020d}"
        expected = self._root / "canonical" / tick.data_version / "segments" / segment
        manifest = expected / "manifest.json"
        if manifest.is_file():
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            path = expected / "ticks.parquet"
            checksum = str(payload["checksum"])
            store.read(path, checksum)
            return path, checksum
        return store.write((tick,), segment_id=segment)

    def _verify_canonical_segment(self, tick: CanonicalTick, ordinal: int) -> None:
        self._canonical_segment(tick, ordinal)

    def _set_state(self, ordinal: int, state: str, **fields: object) -> None:
        self._states[str(ordinal)] = {"state": state, **fields}
        self._save_states()

    def _load_states(self) -> dict[str, dict[str, object]]:
        if not self._state_path.is_file():
            return {}
        envelope = json.loads(self._state_path.read_text(encoding="utf-8"))
        states = envelope.get("states")
        if not isinstance(states, dict):
            raise ValueError("recorded PAPER session journal is malformed")
        canonical = json.dumps(states, sort_keys=True, separators=(",", ":"))
        if sha256(canonical.encode()).hexdigest() != envelope.get("hash"):
            raise ValueError("recorded PAPER session journal checksum mismatch")
        return {
            str(key): dict(value)
            for key, value in states.items()
            if isinstance(value, dict)
        }

    def _save_states(self) -> None:
        canonical = json.dumps(self._states, sort_keys=True, separators=(",", ":"))
        envelope = {
            "states": self._states,
            "hash": sha256(canonical.encode()).hexdigest(),
        }
        temporary = self._state_path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(envelope, sort_keys=True))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self._state_path)


def _decode_raw_event(
    row: dict[str, object], ingest_ordinal: int
) -> tuple[CanonicalTick, datetime]:
    payload = row.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("tick"), dict):
        raise ValueError("recorded PAPER raw event schema is invalid")
    value = payload["tick"]
    assert isinstance(value, dict)
    def optional_decimal(item: object) -> Decimal | None:
        return None if item in {None, "None"} else Decimal(str(item))
    tick = CanonicalTick(
        event_id=str(value["event_id"]),
        provider=str(value["provider"]),
        instrument=str(value["instrument"]),
        exchange=str(value["exchange"]),
        timestamp_exchange=datetime.fromisoformat(str(value["timestamp_exchange"])),
        timestamp_received=datetime.fromisoformat(str(value["timestamp_received"])),
        sequence=int(value["sequence"]),
        price=Decimal(str(value["price"])),
        volume=Decimal(str(value["volume"])),
        turnover=Decimal(str(value["turnover"])),
        bid_price=optional_decimal(value.get("bid_price")),
        ask_price=optional_decimal(value.get("ask_price")),
        bid_volume=optional_decimal(value.get("bid_volume")),
        ask_volume=optional_decimal(value.get("ask_volume")),
        data_version=str(value["data_version"]),
        ingestion_time=datetime.fromisoformat(str(value["ingestion_time"])),
        reconnect_id=str(value.get("reconnect_id", "initial")),
        ingest_ordinal=ingest_ordinal,
        session_nonce=str(value.get("session_nonce", "unattested")),
    )
    watermark = datetime.fromisoformat(str(payload["watermark"]))
    return tick, watermark
