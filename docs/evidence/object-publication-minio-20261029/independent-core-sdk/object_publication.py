"""Nonproduction object-publication protocol; no cloud discovery or credentials.

Only immutable public keys and one conditional latest pointer are writable. An
ETag is an opaque CAS token, never a content checksum. SQLiteObjectStore is a
real local transactional adapter, not evidence of S3/R2 service behavior. A cloud
adapter must translate conditional rejection and uncertain outcomes faithfully,
disable SDK write retries, and pass independent service/ACL/TLS/cache gates.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol, cast

from qtrade.api.unified_data import validate_publication as validate_catalog
from qtrade.monitoring.data_freshness import AUTHORITY
from qtrade.research.data_quality import validate_quality
from qtrade.research.market_validation_receipts import canonical_json
from qtrade.research.unified_market import (
    INDEX_LIMIT,
    SHARD_LIMIT,
    TOTAL_LIMIT,
    validate_index,
    verify_shard_bytes,
)
from scripts.atomic_json import write_json_immutable

LATEST = "market/latest.json"
MANIFEST_LIMIT = 4_000_000
HEAD_LIMIT = 4096
OBJECT_LIMIT = 12_000_000
_KEY = re.compile(
    r"(?:market/(?:shards|index|catalog|quality|manifests)|readonly/summary)/[a-f0-9]{64}\.json\Z"
)
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_GIT = re.compile(r"[a-f0-9]{40}\Z")
Reader = Callable[[str], bytes]
SummaryValidator = Callable[[dict[str, Any], datetime], None]


def _check(ok: Any, code: str) -> None:
    if not ok:
        raise ValueError("OBJECT_PUBLICATION_" + code)


def _hash(value: Any) -> None:
    _check(type(value) is str and _HASH.fullmatch(value), "SHA256_INVALID")


def object_key(key: str) -> str:
    _check(type(key) is str and (key == LATEST or _KEY.fullmatch(key)), "KEY_NOT_ALLOWED")
    return key


def _etag(value: str) -> str:
    _check(
        type(value) is str
        and 1 <= len(value) <= 256
        and not value.startswith("W/")
        and "*" not in value
        and re.fullmatch(r'(?:"[!#-~]+"|[A-Za-z0-9._:-]+)', value) is not None,
        "ETAG_INVALID",
    )
    return value


def _json(raw: bytes, maximum: int) -> dict[str, Any]:
    _check(type(raw) is bytes and 0 < len(raw) <= maximum, "BYTE_BOUND")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            _check(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def constant(_: str) -> None:
        raise ValueError("OBJECT_PUBLICATION_NONFINITE_JSON")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    _check(type(value) is dict, "DOCUMENT_OBJECT_REQUIRED")
    canonical_json(value)  # Shared strict tree/depth/type/bounds contract.
    return cast(dict[str, Any], value)


def _authority(value: dict[str, Any]) -> None:
    _check(
        all(type(value.get(k)) is type(v) and value[k] == v for k, v in AUTHORITY.items()),
        "AUTHORITY_INVALID",
    )


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _root(path: Path) -> Path:
    path = path.absolute()
    _check(
        path.resolve() == path and not any(p.is_symlink() for p in (path, *path.parents)),
        "JOURNAL_PATH_ALIAS",
    )
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    path.mkdir(parents=True, exist_ok=True)
    for created in reversed(missing):
        _sync_directory(created.parent)
    return path


def _read_file(path: Path, maximum: int) -> bytes:
    _check(not any(p.is_symlink() for p in (path, *path.parents)), "JOURNAL_PATH_ALIAS")
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_BINARY", 0),
    )
    with os.fdopen(descriptor, "rb") as handle:
        before = os.fstat(handle.fileno())
        _check(
            stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= maximum,
            "JOURNAL_FILE_BOUND",
        )
        raw = handle.read(maximum + 1)
        after = os.fstat(handle.fileno())
    _check(
        (before.st_dev, before.st_ino, before.st_size)
        == (after.st_dev, after.st_ino, after.st_size)
        and len(raw) == after.st_size,
        "JOURNAL_FILE_CHANGED",
    )
    return raw


class PreconditionFailed(RuntimeError):
    """Definitive rejection: the conditional write did not commit."""


class OutcomeUnknown(RuntimeError):
    """No retry: even a subsequent negative read cannot rule out late commit."""


@dataclass(frozen=True)
class StoredObject:
    raw: bytes
    etag: str


class ObjectStore(Protocol):
    scope_id: str

    def get(self, key: str) -> StoredObject | None: ...

    def put(
        self, key: str, raw: bytes, *, if_none_match: str | None = None, if_match: str | None = None
    ) -> StoredObject: ...


class SQLiteObjectStore:
    """Single-host local FULL/WAL emulator; one connection per owning thread.

    No NFS suitability, cloud authentication or service consistency is implied.
    Independent connections exercise real transactional competing CAS operations.
    """

    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.scope_id = sha256(("sqlite-local-v1\0" + str(self.path)).encode()).hexdigest()
        self.connection = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS objects ("
            "key TEXT PRIMARY KEY, body BLOB NOT NULL, "
            "etag TEXT NOT NULL, sha256 TEXT NOT NULL)"
        )

    def close(self) -> None:
        self.connection.close()

    def get(self, key: str) -> StoredObject | None:
        object_key(key)
        row = self.connection.execute(
            "SELECT body,etag,sha256 FROM objects WHERE key=?", (key,)
        ).fetchone()
        if row is None:
            return None
        raw = bytes(row[0])
        _check(
            len(raw) <= OBJECT_LIMIT and sha256(raw).hexdigest() == row[2], "LOCAL_STORE_CORRUPT"
        )
        return StoredObject(raw, _etag(row[1]))

    def put(
        self, key: str, raw: bytes, *, if_none_match: str | None = None, if_match: str | None = None
    ) -> StoredObject:
        object_key(key)
        _check(type(raw) is bytes and 0 < len(raw) <= OBJECT_LIMIT, "BYTE_BOUND")
        _check(
            (if_none_match == "*" and if_match is None)
            or (if_none_match is None and if_match is not None and key == LATEST),
            "CONDITIONAL_WRITE_REQUIRED",
        )
        if if_match is not None:
            _etag(if_match)
        if key != LATEST:
            _check(
                sha256(raw).hexdigest() == key.rsplit("/", 1)[1][:-5], "CONTENT_ADDRESS_MISMATCH"
            )
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT etag FROM objects WHERE key=?", (key,)).fetchone()
            if (if_none_match == "*" and row is not None) or (
                if_match is not None and (row is None or row[0] != if_match)
            ):
                raise PreconditionFailed("OBJECT_PUBLICATION_CONDITION_REJECTED")
            # Deliberately unrelated to SHA256: tests cannot confuse ETag/content identity.
            token = '"local-' + uuid.uuid4().hex + '"'
            self.connection.execute(
                "INSERT INTO objects VALUES(?,?,?,?) ON CONFLICT(key) "
                "DO UPDATE SET body=excluded.body,etag=excluded.etag,"
                "sha256=excluded.sha256",
                (key, raw, token, sha256(raw).hexdigest()),
            )
            self.connection.execute("COMMIT")
            return StoredObject(raw, token)
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise


def _reference(role: str, raw: bytes) -> dict[str, Any]:
    digest = sha256(raw).hexdigest()
    prefix = "readonly/summary" if role == "summary" else "market/" + role
    return {"key": object_key(prefix + "/" + digest + ".json"), "sha256": digest, "bytes": len(raw)}


def _reference_valid(value: Any, role: str) -> None:
    _check(type(value) is dict and set(value) == {"key", "sha256", "bytes"}, "REFERENCE_SCHEMA")
    _hash(value["sha256"])
    _check(type(value["bytes"]) is int and 0 < value["bytes"] <= OBJECT_LIMIT, "REFERENCE_BOUND")
    prefix = "readonly/summary" if role == "summary" else "market/" + role
    _check(value["key"] == prefix + "/" + value["sha256"] + ".json", "REFERENCE_KEY")
    object_key(value["key"])


def _manifest(raw: bytes) -> dict[str, Any]:
    value = _json(raw, MANIFEST_LIMIT)
    _authority(value)
    _check(
        set(value)
        == set(AUTHORITY)
        | {
            "schema_version",
            "parent_id",
            "generation",
            "source_git_commit",
            "created_at",
            "assets",
            "shards",
            "summary_contract_sha256",
        }
        and value["schema_version"] == "qtrade.object-publication-manifest.v1"
        and canonical_json(value) == raw,
        "MANIFEST_SCHEMA",
    )
    _check(
        type(value["generation"]) is int and 1 <= value["generation"] <= 2**63 - 1,
        "GENERATION_INVALID",
    )
    if value["generation"] == 1:
        _check(value["parent_id"] is None, "PARENT_INVALID")
    else:
        _hash(value["parent_id"])
    _check(
        type(value["source_git_commit"]) is str and _GIT.fullmatch(value["source_git_commit"]),
        "GIT40_IDENTITY_INVALID",
    )
    at = datetime.fromisoformat(value["created_at"])
    _check(at.tzinfo is not None, "CLOCK_INVALID")
    assets = value["assets"]
    _check(
        type(assets) is dict
        and set(assets)
        in ({"index", "catalog", "quality"}, {"index", "catalog", "quality", "summary"}),
        "ASSET_SET",
    )
    for role, reference in assets.items():
        _reference_valid(reference, role)
    if "summary" in assets:
        _hash(value["summary_contract_sha256"])
    else:
        _check(value["summary_contract_sha256"] is None, "SUMMARY_CONTRACT_REQUIRED")
    _check(type(value["shards"]) is list and len(value["shards"]) <= 8000, "SHARD_COUNT")
    for reference in value["shards"]:
        _reference_valid(reference, "shards")
        _check(reference["bytes"] <= SHARD_LIMIT, "SHARD_BYTE_BOUND")
    keys = [reference["key"] for reference in value["shards"]]
    _check(
        keys == sorted(set(keys)) and sum(r["bytes"] for r in value["shards"]) <= TOTAL_LIMIT,
        "SHARD_SET_BOUND",
    )
    return value


@dataclass(frozen=True)
class Parent:
    publication_id: str
    generation: int
    etag: str
    raw: bytes


def _head(raw: bytes) -> dict[str, Any]:
    value = _json(raw, HEAD_LIMIT)
    _authority(value)
    _check(
        set(value)
        == set(AUTHORITY)
        | {"schema_version", "publication_id", "manifest_key", "parent_id", "generation"}
        and value["schema_version"] == "qtrade.object-latest.v1"
        and canonical_json(value) == raw,
        "HEAD_SCHEMA",
    )
    _hash(value["publication_id"])
    _check(
        value["manifest_key"] == "market/manifests/" + value["publication_id"] + ".json",
        "MANIFEST_KEY",
    )
    _check(
        type(value["generation"]) is int and 1 <= value["generation"] <= 2**63 - 1,
        "GENERATION_INVALID",
    )
    if value["generation"] == 1:
        _check(value["parent_id"] is None, "PARENT_INVALID")
    else:
        _hash(value["parent_id"])
    return value


def read_parent(store: ObjectStore) -> Parent | None:
    stored = store.get(LATEST)
    if stored is None:
        return None
    value = _head(stored.raw)
    manifest = store.get(value["manifest_key"])
    _check(manifest is not None, "LATEST_MANIFEST_MISSING")
    assert manifest is not None
    _check(sha256(manifest.raw).hexdigest() == value["publication_id"], "MANIFEST_SHA256")
    document = _manifest(manifest.raw)
    _check(
        document["parent_id"] == value["parent_id"]
        and document["generation"] == value["generation"],
        "HEAD_MANIFEST_CONTEXT",
    )
    return Parent(value["publication_id"], value["generation"], _etag(stored.etag), stored.raw)


@dataclass(frozen=True)
class PreparedPublication:
    publication_id: str
    manifest: bytes
    parent: Parent | None
    source_git_commit: str
    created_at: datetime
    reader: Reader = field(repr=False, compare=False)
    summary_validator: SummaryValidator | None = field(default=None, repr=False, compare=False)
    summary_contract_sha256: str | None = None

    @property
    def manifest_key(self) -> str:
        return "market/manifests/" + self.publication_id + ".json"

    @property
    def head(self) -> bytes:
        document = _manifest(self.manifest)
        return canonical_json(
            {
                **AUTHORITY,
                "schema_version": "qtrade.object-latest.v1",
                "publication_id": self.publication_id,
                "manifest_key": self.manifest_key,
                "generation": document["generation"],
                "parent_id": document["parent_id"],
            }
        )


def prepare_market(
    reader: Reader,
    *,
    parent: Parent | None,
    source_git_commit: str,
    now: datetime,
    created_at: datetime | None = None,
    summary_validator: SummaryValidator | None = None,
    summary_contract_sha256: str | None = None,
) -> tuple[PreparedPublication, dict[str, bytes]]:
    """Reader supplies current physical bytes; never mtime/old receipts as authority.

    Source replay/quality admission remains the existing producer's obligation;
    this layer additionally repeats existing public index/shard/lineage contracts.
    It does not rewrite source timestamps or declare transport fresh from upload.
    """
    created_at = created_at or now
    _check(
        now.tzinfo is not None and created_at.tzinfo is not None and created_at <= now,
        "CLOCK_INVALID",
    )
    _check(
        type(source_git_commit) is str and _GIT.fullmatch(source_git_commit),
        "GIT40_IDENTITY_INVALID",
    )
    if parent is not None:
        parsed = _head(parent.raw)
        _check(
            type(parent.generation) is int
            and parsed["publication_id"] == parent.publication_id
            and parsed["generation"] == parent.generation,
            "PARENT_INVALID",
        )
        _etag(parent.etag)
    documents = {
        "index": reader("unified-market.json"),
        "catalog": reader("unified.json"),
        "quality": reader("quality.json"),
    }
    index = validate_index(_json(documents["index"], INDEX_LIMIT), now)
    catalog = validate_catalog(_json(documents["catalog"], OBJECT_LIMIT))
    quality = validate_quality(_json(documents["quality"], 1_500_000), now=now)
    _authority(catalog)
    _check(
        catalog["master"]["universe_version"]
        == index["universe_version"]
        == quality["universe_version"],
        "ASSET_UNIVERSE_MISMATCH",
    )
    _check(
        catalog["master"]["evidence_sha256"] == index["master_sha256"],
        "INDEX_CATALOG_LINEAGE_MISMATCH",
    )
    _check(datetime.fromisoformat(catalog["built_at"]) <= now, "CATALOG_CLOCK_INVALID")
    targets = {row["symbol"] for row in catalog["master"]["securities"] if row["in_target"]}
    _check({row["symbol"] for row in index["entries"]} == targets, "INDEX_CATALOG_TARGETS")
    objects: dict[str, bytes] = {}
    refs: dict[str, dict[str, Any]] = {}
    for entry in index["entries"]:
        for reference in entry["shards"]:
            digest = reference["sha256"]
            # Read once per distinct object, validate EVERY entry occurrence.
            key = "market/shards/" + digest + ".json"
            if key not in objects:
                objects[key] = reader("unified-market/objects/" + digest + ".json")
            verify_shard_bytes(objects[key], reference, index, entry, now)
            refs[digest] = _reference("shards", objects[key])
    if summary_validator is not None:
        _hash(summary_contract_sha256)
        raw = reader("summary.json")
        value = _json(raw, OBJECT_LIMIT)
        _authority(value)
        _check(value.get("read_only") is True, "READONLY_SUMMARY_REQUIRED")
        summary_validator(value, now)
        documents["summary"] = raw
    else:
        _check(summary_contract_sha256 is None, "SUMMARY_VALIDATOR_REQUIRED")
    assets = {role: _reference(role, raw) for role, raw in documents.items()}
    objects.update({assets[role]["key"]: raw for role, raw in documents.items()})
    manifest = canonical_json(
        {
            **AUTHORITY,
            "schema_version": "qtrade.object-publication-manifest.v1",
            "parent_id": None if parent is None else parent.publication_id,
            "generation": 1 if parent is None else parent.generation + 1,
            "source_git_commit": source_git_commit,
            "created_at": created_at.isoformat(),
            "assets": assets,
            "shards": [refs[key] for key in sorted(refs)],
            "summary_contract_sha256": summary_contract_sha256,
        }
    )
    _manifest(manifest)
    prepared = PreparedPublication(
        sha256(manifest).hexdigest(),
        manifest,
        parent,
        source_git_commit,
        created_at,
        reader,
        summary_validator,
        summary_contract_sha256,
    )
    return prepared, objects


class Journal:
    """Immutable fsynced intent/receipt evidence; corrupt evidence fails closed."""

    def __init__(self, root: Path, scope_id: str):
        _hash(scope_id)
        self.root = _root(root)
        self.scope_id = scope_id

    def path(self, kind: str, binding: dict[str, Any]) -> Path:
        _check(
            kind in {"uploaded", "upload-intent", "cas-intent", "committed", "cas-rejected"},
            "JOURNAL_KIND_INVALID",
        )
        identity = sha256(canonical_json({"scope_id": self.scope_id, **binding})).hexdigest()
        return self.root / (kind + "-" + identity + ".json")

    def read(self, kind: str, binding: dict[str, Any]) -> dict[str, Any] | None:
        path = self.path(kind, binding)
        if not os.path.lexists(path):
            return None
        value = _json(_read_file(path, MANIFEST_LIMIT), MANIFEST_LIMIT)
        _check(
            set(value) == {"schema_version", "scope_id", "binding", "result", "sha256"}
            and value["schema_version"] == "qtrade.object-upload-journal.v1"
            and value["scope_id"] == self.scope_id
            and value["binding"] == binding,
            "JOURNAL_SCHEMA",
        )
        body = {k: v for k, v in value.items() if k != "sha256"}
        _check(sha256(canonical_json(body)).hexdigest() == value["sha256"], "JOURNAL_CHECKSUM")
        result = value["result"]
        _check(type(result) is dict, "JOURNAL_RESULT_SCHEMA")
        if kind == "uploaded":
            _check(
                set(result) == {"state", "etag"} and result["state"] == "VERIFIED",
                "JOURNAL_RESULT_SCHEMA",
            )
            _etag(result["etag"])
        elif kind == "cas-rejected":
            _check(result == {"state": "REJECTED"}, "JOURNAL_RESULT_SCHEMA")
        else:
            _check(
                result == {"state": "VERIFIED_CURRENT_HEAD" if kind == "committed" else "INTENT"},
                "JOURNAL_RESULT_SCHEMA",
            )
        # Recover an already-linked record whose previous writer may have failed
        # its directory fsync. Never claim durability by merely finding a name.
        _sync_directory(self.root)
        return cast(dict[str, Any], value["result"])

    def write(self, kind: str, binding: dict[str, Any], result: dict[str, Any]) -> bool:
        body = {
            "schema_version": "qtrade.object-upload-journal.v1",
            "scope_id": self.scope_id,
            "binding": binding,
            "result": result,
        }
        value = {**body, "sha256": sha256(canonical_json(body)).hexdigest()}
        try:
            write_json_immutable(self.path(kind, binding), value)
        except FileExistsError:
            _check(self.read(kind, binding) == result, "IMMUTABLE_JOURNAL_CONFLICT")
            return False
        return True


@dataclass(frozen=True)
class PublicationResult:
    state: str
    publication_id: str
    current_publication_id: str | None


class Publisher:
    def __init__(self, store: ObjectStore, receipt_root: Path):
        _hash(store.scope_id)
        self.store = store
        self.journal = Journal(receipt_root, store.scope_id)

    def _remote_complete(self, prepared: PreparedPublication) -> None:
        manifest = _manifest(prepared.manifest)
        roles = {
            "unified-market.json": "index",
            "unified.json": "catalog",
            "quality.json": "quality",
        }
        references = {name: manifest["assets"][role] for name, role in roles.items()}
        if "summary" in manifest["assets"]:
            references["summary.json"] = manifest["assets"]["summary"]
        for reference in manifest["shards"]:
            references["unified-market/objects/" + reference["sha256"] + ".json"] = reference

        def reader(name: str) -> bytes:
            reference = references[name]
            stored = self.store.get(reference["key"])
            _check(stored is not None, "COMMITTED_OBJECT_MISSING")
            assert stored is not None
            _check(
                len(stored.raw) == reference["bytes"]
                and sha256(stored.raw).hexdigest() == reference["sha256"],
                "COMMITTED_OBJECT_CORRUPT",
            )
            _etag(stored.etag)
            return stored.raw

        checked, _ = prepare_market(
            reader,
            parent=prepared.parent,
            source_git_commit=prepared.source_git_commit,
            now=datetime.now(UTC),
            created_at=prepared.created_at,
            summary_validator=prepared.summary_validator,
            summary_contract_sha256=prepared.summary_contract_sha256,
        )
        _check(checked.manifest == prepared.manifest, "COMMITTED_MANIFEST_CONTEXT")

    def _upload(self, key: str, raw: bytes) -> None:
        object_key(key)
        binding = {"key": key, "sha256": sha256(raw).hexdigest(), "bytes": len(raw)}
        # Even a durable receipt cannot replace current remote body SHA256.
        receipt = self.journal.read("uploaded", binding)
        stored = self.store.get(key)
        if stored is None:
            if receipt is not None or self.journal.read("upload-intent", binding) is not None:
                raise OutcomeUnknown("OBJECT_PUBLICATION_UPLOAD_MISSING_OR_IN_FLIGHT")
            if not self.journal.write("upload-intent", binding, {"state": "INTENT"}):
                raise OutcomeUnknown("OBJECT_PUBLICATION_CONCURRENT_UPLOAD_INTENT")
            try:
                self.store.put(key, raw, if_none_match="*")
            except PreconditionFailed:
                pass  # Resolve by full GET; ETag alone is never success.
            except (OutcomeUnknown, OSError, TimeoutError):
                raise OutcomeUnknown("OBJECT_PUBLICATION_UPLOAD_RECONCILIATION_REQUIRED") from None
            stored = self.store.get(key)
        if stored is None:
            raise OutcomeUnknown("OBJECT_PUBLICATION_UPLOAD_RECONCILIATION_REQUIRED")
        _check(
            type(stored.raw) is bytes
            and stored.raw == raw
            and sha256(stored.raw).hexdigest() == binding["sha256"],
            "REMOTE_BYTES_MISMATCH",
        )
        _etag(stored.etag)
        if receipt is None:
            self.journal.write("uploaded", binding, {"state": "VERIFIED", "etag": stored.etag})
        else:
            _check(
                set(receipt) == {"state", "etag"} and receipt["state"] == "VERIFIED",
                "UPLOAD_RECEIPT_INVALID",
            )
            _etag(receipt["etag"])

    def reconcile(self, prepared: PreparedPublication) -> PublicationResult:
        """Read-only remote reconciliation; never retries a conditional write."""
        current = read_parent(self.store)
        if current is not None and current.publication_id == prepared.publication_id:
            _check(current.raw == prepared.head, "PROPOSED_HEAD_CONTEXT_CHANGED")
            self._remote_complete(prepared)
            return PublicationResult(
                "COMMITTED_RECONCILED", prepared.publication_id, current.publication_id
            )
        parent = prepared.parent
        if current == parent:
            return PublicationResult(
                "UNCERTAIN_OLD_HEAD_OBSERVED",
                prepared.publication_id,
                None if current is None else current.publication_id,
            )
        return PublicationResult(
            "CONFLICT_OR_SUPERSEDED",
            prepared.publication_id,
            None if current is None else current.publication_id,
        )

    def publish(self, prepared: PreparedPublication, *, now: datetime) -> PublicationResult:
        """Full current-byte revalidation, immutable uploads, then one fenced CAS."""
        checked, objects = prepare_market(
            prepared.reader,
            parent=prepared.parent,
            source_git_commit=prepared.source_git_commit,
            now=now,
            created_at=prepared.created_at,
            summary_validator=prepared.summary_validator,
            summary_contract_sha256=prepared.summary_contract_sha256,
        )
        _check(
            checked.manifest == prepared.manifest
            and sha256(prepared.manifest).hexdigest() == prepared.publication_id,
            "CURRENT_SOURCE_CHANGED",
        )
        binding = {
            "publication_id": prepared.publication_id,
            "head_sha256": sha256(prepared.head).hexdigest(),
            "parent_id": None if prepared.parent is None else prepared.parent.publication_id,
            "parent_etag": None if prepared.parent is None else prepared.parent.etag,
        }
        if self.journal.read("cas-intent", binding) is not None:
            if self.journal.read("cas-rejected", binding) is not None:
                current = read_parent(self.store)
                return PublicationResult(
                    "CAS_REJECTED",
                    prepared.publication_id,
                    None if current is None else current.publication_id,
                )
            return self.reconcile(prepared)
        _check(read_parent(self.store) == prepared.parent, "PARENT_FENCE_CHANGED")
        for key, raw in sorted(objects.items()):
            self._upload(key, raw)
        self._upload(prepared.manifest_key, prepared.manifest)
        # Check every object again immediately before CAS; failed/deleted uploads
        # cannot become a latest pointer. Cloud ACL must prohibit external deletes.
        for key, raw in {**objects, prepared.manifest_key: prepared.manifest}.items():
            self._upload(key, raw)
        if not self.journal.write("cas-intent", binding, {"state": "INTENT"}):
            return self.reconcile(prepared)
        try:
            if prepared.parent is None:
                self.store.put(LATEST, prepared.head, if_none_match="*")
            else:
                self.store.put(LATEST, prepared.head, if_match=prepared.parent.etag)
        except PreconditionFailed:
            self.journal.write("cas-rejected", binding, {"state": "REJECTED"})
            return PublicationResult(
                "CAS_REJECTED",
                prepared.publication_id,
                None if (current := read_parent(self.store)) is None else current.publication_id,
            )
        except (OutcomeUnknown, OSError, TimeoutError):
            return self.reconcile(prepared)
        result = self.reconcile(prepared)
        _check(result.state == "COMMITTED_RECONCILED", "ACK_WITHOUT_MATCHING_HEAD")
        self.journal.write("committed", binding, {"state": "VERIFIED_CURRENT_HEAD"})
        return result
