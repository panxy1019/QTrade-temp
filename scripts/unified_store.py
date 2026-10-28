"""Immutable bounded collection batches; raw bytes precede parsed admission."""

from __future__ import annotations

import base64
import gzip
import importlib.util
import io
import json
import os
import re
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from qtrade.data import BarQualityGate
from qtrade.data.storage import _fsync_directory
from qtrade.market_data.sina_incremental import PRICE_BASIS, IncrementalBar
from qtrade.market_data.sina_minute import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts.atomic_json import write_json_immutable

SCHEMA = "qtrade.incremental-collection-batch.v1"
FIELDS = pa.schema([
    (key, pa.string()) for key in (
        "symbol", "exchange", "frequency", "timestamp", "trading_day", "open", "high", "low",
        "close", "volume", "amount", "vwap", "source_clock", "amount_basis", "price_basis",
        "completion_status", "historical_available_at", "raw_pack_sha256",
    )
] + [("source_hashes", pa.list_(pa.string()))])
_ReadObserver = Callable[[int, Literal['before', 'after', 'failed']], None]


def _export_rows(table: Any) -> list[dict[str, Any]]:
    """Decode the fixed bounded export schema without changing malformed-data behavior."""
    if importlib.util.find_spec('numpy') is None:
        return cast(list[dict[str, Any]], table.to_pylist())
    try:
        # NumPy conversion alone replaces invalid UTF-8. Only fully valid Arrow
        # buffers may use that path; the original decoder owns every failure.
        table.validate(full=True)
    except pa.ArrowInvalid:
        return cast(list[dict[str, Any]], table.to_pylist())
    names = FIELDS.names
    columns = [column.to_pylist() if name == 'source_hashes' else
               column.to_numpy(zero_copy_only=False)
               for name, column in zip(names, table.columns, strict=True)]
    return [dict(zip(names, row, strict=True)) for row in zip(*columns, strict=True)]


def _read_bytes(path: Path, observer: _ReadObserver | None = None, *,
                verified_size: int | None = None) -> bytes:
    if observer is None:
        return path.read_bytes()
    observer(path.stat().st_size if verified_size is None else verified_size, 'before')
    try:
        raw = path.read_bytes()
    except BaseException:
        observer(0, 'failed')
        raise
    observer(len(raw), 'after')
    return raw


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def _decode_captures(ref: dict[str, Any], raw: bytes) -> list[dict[str, Any]]:
    """Decode bytes already checksum-verified by this store's physical read."""
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as handle:
        payload = handle.read(64_000_001)
    if len(payload) > 64_000_000 or len(payload) != ref["uncompressed_bytes"]:
        raise ValueError("RAW_CAPTURE_DECOMPRESSION_LIMIT_OR_SIZE_MISMATCH")
    rows = [json.loads(line) for line in payload.splitlines()]
    if len(rows) != ref["captures"]:
        raise ValueError("RAW_CAPTURE_COUNT_MISMATCH")
    for row in rows:
        body = base64.b64decode(row.pop("body_base64"), validate=True)
        if sha256(body).hexdigest() != row["body_sha256"]:
            raise ValueError("RAW_RESPONSE_CHECKSUM_MISMATCH")
        row["body"] = body
    if [r["body_sha256"] for r in rows] != ref["body_hashes"]:
        raise ValueError("RAW_CAPTURE_LINEAGE_MISMATCH")
    return rows


class _BatchMaterial:
    """One store-owned physical read; callers cannot supply source bytes or decoded rows."""

    def __init__(self, store: UnifiedBatchStore, batch_id: str, *,
                 _read_observer: _ReadObserver | None = None):
        self._read_observer = _read_observer
        self._path = store.batch_path(batch_id)
        self._manifest_bytes = _read_bytes(self._path, _read_observer)
        self.manifest: dict[str, Any] = json.loads(self._manifest_bytes)
        value = self.manifest
        if (value.get("schema_version") != SCHEMA or value.get("batch_id") != batch_id
                or signed({k: v for k, v in value.items() if k != "evidence_sha256"}) != value
                or any(value.get(k) != v for k, v in AUTHORITY.items())):
            raise ValueError("COLLECTION_BATCH_HASH_OR_AUTHORITY_MISMATCH")
        def read_blob(ref: dict[str, Any]) -> bytes:
            if _read_observer is None:
                return store.read_blob(ref)
            return store.read_blob(ref, _read_observer=_read_observer)
        self._raw = read_blob(self.manifest['raw'])
        self._normalized = (read_blob(self.manifest['normalized'])
                            if self.manifest['normalized'] is not None else None)
        self.source_bytes_read = len(self._manifest_bytes)+len(self._raw)+len(
            self._normalized or b'')
        self._file: pq.ParquetFile | None = None
        self._export_bounds_checked = False
        self._verified: (
            tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]] | None) = None

    def identities(self) -> tuple[str, str, str]:
        # read_blob has just checked each physical blob against these exact IDs.
        return (sha256(self._manifest_bytes).hexdigest(), self.manifest['raw']['sha256'],
                self.manifest['normalized']['sha256'] if self._normalized is not None else '')

    def _parquet(self) -> pq.ParquetFile:
        if self._normalized is None:
            raise ValueError('NORMALIZED_INCREMENTAL_SOURCE_MISSING')
        if self._file is None:
            self._file = pq.ParquetFile(io.BytesIO(self._normalized))
        return self._file

    def check_export_bounds(self) -> None:
        # Ordinary store.load retains its existing schema/count policy. Only the
        # bounded market exporter requests these pre-decode working-set checks.
        metadata = self._parquet().metadata
        if (not 1 <= metadata.num_rows <= 50*48 or sum(
                metadata.row_group(i).total_byte_size for i in range(metadata.num_row_groups))
                > 16_000_000):
            raise ValueError('UNIFIED_MARKET_SOURCE_PARQUET_BOUND')
        self._export_bounds_checked = True

    def validate(self) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        if self._verified is None:
            value = self.manifest
            captures = _decode_captures(value['raw'], self._raw)
            rows: list[dict[str, Any]] = []
            if self._normalized is not None:
                file = self._parquet()
                if (not file.schema_arrow.equals(FIELDS, check_metadata=False)
                        or file.metadata.num_rows != value["bar_count"]):
                    raise ValueError("NORMALIZED_INCREMENTAL_SCHEMA_OR_COUNT_MISMATCH")
                table = file.read()
                rows = (_export_rows(table) if self._export_bounds_checked
                        else table.to_pylist())
                plain = [{k: v for k, v in row.items()
                          if k not in {"raw_pack_sha256", "source_hashes"}} for row in rows]
                if sha256(canonical(plain)).hexdigest() != value["admitted_rows_sha256"]:
                    raise ValueError("NORMALIZED_ROW_CONTENT_MISMATCH")
            self._verified = value, captures, rows
        return self._verified

    def check_manifest_unchanged(self) -> None:
        actual = _read_bytes(self._path, self._read_observer)
        self.source_bytes_read += len(actual)
        if actual != self._manifest_bytes:
            raise ValueError('UNIFIED_MARKET_BATCH_CHANGED_DURING_REPLAY')


class UnifiedBatchStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _blob_path(self, digest: str, suffix: str) -> Path:
        if re.fullmatch(r"[a-f0-9]{64}", digest) is None or suffix not in {".jsonl.gz", ".parquet"}:
            raise ValueError("INVALID_INCREMENTAL_BLOB_IDENTITY")
        path = self.root / "objects" / (digest + suffix)
        if path.resolve().parent != (self.root / "objects").resolve():
            raise ValueError("INCREMENTAL_BLOB_PATH_ESCAPE")
        return path

    def _put_blob(self, raw: bytes, suffix: str) -> dict[str, Any]:
        if not 0 < len(raw) <= 64_000_000:
            raise ValueError("INCREMENTAL_BLOB_SIZE_LIMIT")
        digest = sha256(raw).hexdigest()
        path = self._blob_path(digest, suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            if not path.exists():
                with NamedTemporaryFile(dir=path.parent, prefix=".batch-", delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.link(temporary, path)
                    _fsync_directory(path.parent)
                except FileExistsError:
                    pass
            if path.stat().st_size != len(raw) or sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("IMMUTABLE_INCREMENTAL_BLOB_CONFLICT")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return {"sha256": digest, "bytes": len(raw), "suffix": suffix}

    def read_blob(self, ref: dict[str, Any], *,
                  _read_observer: _ReadObserver | None = None) -> bytes:
        path = self._blob_path(ref["sha256"], ref["suffix"])
        if not 0 < ref["bytes"] <= 64_000_000 or path.stat().st_size != ref["bytes"]:
            raise ValueError("INCREMENTAL_BLOB_SIZE_MISMATCH")
        raw = _read_bytes(path, _read_observer, verified_size=ref['bytes'])
        if sha256(raw).hexdigest() != ref["sha256"]:
            raise ValueError("INCREMENTAL_BLOB_CHECKSUM_MISMATCH")
        return raw

    def capture(self, responses: list[dict[str, Any]]) -> dict[str, Any]:
        """Bodies can be malformed; only fixed public-source request envelopes are allowed."""
        if not 1 <= len(responses) <= 100:
            raise ValueError("CAPTURE_PACK_SIZE_LIMIT")
        entries = []
        for response in responses:
            if set(response) != {"source_id", "request", "received_at", "status_code", "body",
                                "transport_error", "body_complete"}:
                raise ValueError("INVALID_RAW_CAPTURE_FIELDS")
            request = response["request"]
            source = response["source_id"]
            if source == "sina_klines":
                valid = (set(request) == {"symbol", "scale", "ma", "datalen"}
                         and re.fullmatch(r"(?:sh|sz)\d{6}", request["symbol"]) is not None
                         and request["scale"] in {"5", "240"} and request["ma"] == "no"
                         and str(request["datalen"]).isdigit()
                         and 1 <= int(request["datalen"]) <= 1970)
            elif source == "sina_quote":
                symbols = request.get("list", "").split(",")
                valid = (set(request) == {"list"} and 1 <= len(symbols) <= 50
                         and len(symbols) == len(set(symbols)) and all(
                             re.fullmatch(r"(?:sh|sz)\d{6}", s) is not None for s in symbols))
            else:
                valid = False
            stamp = datetime.fromisoformat(response["received_at"])
            body = response["body"]
            code, error = response["status_code"], response["transport_error"]
            if (not valid or stamp.tzinfo is None or not isinstance(body, bytes)
                    or code is not None and (type(code) is not int or not 100 <= code <= 599)
                    or code is None and error is None
                    or error is not None and re.fullmatch(
                        r"[A-Za-z][A-Za-z0-9_]{0,79}", error) is None
                    or type(response["body_complete"]) is not bool
                    or code is None and response["body_complete"]
                    or len(body) > 2_000_000):
                raise ValueError("INVALID_PUBLIC_SOURCE_CAPTURE")
            entries.append({**{k: v for k, v in response.items() if k != "body"},
                            "body_sha256": sha256(body).hexdigest(),
                            "body_base64": base64.b64encode(body).decode()})
        payload = b"\n".join(canonical(row) for row in entries) + b"\n"
        if len(payload) > 64_000_000:
            raise ValueError("RAW_CAPTURE_PACK_TOO_LARGE")
        ref = self._put_blob(gzip.compress(payload, mtime=0), ".jsonl.gz")
        return {**ref, "captures": len(entries), "uncompressed_bytes": len(payload),
                "body_hashes": [row["body_sha256"] for row in entries]}

    def read_captures(self, ref: dict[str, Any]) -> list[dict[str, Any]]:
        return _decode_captures(ref, self.read_blob(ref))

    def batch_path(self, batch_id: str) -> Path:
        if re.fullmatch(r"[a-f0-9]{32}", batch_id) is None:
            raise ValueError("INVALID_COLLECTION_BATCH_ID")
        return self.root / "batches" / (batch_id + ".json")

    def commit(
        self, batch_id: str, spec: dict[str, Any], raw_ref: dict[str, Any],
        bars: list[IncrementalBar], outcomes: dict[str, dict[str, Any]], *, completed_at: datetime,
    ) -> dict[str, Any]:
        path = self.batch_path(batch_id)
        records = sorted(bars, key=lambda row: (row.bar.timestamp, row.bar.symbol))
        admitted_hash = sha256(canonical([row.row() for row in records])).hexdigest()
        if path.exists():
            previous = self.load(batch_id)
            if (previous["spec"] != spec or previous["raw"] != raw_ref
                    or previous["admitted_rows_sha256"] != admitted_hash):
                raise ValueError("BATCH_IDEMPOTENCY_INPUT_CONFLICT")
            return previous
        captures = self.read_captures(raw_ref)
        if (set(spec) != {"frequency", "target_end", "universe_version", "symbols", "code_revision",
                          "after_by_symbol"}
                or spec["frequency"] not in {"day", "5min"}
                or re.fullmatch(r"[a-f0-9]{64}", spec["universe_version"]) is None
                or re.fullmatch(r"[a-f0-9]{40}", spec["code_revision"]) is None
                or not 1 <= len(spec["symbols"]) <= 50
                or len(set(spec["symbols"])) != len(spec["symbols"])
                or any(re.fullmatch(r"\d{6}", s) is None for s in spec["symbols"])
                or set(spec["after_by_symbol"]) != set(spec["symbols"])
                or set(outcomes) != set(spec["symbols"]) or completed_at.tzinfo is None):
            raise ValueError("INVALID_INCREMENTAL_BATCH_SPEC")
        target = datetime.fromisoformat(spec["target_end"])
        if target.tzinfo is None or target > completed_at:
            raise ValueError("INVALID_INCREMENTAL_TARGET_CLOCK")
        if BarQualityGate().validate([r.bar for r in records]):
            raise ValueError("INCREMENTAL_BATCH_OHLCV_FAILED")
        for row in records:
            after = spec["after_by_symbol"].get(row.bar.symbol)
            if (row.bar.symbol not in spec["symbols"] or row.frequency != spec["frequency"]
                    or row.price_basis != PRICE_BASIS or row.bar.timestamp > target
                    or row.bar.trading_day != target.date()
                    or after is not None and row.bar.timestamp <= datetime.fromisoformat(after)
                    or row.frequency == "5min"
                    and row.bar.timestamp.time() not in SINA_FIVE_MINUTE_SESSION_TIMES):
                raise ValueError("BATCH_ROW_SCOPE_OR_COMPLETION_MISMATCH")
            if row.frequency == "day" and (row.amount is None or row.vwap is None
                                            or row.source_clock is None):
                raise ValueError("DAILY_AMOUNT_OR_SOURCE_CLOCK_UNVERIFIED")
        counts = Counter(r.bar.symbol for r in records)
        for symbol, outcome in outcomes.items():
            if (outcome["state"] not in {"SUCCEEDED", "FAILED"}
                    or (outcome["state"] == "SUCCEEDED") != bool(counts[symbol])):
                raise ValueError("BATCH_OUTCOME_ROWS_MISMATCH")
        source_hashes: dict[str, list[str]] = {symbol: [] for symbol in spec["symbols"]}
        for capture in captures:
            keys = ([capture["request"]["symbol"]] if capture["source_id"] == "sina_klines"
                    else capture["request"]["list"].split(","))
            for code in keys:
                if code[2:] in source_hashes:
                    if any(r.bar.symbol == code[2:] and (r.bar.exchange == "SSE") != (
                            code[:2] == "sh") for r in records):
                        raise ValueError("RAW_LINEAGE_EXCHANGE_MISMATCH")
                    source_hashes[code[2:]].append(capture["body_sha256"])
        if any(not source_hashes[r.bar.symbol] for r in records):
            raise ValueError("MISSING_BAR_RAW_LINEAGE")
        normalized = None
        if records:
            table = pa.Table.from_pylist([
                {**row.row(), "raw_pack_sha256": raw_ref["sha256"],
                 "source_hashes": source_hashes[row.bar.symbol]} for row in records
            ], schema=FIELDS)
            buffer = io.BytesIO()
            pq.write_table(table, buffer, compression="zstd")
            normalized = self._put_blob(buffer.getvalue(), ".parquet")
        summary = {}
        for symbol, outcome in outcomes.items():
            selected = [r for r in records if r.bar.symbol == symbol]
            summary[symbol] = {**outcome, "bar_count": len(selected),
                               "start": selected[0].bar.timestamp.isoformat() if selected else None,
                               "end": selected[-1].bar.timestamp.isoformat() if selected else None,
                               "missing_amount_bars": sum(r.amount is None for r in selected)}
        manifest = signed({
            **AUTHORITY, "schema_version": SCHEMA, "batch_id": batch_id, "spec": spec,
            "completed_at": completed_at.isoformat(), "raw": raw_ref, "normalized": normalized,
            "bar_count": len(records), "outcomes": summary, "price_basis": PRICE_BASIS,
            "admitted_rows_sha256": admitted_hash,
            "stage_counts": {
                "requested": len(captures),
                "downloaded": sum(c["status_code"] == 200 and c["body_complete"] for c in captures),
                "parsed_symbols": sum(bool(o.get("decoded")) for o in outcomes.values()),
                "validated_symbols": sum(bool(o.get("parsed")) for o in outcomes.values()),
                "admitted_symbols": len(counts), "admitted_rows": len(records),
                "same_vendor_endpoint_matched": sum(bool(o.get("same_vendor_matched"))
                                                    for o in outcomes.values()),
                "independent_source_reconciled": 0,
            },
            "historical_pit_certified": False, "independent_source_reconciled": False,
        })
        write_json_immutable(path, manifest)
        return manifest

    def load(self, batch_id: str) -> dict[str, Any]:
        return self._batch_material(batch_id).validate()[0]

    def _batch_material(self, batch_id: str, *,
                        _read_observer: _ReadObserver | None = None) -> _BatchMaterial:
        return _BatchMaterial(self, batch_id, _read_observer=_read_observer)
