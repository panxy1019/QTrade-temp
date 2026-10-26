"""Immutable raw history windows and independently replayable content qualification."""

from __future__ import annotations

import gzip
import io
import json
import re
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from qtrade.data import Bar, BarQualityGate
from qtrade.instruments.observed import instrument_id
from qtrade.market_data.akshare import SHANGHAI
from qtrade.market_data.baostock_five_minute import (
    BAOSTOCK_FIVE_MINUTE_FIELDS,
    fetch_baostock_five_minute_history,
)
from qtrade.market_data.sina_incremental import IncrementalBar
from qtrade.market_data.sina_minute import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts.atomic_json import write_json_immutable
from scripts.probe_unified_history import RecordedApi
from scripts.unified_store import FIELDS, UnifiedBatchStore, canonical

DAY_FIELDS = ("date", "code", "open", "high", "low", "close", "volume", "amount",
              "adjustflag", "tradestatus")
PRICE_BASIS = "BAOSTOCK_RAW_ADJUSTFLAG_3_VENDOR_CLAIM_UNCERTIFIED"
SCHEMA = "qtrade.history-window.v1"
HISTORY_FIELDS = pa.schema(list(FIELDS) + [pa.field("session_quality", pa.string())])


def fields(frequency: str) -> tuple[str, ...]:
    if frequency not in {"day", "5min"}:
        raise ValueError("HISTORY_FREQUENCY_INVALID")
    return DAY_FIELDS if frequency == "day" else BAOSTOCK_FIVE_MINUTE_FIELDS


def request_for(scope: dict[str, Any]) -> dict[str, str]:
    start, end = date.fromisoformat(scope["start"]), date.fromisoformat(scope["end"])
    if (not 0 <= (end - start).days < 31 or scope["instrument_id"] != instrument_id(
            scope["symbol"], scope["security_type"])):
        raise ValueError("HISTORY_WINDOW_SCOPE_INVALID")
    return {"code": ("sh." if scope["instrument_id"].startswith("SSE.") else "sz.")
            + scope["symbol"], "fields": ",".join(fields(scope["frequency"])),
            "start_date": scope["start"], "end_date": scope["end"],
            "frequency": "d" if scope["frequency"] == "day" else "5", "adjustflag": "3"}


def qualify(
    raw: dict[str, Any], scope: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Missing weekdays are candidates, never certified exchange-session gaps."""
    freq = scope["frequency"]
    request = request_for(scope)
    if (raw["request"] != request or tuple(raw["fields"]) != fields(freq)
            or not raw["body_complete"] or raw["vendor_error_code"] != "0"
            or raw["transport_error"] is not None):
        raise ValueError("HISTORY_DOWNLOAD_OR_FIELDS_INCOMPLETE")
    if len(raw["rows"]) > (31 if freq == "day" else 1488):
        raise ValueError("HISTORY_RESPONSE_LIMIT")
    start, end = date.fromisoformat(scope["start"]), date.fromisoformat(scope["end"])
    candidates = [str(start + timedelta(days=i)) for i in range((end - start).days + 1)
                  if (start + timedelta(days=i)).weekday() < 5]
    observations: list[IncrementalBar] = []
    placeholders: Counter[str] = Counter()
    zero_volume: Counter[str] = Counter()
    seen: set[tuple[str, str]] = set()
    for row in raw["rows"]:
        if (len(row) != len(fields(freq)) or any(not isinstance(v, str) or len(v) > 100
                                                for v in row)):
            raise ValueError("HISTORY_ROW_FIELDS_INVALID")
        day = date.fromisoformat(row[0])
        offset = 3 if freq == "5min" else 2
        code = row[2] if freq == "5min" else row[1]
        if (code != request["code"] or row[offset + 6] != "3"
                or not start <= day <= end or day.weekday() >= 5):
            raise ValueError("HISTORY_ROW_IDENTITY_OR_DATE_INVALID")
        values = tuple(Decimal(v) for v in row[offset:offset + 6])
        opening, high, low, close, volume, amount = values
        if any(not v.is_finite() for v in values) or min(volume, amount) < 0:
            raise ValueError("HISTORY_NUMERIC_INVALID")
        stamp = (datetime.strptime(row[1], "%Y%m%d%H%M%S%f").replace(tzinfo=SHANGHAI)
                 if freq == "5min" else datetime.combine(day, time(15), SHANGHAI))
        if (freq == "5min" and (len(row[1]) != 17 or not row[1].isdigit()
                               or stamp.date() != day
                               or stamp.time() not in SINA_FIVE_MINUTE_SESSION_TIMES)
                or (code, stamp.isoformat()) in seen):
            raise ValueError("HISTORY_DUPLICATE_OR_CLOCK_INVALID")
        seen.add((code, stamp.isoformat()))
        if freq == "day" and row[-1] not in {"0", "1"}:
            raise ValueError("HISTORY_VENDOR_TRADESTATUS_INVALID")
        if all(v == 0 for v in values[:5]):
            if amount != 0 or freq == "day" and row[-1] != "0":
                raise ValueError("HISTORY_PLACEHOLDER_INCONSISTENT")
            placeholders[str(day)] += 1
            continue
        bar = Bar(scope["symbol"], scope["instrument_id"].split(".")[0], stamp, day,
                  opening, high, low, close, volume)
        if BarQualityGate().validate([bar], expected_symbol=scope["symbol"]):
            raise ValueError("HISTORY_OHLCV_INVALID")
        if volume == 0:
            if amount != 0 or freq == "day" and row[-1] == "0" and len(
                    {opening, high, low, close}) != 1:
                raise ValueError("HISTORY_ZERO_VOLUME_INCONSISTENT")
            zero_volume[str(day)] += 1
            continue
        if freq == "day" and row[-1] == "0":
            raise ValueError("HISTORY_SUSPENSION_VOLUME_CONFLICT")
        tick = Decimal("0.001") if scope["security_type"] == "ETF" else Decimal("0.01")
        if amount <= 0 or not low - tick <= amount / volume <= high + tick:
            raise ValueError("HISTORY_AMOUNT_OR_UNIT_MISMATCH")
        observations.append(IncrementalBar(
            bar, freq, amount, amount / volume, price_basis=PRICE_BASIS,
        ))
    # Reuse the deployed parser for minute identity, exact clock grid, placeholders,
    # ordering, duplicate timestamps and the canonical OHLCV/jump gate.
    if freq == "5min" and observations:
        parsed = fetch_baostock_five_minute_history(
            RecordedApi(raw), symbol=scope["symbol"], start_date=scope["start"],
            end_date=scope["end"], adjustflag="3",
        )
        if [b for b in parsed if b.volume > 0] != [r.bar for r in observations]:
            raise ValueError("HISTORY_PRODUCTION_PARSER_DISAGREEMENT")
    elif observations and BarQualityGate().validate(r.bar for r in observations):
        raise ValueError("HISTORY_DAILY_ORDER_OR_JUMP_INVALID")
    counts = Counter(str(r.bar.trading_day) for r in observations)
    full = sorted(d for d, n in counts.items() if freq == "day" or n == 48)
    partial = {d: n for d, n in counts.items() if freq == "5min" and n != 48}
    normalized = [{**r.row(), "session_quality": "CONTENT_QUALIFIED_OBSERVED_SESSION"
                   if str(r.bar.trading_day) in full else "PARTIAL_SESSION_QUARANTINED"}
                  for r in observations]
    report = {
        "observed_rows": len(observations), "qualified_rows": sum(counts[d] for d in full),
        "observed_start": min(counts) if counts else None,
        "observed_end": max(counts) if counts else None,
        "qualified_start": full[0] if full else None, "qualified_end": full[-1] if full else None,
        "qualified_observed_sessions": full, "partial_sessions": partial,
        "vendor_all_zero_rows_by_day": dict(placeholders),
        "vendor_zero_volume_rows_by_day": dict(zero_volume),
        "unobserved_weekday_candidates_not_exchange_calendar": [d for d in candidates
                                                                 if d not in counts],
        "window_coverage_certified": False,
        "missing_reason": "UNKNOWN_CALENDAR_LISTING_SUSPENSION_AND_SOURCE_RANGE_UNRECONCILED",
    }
    report["state"] = ("CONTENT_QUALIFIED_WITH_UNRESOLVED_GAPS" if full else
                       "PARTIAL_SESSIONS_QUARANTINED" if observations else
                       "VENDOR_NONTRADING_OBSERVATIONS_UNCERTIFIED" if raw["rows"] else
                       "EMPTY_WINDOW_UNEXPLAINED")
    return normalized, report


class HistoryStore:
    def __init__(self, root: Path):
        self.root = root
        self.blobs = UnifiedBatchStore(root)

    def path(self, attempt_id: str, name: str) -> Path:
        if re.fullmatch(r"[a-f0-9]{32}", attempt_id) is None or name not in {
                "request.json", "receipt.json", "capture.jsonl"}:
            raise ValueError("HISTORY_ATTEMPT_PATH_INVALID")
        return self.root / "attempts" / attempt_id / name

    def begin(self, attempt: dict[str, Any]) -> None:
        write_json_immutable(self.path(attempt["attempt_id"], "request.json"),
                             signed({**AUTHORITY, **attempt}))

    def seal(self, attempt: dict[str, Any], raw: dict[str, Any], *, state: str | None = None,
             error: str | None = None) -> dict[str, Any]:
        """Even failed or preempted downloads get immutable raw capture and a receipt."""
        path = self.path(attempt["attempt_id"], "receipt.json")
        if path.exists():
            previous = self.load(attempt["attempt_id"])
            digest = sha256(gzip.compress(canonical(raw), mtime=0)).hexdigest()
            if (previous["attempt"] != attempt or previous["raw"]["sha256"] != digest
                    or state is not None and previous["quality"]["state"] != state):
                raise ValueError("HISTORY_ATTEMPT_IDEMPOTENCY_CONFLICT")
            return previous
        raw_ref = self.blobs._put_blob(gzip.compress(canonical(raw), mtime=0), ".jsonl.gz")
        records: list[dict[str, Any]] = []
        quality: dict[str, Any] = {"state": state or "SOURCE_QUERY_FAILED", "qualified_rows": 0,
                                   "observed_rows": 0, "qualified_observed_sessions": []}
        if state is None:
            try:
                records, quality = qualify(raw, attempt["scope"])
            except Exception as exc:
                message = str(exc)
                error = message if re.fullmatch(r"[A-Z_]{1,100}", message) else type(exc).__name__
                quality["state"] = ("SOURCE_QUERY_FAILED" if not raw["body_complete"]
                                    or raw["vendor_error_code"] != "0" else "QUALITY_REJECTED")
        normalized = None
        if records:
            rows = [{**r, "raw_pack_sha256": raw_ref["sha256"],
                     "source_hashes": [raw_ref["sha256"]]} for r in records]
            buffer = io.BytesIO()
            pq.write_table(pa.Table.from_pylist(rows, schema=HISTORY_FIELDS), buffer,
                           compression="zstd")
            normalized = self.blobs._put_blob(buffer.getvalue(), ".parquet")
        receipt = signed({
            **AUTHORITY, "schema_version": SCHEMA, "attempt": attempt, "raw": raw_ref,
            "sealed_at": datetime.now(UTC).isoformat(),
            "normalized": normalized, "quality": quality, "error_code": error,
            "normalized_rows_sha256": sha256(canonical(records)).hexdigest(),
            "downloaded_rows": len(raw["rows"]), "price_basis": PRICE_BASIS,
            "stage_counts": {"requested": int(raw.get("query_attempted", False)),
                             "downloaded": int(raw["body_complete"]),
                             "parsed_rows": quality["observed_rows"],
                             "qualified_rows": quality["qualified_rows"],
                             "independent_source_reconciled": 0, "published_qlib_versions": 0},
            "historical_available_at": None, "historical_pit_certified": False,
            "independent_source_reconciled": False, "ten_year_history_certified": False,
        })
        write_json_immutable(path, receipt)
        return receipt

    def load(self, attempt_id: str) -> dict[str, Any]:
        value: dict[str, Any] = json.loads(self.path(attempt_id, "receipt.json").read_text(
            encoding="utf-8"))
        request = json.loads(self.path(attempt_id, "request.json").read_text(encoding="utf-8"))
        if (value.get("schema_version") != SCHEMA or value["attempt"]["attempt_id"] != attempt_id
                or signed({k: v for k, v in value.items() if k != "evidence_sha256"}) != value
                or any(value.get(k) != v for k, v in AUTHORITY.items())
                or request != signed({**AUTHORITY, **value["attempt"]})):
            raise ValueError("HISTORY_RECEIPT_IDENTITY_INVALID")
        with gzip.GzipFile(fileobj=io.BytesIO(self.blobs.read_blob(value["raw"]))) as handle:
            payload = handle.read(5_000_001)
        if len(payload) > 5_000_000:
            raise ValueError("HISTORY_RAW_DECOMPRESSION_BOUND")
        raw = json.loads(payload)
        if (raw["request"] != request_for(value["attempt"]["scope"])
                or value["downloaded_rows"] != len(raw["rows"])
                or value["normalized"] is None and (value["quality"]["qualified_rows"] != 0
                                                      or value["quality"]["observed_rows"] != 0)):
            raise ValueError("HISTORY_RAW_SCOPE_OR_RECEIPT_COUNTS_INVALID")
        stages = {"requested": int(raw.get("query_attempted", False)),
                  "downloaded": int(raw["body_complete"]),
                  "parsed_rows": value["quality"]["observed_rows"],
                  "qualified_rows": value["quality"]["qualified_rows"],
                  "independent_source_reconciled": 0, "published_qlib_versions": 0}
        if (value["stage_counts"] != stages or value["price_basis"] != PRICE_BASIS
                or value["historical_available_at"] is not None
                or any(value[k] is not False for k in (
                    "historical_pit_certified", "independent_source_reconciled",
                    "ten_year_history_certified"))
                or value["normalized"] is None and value["normalized_rows_sha256"] != sha256(
                    canonical([])).hexdigest()):
            raise ValueError("HISTORY_RECEIPT_STAGE_OR_AUTHORITY_MISMATCH")
        if raw["body_complete"] and value["quality"]["state"] in {
                "CONTENT_QUALIFIED_WITH_UNRESOLVED_GAPS", "PARTIAL_SESSIONS_QUARANTINED",
                "VENDOR_NONTRADING_OBSERVATIONS_UNCERTIFIED", "EMPTY_WINDOW_UNEXPLAINED"}:
            records, quality = qualify(raw, value["attempt"]["scope"])
            if quality != value["quality"]:
                raise ValueError("HISTORY_QUALITY_REPLAY_MISMATCH")
        if value["normalized"] is not None:
            records, quality = qualify(raw, value["attempt"]["scope"])
            table = pq.read_table(io.BytesIO(self.blobs.read_blob(value["normalized"])))
            actual = table.to_pylist()
            stripped = [{k: v for k, v in r.items() if k not in {
                "raw_pack_sha256", "source_hashes"}} for r in actual]
            if (not table.schema.equals(HISTORY_FIELDS, check_metadata=False)
                    or stripped != records or quality != value["quality"]
                    or sha256(canonical(records)).hexdigest() != value["normalized_rows_sha256"]
                    or any(r["raw_pack_sha256"] != value["raw"]["sha256"]
                           or r["source_hashes"] != [value["raw"]["sha256"]] for r in actual)):
                raise ValueError("HISTORY_NORMALIZED_REPLAY_MISMATCH")
        return value

    def qualified_rows(self, attempt_id: str) -> dict[str, dict[str, Any]]:
        value = self.load(attempt_id)
        if value["normalized"] is None:
            return {}
        rows = pq.read_table(io.BytesIO(self.blobs.read_blob(value["normalized"]))).to_pylist()
        return {r["timestamp"]: {k: v for k, v in r.items() if k not in {
            "raw_pack_sha256", "source_hashes"}} for r in rows
            if r["session_quality"] == "CONTENT_QUALIFIED_OBSERVED_SESSION"}
