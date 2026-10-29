"""Bounded, append-only semantic replay receipts; never source-byte authority.

The publisher must physically read/hash all source objects before deriving a key,
and recheck current metadata, window and freshness on every pass. This module
stores successful replay output only. SHA256 is integrity, not authentication.
SQLite belongs on a local filesystem; immutable sidecars may live on NFS after
that mount's file/link/directory-fsync contract has been verified separately.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Self, cast

from qtrade.data.storage import _fsync_directory
from qtrade.market_data.sina_incremental import PRICE_BASIS
from qtrade.monitoring.data_freshness import AUTHORITY

SCHEMA = "qtrade.market-validation-receipt.v1"
MAX_RECEIPT_BYTES = 16_000_000
MAX_ROWS = 2400
MAX_CANDIDATES = 8
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_BATCH = re.compile(r"[a-f0-9]{32}\Z")
_ROW_FIELDS = {
    "symbol", "exchange", "frequency", "timestamp", "trading_day", "open", "high", "low",
    "close", "volume", "amount", "vwap", "source_clock", "amount_basis", "price_basis",
    "completion_status", "historical_available_at", "raw_pack_sha256", "source_hashes",
}
_MANIFEST_FIELDS = set(AUTHORITY) | {
    "schema_version", "batch_id", "spec", "completed_at", "raw", "normalized", "bar_count",
    "outcomes", "price_basis", "admitted_rows_sha256", "stage_counts",
    "historical_pit_certified", "independent_source_reconciled", "evidence_sha256",
}
_DOCUMENT_FIELDS = {
    "schema_version", "key", "outcome", "validated_at", "result", "result_sha256", "failure",
    "receipt_id",
}


def _check(condition: bool, code: str = "SCHEMA_INVALID") -> None:
    if not condition:
        raise ValueError("MARKET_RECEIPT_" + code)


def _hash(value: Any) -> None:
    _check(type(value) is str and _HASH.fullmatch(value) is not None, "HASH_INVALID")


def _json_tree(value: Any, depth: int = 0) -> None:
    """Reject lossy JSON coercions, nonfinite numbers and unbounded nesting."""
    _check(depth <= 24, "JSON_DEPTH_BOUND")
    if type(value) is dict:
        _check(len(value) <= 10_000 and all(type(k) is str for k in value), "JSON_KEYS_INVALID")
        for key, item in value.items():
            _check(len(key) <= 1024, "JSON_KEY_BOUND")
            _json_tree(item, depth + 1)
    elif type(value) is list:
        _check(len(value) <= 100_000, "JSON_ARRAY_BOUND")
        for item in value:
            _json_tree(item, depth + 1)
    elif type(value) is str:
        _check(len(value) <= 2_000_000, "JSON_STRING_BOUND")
    elif type(value) is float:
        _check(math.isfinite(value), "JSON_NUMBER_INVALID")
    else:
        _check(value is None or type(value) in {bool, int}, "JSON_TYPE_INVALID")


def canonical_json(value: Any) -> bytes:
    """Use the collection store's sorted UTF-8/separator policy without coercion."""
    _json_tree(value)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    _check(len(raw) <= MAX_RECEIPT_BYTES, "BYTE_BOUND")
    return raw


def _digest(value: Any) -> str:
    return sha256(canonical_json(value)).hexdigest()


def _aware(value: Any) -> datetime:
    _check(type(value) is str and len(value) <= 64, "CLOCK_INVALID")
    stamp = datetime.fromisoformat(value)
    _check(stamp.tzinfo is not None and stamp.utcoffset() is not None, "CLOCK_INVALID")
    return stamp


def _clock(value: datetime) -> datetime:
    _check(isinstance(value, datetime) and value.tzinfo is not None
           and value.utcoffset() is not None, "CLOCK_INVALID")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ReceiptKey:
    batch_id: str
    manifest_sha256: str
    raw_sha256: str
    normalized_sha256: str
    context_sha256: str

    def __post_init__(self) -> None:
        _check(type(self.batch_id) is str and _BATCH.fullmatch(self.batch_id) is not None,
               "BATCH_INVALID")
        for value in (self.manifest_sha256, self.raw_sha256, self.normalized_sha256,
                      self.context_sha256):
            _hash(value)

    @property
    def key_sha256(self) -> str:
        return _digest(asdict(self))


def receipt_key(batch_id: str, identities: tuple[str, str, str],
                context_sha256: str) -> ReceiptKey:
    """identities must come from this pass's physically verified BatchMaterial."""
    _check(type(identities) is tuple and len(identities) == 3, "IDENTITIES_INVALID")
    return ReceiptKey(batch_id, *identities, context_sha256)


def _result(manifest: Any, rows: Any, key: ReceiptKey, validated: datetime) -> dict[str, Any]:
    _check(type(manifest) is dict and set(manifest) == _MANIFEST_FIELDS)
    _check(manifest["schema_version"] == "qtrade.incremental-collection-batch.v1"
           and manifest["batch_id"] == key.batch_id
           and manifest["price_basis"] == PRICE_BASIS)
    _check(all(type(manifest[k]) is type(v) and manifest[k] == v
               for k, v in AUTHORITY.items()), "AUTHORITY_INVALID")
    _check(manifest["historical_pit_certified"] is False
           and manifest["independent_source_reconciled"] is False, "AUTHORITY_INVALID")
    _check(_digest({k: v for k, v in manifest.items() if k != "evidence_sha256"})
           == manifest["evidence_sha256"], "MANIFEST_EVIDENCE_INVALID")
    completed = _aware(manifest["completed_at"])
    _check(completed <= validated, "FUTURE_SOURCE_CLOCK")
    spec = manifest["spec"]
    _check(type(spec) is dict and set(spec) == {
        "frequency", "target_end", "universe_version", "symbols", "code_revision",
        "after_by_symbol",
    })
    _hash(spec["universe_version"])
    _check(spec["frequency"] in {"day", "5min"}
           and type(spec["code_revision"]) is str
           and re.fullmatch(r"[a-f0-9]{40}", spec["code_revision"]) is not None)
    target = _aware(spec["target_end"])
    _check(target <= completed, "FUTURE_SOURCE_CLOCK")
    symbols = spec["symbols"]
    _check(type(symbols) is list and 1 <= len(symbols) <= 50
           and all(type(s) is str and re.fullmatch(r"[0-9]{6}", s) for s in symbols))
    _check(len(set(symbols)) == len(symbols)
           and type(spec["after_by_symbol"]) is dict
           and set(spec["after_by_symbol"]) == set(symbols))
    for value in spec["after_by_symbol"].values():
        if value is not None:
            _check(_aware(value) < target, "CLOCK_INVALID")
    for field, suffix, digest in (("raw", ".jsonl.gz", key.raw_sha256),
                                  ("normalized", ".parquet", key.normalized_sha256)):
        ref = manifest[field]
        fields = {"sha256", "bytes", "suffix"}
        if field == "raw":
            fields |= {"captures", "uncompressed_bytes", "body_hashes"}
        _check(type(ref) is dict and set(ref) == fields)
        _check(ref["sha256"] == digest and ref["suffix"] == suffix
               and type(ref["bytes"]) is int and 1 <= ref["bytes"] <= 64_000_000)
    raw = manifest["raw"]
    _check(type(raw["captures"]) is int and 1 <= raw["captures"] <= 100
           and type(raw["uncompressed_bytes"]) is int
           and 1 <= raw["uncompressed_bytes"] <= 64_000_000
           and type(raw["body_hashes"]) is list
           and len(raw["body_hashes"]) == raw["captures"])
    for digest in raw["body_hashes"]:
        _hash(digest)
    _check(type(rows) is list and 1 <= len(rows) <= MAX_ROWS, "ROW_BOUND")
    _check(type(manifest["bar_count"]) is int and manifest["bar_count"] == len(rows))
    for row in rows:
        _check(type(row) is dict and set(row) == _ROW_FIELDS)
        _check(row["symbol"] in symbols and row["frequency"] == spec["frequency"]
               and row["exchange"] in {"SSE", "SZSE"} and row["price_basis"] == PRICE_BASIS
               and row["completion_status"] == "CLOSED"
               and row["historical_available_at"] is None
               and row["raw_pack_sha256"] == key.raw_sha256)
        nullable = {"amount", "vwap", "source_clock", "historical_available_at"}
        for field in _ROW_FIELDS - {"source_hashes"}:
            value = row[field]
            _check(value is None and field in nullable
                   or type(value) is str and 1 <= len(value) <= 128)
        _check(_aware(row["timestamp"]) <= target, "FUTURE_SOURCE_CLOCK")
        if row["source_clock"] is not None:
            _check(_aware(row["source_clock"]) <= completed, "FUTURE_SOURCE_CLOCK")
        hashes = row["source_hashes"]
        _check(type(hashes) is list and 1 <= len(hashes) <= 100)
        _check(all(digest in raw["body_hashes"] for digest in hashes), "LINEAGE_INVALID")
    plain = [{k: v for k, v in row.items() if k not in {"raw_pack_sha256", "source_hashes"}}
             for row in rows]
    _check(_digest(plain) == manifest["admitted_rows_sha256"], "ROW_HASH_INVALID")
    return {"manifest": manifest, "rows": rows}


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        _check(key not in value, "DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def _decode(raw: bytes, key: ReceiptKey, now: datetime) -> dict[str, Any]:
    _check(type(raw) is bytes and 1 <= len(raw) <= MAX_RECEIPT_BYTES, "BYTE_BOUND")
    value = json.loads(raw, object_pairs_hook=_pairs)
    _check(type(value) is dict and set(value) == _DOCUMENT_FIELDS)
    _check(canonical_json(value) == raw, "NONCANONICAL_JSON")
    _hash(value["receipt_id"])
    _check(value["receipt_id"] == _digest({k: v for k, v in value.items()
                                           if k != "receipt_id"}), "PAYLOAD_HASH_INVALID")
    _check(value["schema_version"] == SCHEMA and value["key"] == asdict(key),
           "CONTEXT_INVALID")
    validated = _aware(value["validated_at"])
    _check(validated <= now, "FUTURE_VALIDATION_CLOCK")
    _check(value["outcome"] in {"PASSED", "FAILED"})
    if value["outcome"] == "PASSED":
        result = value["result"]
        _check(type(result) is dict and set(result) == {"manifest", "rows"}
               and value["failure"] is None)
        _hash(value["result_sha256"])
        _check(_digest(result) == value["result_sha256"], "RESULT_HASH_INVALID")
        _result(result["manifest"], result["rows"], key, validated)
    else:
        failure = value["failure"]
        _check(value["result"] is None and value["result_sha256"] is None
               and type(failure) is dict and set(failure) == {"code", "evidence_refs"})
        _check(type(failure["code"]) is str
               and re.fullmatch(r"[A-Z][A-Z0-9_]{0,159}", failure["code"]) is not None)
        refs = failure["evidence_refs"]
        _check(type(refs) is list and len(refs) <= 100)
        for ref in refs:
            _hash(ref)
    return cast(dict[str, Any], value)


class MarketValidationReceiptStore:
    """Long-lived connection, owned by its constructing process and thread.

    Distinct local connections may compete safely: BEGIN IMMEDIATE serializes
    immutable inserts. This is not a publication lease or cross-host NFS lock.
    Read corruption is a cache miss. Storage/fsync failures propagate.
    """

    def __init__(self, path: Path, *, sidecar_root: Path | None = None) -> None:
        _check(not path.is_symlink(), "DATABASE_SYMLINK")
        self.path = path.resolve()
        self.sidecar_root = sidecar_root.resolve() if sidecar_root is not None else None
        self._owner = (os.getpid(), threading.get_ident())
        self._closed = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            mode = self._connection.execute("PRAGMA journal_mode=WAL").fetchone()
            _check(mode is not None and mode[0] == "wal", "WAL_UNAVAILABLE")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS market_validation_receipts (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_id TEXT NOT NULL UNIQUE,
                    key_sha256 TEXT NOT NULL,
                    outcome TEXT NOT NULL CHECK(outcome IN ('PASSED','FAILED')),
                    payload_sha256 TEXT NOT NULL,
                    payload BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS market_validation_receipts_lookup
                    ON market_validation_receipts(key_sha256, outcome, sequence DESC);
                CREATE TRIGGER IF NOT EXISTS market_validation_receipts_no_update
                    BEFORE UPDATE ON market_validation_receipts
                    BEGIN SELECT RAISE(ABORT, 'immutable market validation receipt'); END;
                CREATE TRIGGER IF NOT EXISTS market_validation_receipts_no_delete
                    BEFORE DELETE ON market_validation_receipts
                    BEGIN SELECT RAISE(ABORT, 'immutable market validation receipt'); END;
                """)
        except BaseException:
            self._connection.close()
            self._closed = True
            raise

    def _owned(self) -> None:
        _check(not self._closed, "STORE_CLOSED")
        _check(self._owner == (os.getpid(), threading.get_ident()), "OWNER_MISMATCH")

    def __enter__(self) -> Self:
        self._owned()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._owned()
            self._connection.close()
            self._closed = True

    def _records(self, key: ReceiptKey, now: datetime) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT receipt_id,payload_sha256,length(payload),"
            "CASE WHEN length(payload)<=? THEN payload ELSE NULL END "
            "FROM market_validation_receipts WHERE key_sha256=? AND outcome='PASSED' "
            "ORDER BY sequence DESC LIMIT ?",
            (MAX_RECEIPT_BYTES, key.key_sha256, MAX_CANDIDATES),
        )
        documents = []
        for receipt_id, payload_hash, count, payload in rows:
            try:
                _check(type(payload) is bytes and 1 <= count <= MAX_RECEIPT_BYTES)
                _check(sha256(payload).hexdigest() == payload_hash, "PAYLOAD_HASH_INVALID")
                value = _decode(payload, key, now)
                _check(value["outcome"] == "PASSED" and value["receipt_id"] == receipt_id)
                documents.append(value)
            except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
                continue
        return documents

    def get(self, key: ReceiptKey, *, now: datetime
            ) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        self._owned()
        now = _clock(now)
        documents = self._records(key, now)
        if not documents and self.sidecar_root is not None:
            directory = self.sidecar_root / key.key_sha256
            # Do not scan an unbounded directory or load oversized payloads.
            if directory.is_symlink():
                return None
            if directory.is_dir():
                paths: list[Path] = []
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if len(paths) >= MAX_CANDIDATES:
                            return None
                        paths.append(Path(entry.path))
                for path in sorted(paths):
                    try:
                        if (path.is_symlink() or path.suffix != ".json"
                                or _HASH.fullmatch(path.stem) is None):
                            continue
                        with path.open("rb") as handle:
                            raw = handle.read(MAX_RECEIPT_BYTES + 1)
                        value = _decode(raw, key, now)
                        _check(value["receipt_id"] == path.stem)
                        if value["outcome"] == "PASSED":
                            self._insert(value, raw, key, now, write_sidecar=False)
                            documents.append(value)
                    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
                        continue
        if not documents:
            return None
        if len({value["result_sha256"] for value in documents}) != 1:
            return None
        result = documents[0]["result"]
        return result["manifest"], result["rows"]

    def put_success(self, key: ReceiptKey, manifest: dict[str, Any], rows: list[dict[str, Any]],
                    *, validated_at: datetime) -> str:
        self._owned()
        stamp = _clock(validated_at)
        result = _result(manifest, rows, key, stamp)
        return self._put(key, stamp, "PASSED", result, None)

    def put_failure(self, key: ReceiptKey, *, validated_at: datetime, code: str,
                    evidence_refs: tuple[str, ...] = ()) -> str:
        self._owned()
        return self._put(key, _clock(validated_at), "FAILED", None,
                         {"code": code, "evidence_refs": list(evidence_refs)})

    def _put(self, key: ReceiptKey, stamp: datetime, outcome: str, result: Any,
             failure: Any) -> str:
        value = {"schema_version": SCHEMA, "key": asdict(key), "outcome": outcome,
                 "validated_at": stamp.isoformat(), "result": result,
                 "result_sha256": _digest(result) if result is not None else None,
                 "failure": failure}
        value["receipt_id"] = _digest(value)
        raw = canonical_json(value)
        _decode(raw, key, stamp)
        return self._insert(value, raw, key, stamp, write_sidecar=True)

    def _insert(self, value: dict[str, Any], raw: bytes, key: ReceiptKey, now: datetime,
                *, write_sidecar: bool) -> str:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            if value["outcome"] == "PASSED":
                previous = self._records(key, now)
                _check(all(old["result_sha256"] == value["result_sha256"] for old in previous),
                       "IMMUTABLE_RESULT_CONFLICT")
                if previous:
                    self._connection.execute("COMMIT")
                    return str(previous[0]["receipt_id"])
            if write_sidecar and self.sidecar_root is not None:
                self._write_sidecar(key, value["receipt_id"], raw)
            self._connection.execute(
                "INSERT OR IGNORE INTO market_validation_receipts "
                "(receipt_id,key_sha256,outcome,payload_sha256,payload) VALUES(?,?,?,?,?)",
                (value["receipt_id"], key.key_sha256, value["outcome"],
                 sha256(raw).hexdigest(), raw),
            )
            self._connection.execute("COMMIT")
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        return str(value["receipt_id"])

    def _write_sidecar(self, key: ReceiptKey, receipt_id: str, raw: bytes) -> None:
        assert self.sidecar_root is not None
        directory = self.sidecar_root / key.key_sha256
        _check(not self.sidecar_root.is_symlink() and not directory.is_symlink(),
               "SIDECAR_SYMLINK")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (receipt_id + ".json")
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(dir=directory, prefix=".receipt-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                _check(not path.is_symlink() and path.stat().st_size == len(raw)
                       and path.read_bytes() == raw, "IMMUTABLE_FILE_CONFLICT")
            # A failed previous attempt may already have linked the exact file.
            # Always fsync the names/ancestors before committing the local index.
            for parent in (directory, self.sidecar_root, self.sidecar_root.parent):
                _fsync_directory(parent)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
