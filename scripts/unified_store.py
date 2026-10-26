"""Immutable bounded collection batches; raw bytes precede parsed admission."""

from __future__ import annotations

import base64
import gzip
import io
import json
import os
import re
from collections import Counter
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

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


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


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

    def read_blob(self, ref: dict[str, Any]) -> bytes:
        path = self._blob_path(ref["sha256"], ref["suffix"])
        if not 0 < ref["bytes"] <= 64_000_000 or path.stat().st_size != ref["bytes"]:
            raise ValueError("INCREMENTAL_BLOB_SIZE_MISMATCH")
        raw = path.read_bytes()
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
        with gzip.GzipFile(fileobj=io.BytesIO(self.read_blob(ref))) as handle:
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
        value: dict[str, Any] = json.loads(self.batch_path(batch_id).read_text())
        if (value.get("schema_version") != SCHEMA or value.get("batch_id") != batch_id
                or signed({k: v for k, v in value.items() if k != "evidence_sha256"}) != value
                or any(value.get(k) != v for k, v in AUTHORITY.items())):
            raise ValueError("COLLECTION_BATCH_HASH_OR_AUTHORITY_MISMATCH")
        self.read_captures(value["raw"])
        if value["normalized"] is not None:
            data = self.read_blob(value["normalized"])
            file = pq.ParquetFile(io.BytesIO(data))
            if (not file.schema_arrow.equals(FIELDS, check_metadata=False)
                    or file.metadata.num_rows != value["bar_count"]):
                raise ValueError("NORMALIZED_INCREMENTAL_SCHEMA_OR_COUNT_MISMATCH")
            rows = [{k: v for k, v in row.items() if k not in {"raw_pack_sha256", "source_hashes"}}
                    for row in file.read().to_pylist()]
            if sha256(canonical(rows)).hexdigest() != value["admitted_rows_sha256"]:
                raise ValueError("NORMALIZED_ROW_CONTENT_MISMATCH")
        return value
