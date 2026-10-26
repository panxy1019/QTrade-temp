import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from qtrade.data import Bar
from qtrade.market_data.akshare import SHANGHAI
from qtrade.market_data.sina_incremental import IncrementalBar
from scripts import unified_store as module
from scripts.unified_store import UnifiedBatchStore

NOW = datetime(2026, 10, 26, 9, tzinfo=UTC)
BATCH = "a" * 32


@pytest.fixture
def store(tmp_path, monkeypatch):
    if os.name == "nt":
        # Linux integration verifies real directory fsync. On Windows it is unavailable.
        monkeypatch.setattr(module, "_fsync_directory", lambda _: None)
        original = module.write_json_immutable

        def windows_write(path, value):
            try:
                original(path, value)
            except PermissionError:
                assert path.is_file() and json.loads(path.read_text()) == value

        monkeypatch.setattr(module, "write_json_immutable", windows_write)
    return UnifiedBatchStore(tmp_path)


def capture(store, *, body=b"invalid response retained before parsing", **extra):
    return store.capture([{
        "source_id": "sina_klines", "received_at": NOW.isoformat(), "status_code": 200,
        "request": {"symbol": "sh600036", "scale": "5", "ma": "no", "datalen": "96"},
        "body": body, "transport_error": None, "body_complete": True, **extra,
    }])


def spec():
    return {"frequency": "5min", "target_end": "2026-10-26T15:00:00+08:00",
            "universe_version": "b" * 64, "symbols": ["600036"], "code_revision": "c" * 40,
            "after_by_symbol": {"600036": None}}


def bars():
    stamp = datetime(2026, 10, 26, 15, tzinfo=SHANGHAI)
    bar = Bar("600036", "SSE", stamp, stamp.date(), *[Decimal("10")] * 4, Decimal("100"))
    return [IncrementalBar(bar, "5min", Decimal("1000"), Decimal("10"))]


def commit(store, raw, records=None, outcomes=None, config=None):
    return store.commit(BATCH, config or spec(), raw, bars() if records is None else records,
                        outcomes or {"600036": {"state": "SUCCEEDED"}}, completed_at=NOW)


def test_raw_capture_survives_bad_parsing_and_preserves_exact_bytes(store):
    body = b"not JSON\x00\xff\n"
    raw = capture(store, body=body, status_code=503)
    assert store.read_captures(raw)[0]["body"] == body
    assert store.read_captures(raw)[0]["status_code"] == 503
    assert capture(store, body=body, status_code=503) == raw
    with pytest.raises(ValueError, match="CAPTURE_FIELDS"):
        capture(store, headers={"unexpected": "not recorded"})
    with pytest.raises(ValueError, match="PUBLIC_SOURCE"):
        capture(store, request={"symbol": "sh600036", "unexpected": "not recorded"})


def test_batch_is_immutable_idempotent_and_restores_after_new_store_instance(store):
    raw = capture(store)
    result = commit(store, raw)
    assert commit(store, raw) == result
    restored = UnifiedBatchStore(store.root).load(BATCH)
    assert restored == result and restored["bar_count"] == 1
    assert restored["outcomes"]["600036"]["end"] == "2026-10-26T15:00:00+08:00"
    assert restored["point_in_time"] is False and restored["live_trading_enabled"] is False
    original = store.batch_path(BATCH).read_bytes()
    with pytest.raises(ValueError, match="IDEMPOTENCY"):
        commit(store, capture(store, body=b"changed input"))
    with pytest.raises(ValueError, match="IDEMPOTENCY"):
        commit(store, raw, records=[replace(bars()[0], amount=Decimal("999"))])
    assert store.batch_path(BATCH).read_bytes() == original


def test_failed_batch_has_raw_evidence_without_a_fabricated_empty_dataset(store):
    value = commit(store, capture(store), records=[], outcomes={
        "600036": {"state": "FAILED", "error_code": "NO_COMPLETED_REQUESTED_BARS_REASON_UNKNOWN"},
    })
    assert value["normalized"] is None and value["bar_count"] == 0
    assert store.load(BATCH) == value


def test_corrupted_blob_and_forged_success_cannot_be_replayed(store):
    raw = capture(store)
    result = commit(store, raw)
    target = store._blob_path(result["normalized"]["sha256"], ".parquet")
    original = target.read_bytes()
    target.write_bytes(b"x" * len(original))
    with pytest.raises(ValueError, match="CHECKSUM"):
        store.load(BATCH)
    with pytest.raises(ValueError, match="CONFLICT"):
        store._put_blob(original, ".parquet")


@pytest.mark.parametrize("change", ["target", "duplicate", "outcome", "lineage"])
def test_bad_scope_rows_outcomes_and_lineage_are_rejected(store, change):
    config, records, outcomes = spec(), bars(), {"600036": {"state": "SUCCEEDED"}}
    if change == "target":
        config["target_end"] = "2026-10-26T14:55:00+08:00"
    if change == "duplicate":
        records *= 2
    if change == "outcome":
        outcomes["600036"]["state"] = "FAILED"
    raw = capture(store, request={"symbol": "sz600036", "scale": "5", "ma": "no", "datalen": "96"})
    if change != "lineage":
        raw = capture(store)
    with pytest.raises(ValueError):
        commit(store, raw, records=records, outcomes=outcomes, config=config)
    assert not store.batch_path(BATCH).exists()


def test_content_addressed_paths_cannot_escape_the_storage_root(store, tmp_path: Path):
    with pytest.raises(ValueError):
        store._blob_path("../escape", ".parquet")
    with pytest.raises(ValueError):
        store.batch_path("../escape")
