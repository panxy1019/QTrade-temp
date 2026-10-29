from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Barrier

import pytest

from qtrade.instruments.observed import build_observed_master
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research import object_publication as op
from qtrade.research import unified_market as market

NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)
GIT = "c" * 40


def encoded(value):
    return market.encoded(value)


def fixture_data():
    calendar = Calendar(Path(__file__).resolve().parents[1] / "configs/observer_calendar_2026.json")
    master = build_observed_master(
        vendor_rows=[
            {"code": "sh.600036", "code_name": "PUBLIC SYNTHETIC", "type": "1", "status": "1"}
        ],
        vendor_sha256="a" * 64,
        source_groups=[
            {
                "source_id": "fixture",
                "role": "candidate",
                "sha256": "b" * 64,
                "rows": [{"symbol": "600036", "security_type": "STOCK"}],
            }
        ],
        observed_at=NOW,
        target_count=1,
    )
    for row in master["securities"]:
        row["frequencies"] = {
            freq: {"planned": True, "state": "PLANNED"} for freq in ("day", "5min")
        }
    master = signed(
        {
            **{k: v for k, v in master.items() if k != "evidence_sha256"},
            "frequency_counts": {freq: {"PLANNED": 1} for freq in ("day", "5min")},
        }
    )
    catalog = signed(
        {
            **AUTHORITY,
            "schema_version": "qtrade.unified-catalog.v1",
            "built_at": NOW.isoformat(),
            "master": master,
        }
    )
    bar = {
        "symbol": "600036",
        "exchange": "SSE",
        "frequency": "5min",
        "timestamp": "2026-10-28T09:35:00+08:00",
        "trading_day": "2026-10-28",
        "open": "10",
        "high": "12",
        "low": "9",
        "close": "10",
        "volume": "100",
        "amount": None,
        "vwap": None,
        "amount_basis": "VENDOR_CLAIM_UNVERIFIED",
        "price_basis": market.PRICE_BASIS,
        "historical_available_at": None,
    }
    shard = signed(
        {
            **AUTHORITY,
            "schema_version": "qtrade.unified-market-shard.v1",
            "universe_version": master["universe_version"],
            "symbol": "600036",
            "security_type": "STOCK",
            "instrument_id": "SSE.600036",
            "frequency": "5min",
            "price_basis": market.PRICE_BASIS,
            "bars": [bar],
            "source_batches": [
                {
                    "batch_id": "1" * 32,
                    "manifest_sha256": "2" * 64,
                    "raw_sha256": "3" * 64,
                    "normalized_sha256": "4" * 64,
                }
            ],
        }
    )
    raw = encoded(shard)
    reference = {
        "sha256": sha256(raw).hexdigest(),
        "evidence_sha256": shard["evidence_sha256"],
        "bytes": len(raw),
        "start": bar["timestamp"],
        "end": bar["timestamp"],
        "count": 1,
    }
    entries = [
        {
            "symbol": "600036",
            "instrument_id": "SSE.600036",
            "security_type": "STOCK",
            "name": "PUBLIC SYNTHETIC",
            "frequency": frequency,
            "state": "NO_DATA" if frequency == "day" else "AVAILABLE",
            "issues": [],
            "shards": [] if frequency == "day" else [reference],
        }
        for frequency in ("day", "5min")
    ]
    index = signed(
        {
            **AUTHORITY,
            "schema_version": "qtrade.unified-market-index.v1",
            "universe_version": master["universe_version"],
            "master_sha256": master["evidence_sha256"],
            "published_at": NOW.isoformat(),
            "calendar": calendar.document,
            "windows": {
                freq: market.sessions(
                    calendar,
                    calendar.expected(NOW, "daily" if freq == "day" else "minute").date(),
                    20 if freq == "day" else 3,
                )
                for freq in ("day", "5min")
            },
            "entries": entries,
            "total_files": 1,
            "total_bytes": len(raw),
            "limitations": market.LIMITATIONS,
            "implementation_sources": {
                "scripts/publish_unified_market.py": "5" * 64,
                "src/qtrade/research/unified_market.py": "6" * 64,
            },
        }
    )
    quality = signed(
        {
            **AUTHORITY,
            "schema_version": "qtrade.data-quality-observation.v1",
            "observed_at": NOW.isoformat(),
            "universe_version": master["universe_version"],
            "source_revision": GIT,
            "production_admission_effect": "NONE",
            "execution_admitted": False,
            "cases": [],
            "case_counts": {},
            "source_issues": [],
            "recent_status": {"available": False, "content_verified_symbols": 0},
        }
    )
    return {
        "unified-market.json": encoded(index),
        "unified.json": encoded(catalog),
        "quality.json": encoded(quality),
        "unified-market/objects/" + reference["sha256"] + ".json": raw,
    }


@pytest.fixture
def setup(tmp_path, monkeypatch):
    # Windows cannot fsync directory handles with os.open. Its tests explicitly
    # instrument that syscall only. Linux uses actual immutable link/fsync.
    if os.name == "nt":
        monkeypatch.setattr(op, "_sync_directory", lambda path: None)
        original = op.write_json_immutable

        def immutable(path, value):
            try:
                original(path, value)
            except PermissionError:
                assert json.loads(path.read_bytes()) == value

        monkeypatch.setattr(op, "write_json_immutable", immutable)
    data = fixture_data()
    store = op.SQLiteObjectStore(tmp_path / "store.sqlite")
    prepared, objects = op.prepare_market(
        data.__getitem__, parent=None, source_git_commit=GIT, now=NOW
    )
    publisher = op.Publisher(store, tmp_path / "receipts")
    yield data, store, prepared, objects, publisher
    store.close()


def test_local_real_full_wal_persistence_and_git40_distinct_from_publication64(setup, tmp_path):
    data, store, prepared, objects, publisher = setup
    assert store.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store.connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    result = publisher.publish(prepared, now=NOW)
    assert result.state == "COMMITTED_RECONCILED"
    assert len(prepared.publication_id) == 64 and prepared.publication_id != GIT
    head = op.read_parent(store)
    assert head.publication_id == prepared.publication_id and head.generation == 1
    assert head.etag != sha256(head.raw).hexdigest()
    reopened = op.SQLiteObjectStore(tmp_path / "store.sqlite")
    assert op.Publisher(reopened, tmp_path / "receipts").reconcile(prepared) == result
    assert json.loads(reopened.get(prepared.manifest_key).raw)["source_git_commit"] == GIT
    reopened.close()


@pytest.mark.parametrize(
    "key",
    [
        "https://example.invalid/a",
        "s3://bucket/key",
        "../market/latest.json",
        "/market/latest.json",
        "market/latest.json?x=1",
        "market/%2e%2e/latest.json",
        "market/shards/" + "a" * 40 + ".json",
        "private/config.json",
        "market/latest.json/extra",
        "market/shards/" + "A" * 64 + ".json",
    ],
)
def test_key_allowlist_rejects_arbitrary_urls_and_aliases(key):
    with pytest.raises(ValueError, match="KEY_NOT_ALLOWED"):
        op.object_key(key)


def test_immutable_create_only_and_content_hash_not_etag(setup):
    _, store, _, objects, _ = setup
    key, raw = next(iter(objects.items()))
    stored = store.put(key, raw, if_none_match="*")
    assert stored.etag.startswith('"local-')
    with pytest.raises(op.PreconditionFailed):
        store.put(key, raw, if_none_match="*")
    with pytest.raises(ValueError, match="CONDITIONAL_WRITE_REQUIRED"):
        store.put(key, raw)
    with pytest.raises(ValueError, match="CONTENT_ADDRESS_MISMATCH"):
        store.put("market/shards/" + "a" * 64 + ".json", raw, if_none_match="*")


def test_current_physical_source_recheck_is_required_even_with_persistent_receipts(setup):
    data, store, prepared, _, publisher = setup
    publisher.publish(prepared, now=NOW)
    original = store.get(op.LATEST)
    shard = next(name for name in data if name.startswith("unified-market/objects/"))
    data[shard] += b" "
    with pytest.raises(ValueError):
        publisher.publish(prepared, now=NOW)
    assert store.get(op.LATEST) == original


def test_actual_filesystem_reader_detects_same_size_same_mtime_corruption(setup, tmp_path):
    data, store, _, _, publisher = setup
    source = tmp_path / "physical-source"
    for name, raw in data.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    calls = []

    def reader(name):
        calls.append(name)
        return (source / name).read_bytes()

    prepared, _ = op.prepare_market(reader, parent=None, source_git_commit=GIT, now=NOW)
    publisher.publish(prepared, now=NOW)
    before = store.get(op.LATEST)
    shard_name = next(name for name in data if name.startswith("unified-market/objects/"))
    path = source / shard_name
    metadata = path.stat()
    raw = path.read_bytes().replace(b'"open": "10"', b'"open": "11"', 1)
    assert len(raw) == metadata.st_size and raw != data[shard_name]
    path.write_bytes(raw)
    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    assert (
        path.stat().st_size == metadata.st_size and path.stat().st_mtime_ns == metadata.st_mtime_ns
    )
    with pytest.raises(ValueError, match="SHARD_CHECKSUM_INVALID"):
        publisher.publish(prepared, now=NOW)
    assert calls.count(shard_name) == 3
    assert store.get(op.LATEST) == before


@pytest.mark.parametrize(
    "damage", ["quality_universe", "catalog_lineage", "authority", "future", "quality_hash"]
)
def test_real_asset_validators_reject_semantic_mismatch_before_upload(setup, damage):
    data, store, _, _, _ = setup
    name = "quality.json" if damage.startswith("quality") else "unified-market.json"
    value = json.loads(data[name])
    if damage == "quality_universe":
        value["universe_version"] = "a" * 64
    elif damage == "catalog_lineage":
        value["master_sha256"] = "a" * 64
    elif damage == "authority":
        value["live_trading_enabled"] = True
    elif damage == "future":
        value["published_at"] = "2099-01-01T00:00:00+00:00"
    elif damage == "quality_hash":
        value["case_counts"] = {"COMPLETE": 2}
    data[name] = encoded(signed({k: v for k, v in value.items() if k != "evidence_sha256"}))
    with pytest.raises(ValueError):
        op.prepare_market(data.__getitem__, parent=None, source_git_commit=GIT, now=NOW)
    assert store.get(op.LATEST) is None


def test_same_digest_occurrence_is_not_validated_only_for_last_entry(setup):
    data, _, _, _, _ = setup
    index = json.loads(data["unified-market.json"])
    # Last original is correct, first reused reference is wrong for day frequency.
    index["entries"][0].update(state="AVAILABLE", shards=deepcopy(index["entries"][1]["shards"]))
    data["unified-market.json"] = encoded(
        signed({k: v for k, v in index.items() if k != "evidence_sha256"})
    )
    market.validate_index(json.loads(data["unified-market.json"]), NOW)
    with pytest.raises(ValueError, match="SHARD_INDEX_MISMATCH"):
        op.prepare_market(data.__getitem__, parent=None, source_git_commit=GIT, now=NOW)


def test_new_generation_parent_fences_stale_writer_and_failure_retains_head(setup, tmp_path):
    data, store, first, _, publisher = setup
    publisher.publish(first, now=NOW)
    parent = op.read_parent(store)
    second, _ = op.prepare_market(
        data.__getitem__, parent=parent, source_git_commit=GIT, now=NOW + timedelta(seconds=1)
    )
    stale, _ = op.prepare_market(
        data.__getitem__, parent=parent, source_git_commit=GIT, now=NOW + timedelta(seconds=2)
    )
    publisher.publish(second, now=NOW + timedelta(seconds=1))
    before = store.get(op.LATEST)
    with pytest.raises(ValueError, match="PARENT_FENCE_CHANGED"):
        publisher.publish(stale, now=NOW + timedelta(seconds=2))
    assert store.get(op.LATEST) == before and op.read_parent(store).generation == 2
    assert publisher.reconcile(first).state == "CONFLICT_OR_SUPERSEDED"


class FaultStore:
    def __init__(self, base, mode):
        self.base = base
        self.mode = mode
        self.scope_id = base.scope_id
        self.puts = 0
        self.pending = None

    def get(self, key):
        return self.base.get(key)

    def put(self, key, raw, **conditions):
        self.puts += 1
        if key == op.LATEST and self.mode in {"cas_late", "cas_ack_lost"}:
            self.pending = (key, raw, conditions)
            if self.mode == "cas_ack_lost":
                self.base.put(key, raw, **conditions)
            raise op.OutcomeUnknown("SYNTHETIC_ACK_UNKNOWN")
        if self.mode == "upload_late" and key != op.LATEST:
            self.pending = (key, raw, conditions)
            raise op.OutcomeUnknown("SYNTHETIC_UPLOAD_UNKNOWN")
        if self.mode == "upload_failure":
            raise OSError("SYNTHETIC_STORAGE_FAILURE")
        return self.base.put(key, raw, **conditions)


def test_lost_cas_ack_reconciles_without_reissuing_write(setup, tmp_path):
    _, store, prepared, _, _ = setup
    fault = FaultStore(store, "cas_ack_lost")
    publisher = op.Publisher(fault, tmp_path / "fault-receipts")
    result = publisher.publish(prepared, now=NOW)
    assert result.state == "COMMITTED_RECONCILED"
    count = fault.puts
    assert publisher.publish(prepared, now=NOW).state == "COMMITTED_RECONCILED"
    assert fault.puts == count


def test_negative_read_does_not_cancel_inflight_cas_and_restart_never_retries(setup, tmp_path):
    _, store, prepared, _, _ = setup
    fault = FaultStore(store, "cas_late")
    receipt_root = tmp_path / "late-receipts"
    result = op.Publisher(fault, receipt_root).publish(prepared, now=NOW)
    assert result.state == "UNCERTAIN_OLD_HEAD_OBSERVED" and store.get(op.LATEST) is None
    count = fault.puts
    assert op.Publisher(fault, receipt_root).publish(prepared, now=NOW) == result
    assert fault.puts == count
    key, raw, conditions = fault.pending
    store.put(key, raw, **conditions)  # Controlled delayed service commit.
    assert op.Publisher(fault, receipt_root).reconcile(prepared).state == "COMMITTED_RECONCILED"
    assert fault.puts == count


def test_uncertain_upload_get404_is_not_automatic_reupload(setup, tmp_path):
    _, store, prepared, _, _ = setup
    fault = FaultStore(store, "upload_late")
    publisher = op.Publisher(fault, tmp_path / "upload-late")
    with pytest.raises(op.OutcomeUnknown):
        publisher.publish(prepared, now=NOW)
    count = fault.puts
    with pytest.raises(op.OutcomeUnknown):
        publisher.publish(prepared, now=NOW)
    assert fault.puts == count and store.get(op.LATEST) is None


def test_upload_failure_preserves_old_head(setup, tmp_path):
    data, store, first, _, publisher = setup
    publisher.publish(first, now=NOW)
    parent = op.read_parent(store)
    before = store.get(op.LATEST)
    # Different source_git identity changes only immutable manifest, which fails.
    second, _ = op.prepare_market(
        data.__getitem__, parent=parent, source_git_commit="d" * 40, now=NOW + timedelta(seconds=1)
    )
    with pytest.raises(op.OutcomeUnknown):
        op.Publisher(FaultStore(store, "upload_failure"), tmp_path / "failed").publish(
            second, now=NOW + timedelta(seconds=1)
        )
    assert store.get(op.LATEST) == before


def test_remote_corrupt_bytes_with_same_etag_are_not_receipt_hits(setup):
    _, store, prepared, objects, publisher = setup
    publisher.publish(prepared, now=NOW)
    key = next(iter(objects))
    row = store.get(key)
    changed = row.raw + b" "
    # Corrupt store through fixture-only SQL, preserving ETag and self-consistent DB checksum.
    store.connection.execute(
        "UPDATE objects SET body=?,sha256=? WHERE key=?",
        (changed, sha256(changed).hexdigest(), key),
    )
    with pytest.raises(ValueError, match="COMMITTED_OBJECT_CORRUPT"):
        publisher.reconcile(prepared)


def test_journal_tamper_is_preserved_and_rejected(setup):
    _, _, prepared, _, publisher = setup
    publisher.publish(prepared, now=NOW)
    path = next(publisher.journal.root.glob("cas-intent-*.json"))
    raw = path.read_bytes()
    value = json.loads(raw)
    value["result"]["state"] = "FAKE"
    path.write_bytes(encoded(value))
    corrupt = path.read_bytes()
    with pytest.raises(ValueError, match="JOURNAL_CHECKSUM"):
        publisher.publish(prepared, now=NOW)
    assert path.read_bytes() == corrupt


def test_real_independent_sqlite_connections_allow_only_one_conditional_create(setup, tmp_path):
    _, store, prepared, _, _ = setup
    barrier = Barrier(2)

    def writer(number):
        connection = op.SQLiteObjectStore(tmp_path / "store.sqlite")
        barrier.wait()
        try:
            return connection.put(
                op.LATEST, b"synthetic-" + str(number).encode(), if_none_match="*"
            )
        except op.PreconditionFailed:
            return None
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(writer, range(2)))
    assert sum(result is not None for result in results) == 1
    assert store.get(op.LATEST).raw in {b"synthetic-0", b"synthetic-1"}


def test_summary_is_explicit_readonly_extension_with_separate_contract_identity(setup):
    data, _, _, _, publisher = setup
    data["summary.json"] = encoded(
        {**AUTHORITY, "schema_version": "SYNTHETIC_SUMMARY", "read_only": True}
    )
    with pytest.raises(ValueError, match="SUMMARY_VALIDATOR_REQUIRED"):
        op.prepare_market(
            data.__getitem__,
            parent=None,
            source_git_commit=GIT,
            now=NOW,
            summary_contract_sha256="a" * 64,
        )

    def validator(value, now):
        assert value["schema_version"] == "SYNTHETIC_SUMMARY"

    prepared, _ = op.prepare_market(
        data.__getitem__,
        parent=None,
        source_git_commit=GIT,
        now=NOW,
        summary_validator=validator,
        summary_contract_sha256="a" * 64,
    )
    assert publisher.publish(prepared, now=NOW).state == "COMMITTED_RECONCILED"
    assert "summary" in json.loads(prepared.manifest)["assets"]


@pytest.mark.parametrize("token", ["*", '"*"', 'W/"a"', '"a", "b"', "a\r\nb", " a"])
def test_etag_cannot_inject_wildcard_or_multiple_cas_conditions(token):
    with pytest.raises(ValueError, match="ETAG_INVALID"):
        op._etag(token)


def test_same_journal_concurrent_claim_has_exactly_one_owner(setup):
    *_, publisher = setup
    binding = {"publication_id": "a" * 64}
    barrier = Barrier(2)

    def claim(_):
        barrier.wait()
        return publisher.journal.write("cas-intent", binding, {"state": "INTENT"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, range(2))) == [False, True]


def test_two_real_publishers_compete_on_same_parent_without_partial_head(setup, tmp_path):
    data, store, *_ = setup
    barrier = Barrier(2)

    def writer(number):
        base = op.SQLiteObjectStore(tmp_path / "store.sqlite")

        class Racing:
            scope_id = base.scope_id

            def get(self, key):
                return base.get(key)

            def put(self, key, raw, **conditions):
                if key == op.LATEST:
                    barrier.wait(timeout=10)
                return base.put(key, raw, **conditions)

        now = NOW + timedelta(seconds=number)
        prepared, _ = op.prepare_market(
            data.__getitem__, parent=None, source_git_commit=GIT, now=now
        )
        try:
            result = op.Publisher(Racing(), tmp_path / f"racer-{number}").publish(prepared, now=now)
            return result, prepared
        finally:
            base.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(writer, (1, 2)))
    assert sorted(result.state for result, _ in results) == ["CAS_REJECTED", "COMMITTED_RECONCILED"]
    winner = next(
        prepared for result, prepared in results if result.state == "COMMITTED_RECONCILED"
    )
    assert store.get(op.LATEST).raw == winner.head
    assert (
        op.Publisher(store, tmp_path / "verify-race").reconcile(winner).state
        == "COMMITTED_RECONCILED"
    )


def test_journal_fsync_error_stops_before_any_remote_write(setup, tmp_path, monkeypatch):
    _, store, prepared, _, _ = setup
    publisher = op.Publisher(store, tmp_path / "durability-failure")

    def failed(*args):
        raise OSError("SYNTHETIC_FSYNC_FAILED")

    monkeypatch.setattr(op, "write_json_immutable", failed)
    with pytest.raises(OSError):
        publisher.publish(prepared, now=NOW)
    assert store.connection.execute("SELECT COUNT(*) FROM objects").fetchone()[0] == 0


def test_new_process_reopens_real_db_and_receipts_without_rewriting_latest(setup, tmp_path):
    data, store, prepared, _, publisher = setup
    publisher.publish(prepared, now=NOW)
    before = store.get(op.LATEST)
    source = tmp_path / "source-fixture.json"
    source.write_text(json.dumps({key: raw.decode() for key, raw in data.items()}))
    script = """
import json,sys,os
from datetime import datetime
from pathlib import Path
from qtrade.research import object_publication as op
from qtrade.research.object_publication import Publisher,SQLiteObjectStore,prepare_market
if os.name=='nt':op._sync_directory=lambda path:None
root=Path(sys.argv[1]);data=json.loads((root/'source-fixture.json').read_text())
reader=lambda name:data[name].encode()
prepared,_=prepare_market(reader,parent=None,source_git_commit='c'*40,
    now=datetime.fromisoformat('2026-10-28T12:00:00+00:00'))
store=SQLiteObjectStore(root/'store.sqlite')
result=Publisher(store,root/'receipts').publish(prepared,
    now=datetime.fromisoformat('2026-10-28T12:00:00+00:00'))
print(json.dumps({'state':result.state,'publication_id':result.publication_id}))
store.close()
"""
    repository = Path(__file__).resolve().parents[1]
    env = {
        # Runtime dependency search paths are metadata, not inherited secrets or
        # cloud configuration. This workstation keeps bundled pyarrow off the
        # base Python's default site path; Linux also runs the genuine new process.
        "PYTHONPATH": os.pathsep.join(
            [str(repository / "src"), str(repository), *[path for path in sys.path if path]]
        ),
        "PYTHONSAFEPATH": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LIVE_TRADING_ENABLED": "false",
    }
    if os.name == "nt":
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    result = subprocess.run(
        [sys.executable, "-B", "-P", "-c", script, str(tmp_path)],
        capture_output=True,
        timeout=15,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "state": "COMMITTED_RECONCILED",
        "publication_id": prepared.publication_id,
    }
    assert store.get(op.LATEST) == before


def test_reconcile_rejects_other_valid_manifest_using_committed_publication_id(setup):
    data, store, first, _, publisher = setup
    publisher.publish(first, now=NOW)
    before = store.get(op.LATEST)
    other, _ = op.prepare_market(data.__getitem__, parent=None, source_git_commit="d" * 40, now=NOW)
    forged = replace(other, publication_id=first.publication_id)
    assert forged.head == first.head
    with pytest.raises(ValueError, match="PREPARED_MANIFEST_ID"):
        publisher.reconcile(forged)
    assert store.get(op.LATEST) == before


@pytest.mark.parametrize(
    "field", ["source_git_commit", "created_at", "parent", "summary_contract_sha256"]
)
def test_reconcile_checks_all_prepared_context_before_any_remote_read(setup, field):
    _, store, first, _, _ = setup
    changes = {
        "source_git_commit": "d" * 40,
        "created_at": NOW + timedelta(seconds=1),
        "parent": op.Parent(first.publication_id, 1, '"synthetic-parent"', first.head),
        "summary_contract_sha256": "a" * 64,
    }

    class NoRead:
        scope_id = store.scope_id

        def get(self, key):
            raise AssertionError("Invalid prepared context must stop before remote access")

    publisher = op.Publisher(NoRead(), store.path.parent / ("invalid-" + field))
    with pytest.raises(ValueError, match="PREPARED_.*CONTEXT"):
        publisher.reconcile(replace(first, **{field: changes[field]}))


def test_reconcile_compares_actual_manifest_bytes_after_read_parent(setup, tmp_path):
    _, store, first, _, publisher = setup
    publisher.publish(first, now=NOW)
    before = store.get(op.LATEST)
    changed = json.loads(first.manifest)
    changed["source_git_commit"] = "d" * 40
    changed_raw = op.canonical_json(changed)

    class ChangedManifest:
        scope_id = store.scope_id
        reads = 0

        def get(self, key):
            stored = store.get(key)
            if key == first.manifest_key:
                self.reads += 1
                if self.reads == 2:
                    return op.StoredObject(changed_raw, stored.etag)
            return stored

    changed_store = ChangedManifest()
    with pytest.raises(ValueError, match="COMMITTED_MANIFEST_CONTEXT"):
        op.Publisher(changed_store, tmp_path / "manifest-race").reconcile(first)
    assert changed_store.reads == 2 and store.get(op.LATEST) == before
