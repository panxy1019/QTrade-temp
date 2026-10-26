"""Daily preparation binds terminal source-ledger rows, not rotating progress snapshots."""

import json
import shutil
import sqlite3
from types import SimpleNamespace

import pytest
from test_prepare_qlib_incremental import inputs as stock_inputs  # noqa: F401
from test_prepare_qlib_incremental import windows_atomic_writes  # noqa: F401
from test_qlib_data_service import NOW, runtime  # noqa: F401

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts import prepare_qlib_incremental as preparation
from scripts import run_qlib_data_service as service


@pytest.fixture
def daily(stock_inputs, tmp_path):  # noqa: F811
    previous, _, stock, batch, store, _, fetch = stock_inputs
    base = tmp_path
    lake = base / "unified-data/lake"
    shutil.copytree(store.root, lake)
    dataset = signed(
        {
            **AUTHORITY,
            "end": "2026-10-23",
            "stocks": [stock],
            "incremental": {"observation_universe_version": "b" * 64},
        }
    )
    preparation.write_json_immutable(previous / "dataset.json", dataset)
    preparation.write_json_immutable(
        base / "unified-data/collection-progress.json",
        signed(
            {
                **AUTHORITY,
                "universe_version": "b" * 64,
                "state": "PASS_FINISHED",
                "required_through": {"5min": "2026-10-26T07:00:00+00:00"},
                "tasks": [
                    {
                        "symbol": "600036",
                        "frequency": "5min",
                        "state": "SUCCEEDED",
                        "trading_day": "2026-10-26",
                        "last_success_batch": "c" * 32,
                    }
                ],
            }
        ),
    )
    master = signed(
        {
            **AUTHORITY,
            "universe_version": "b" * 64,
            "securities": [
                {"symbol": "600036", "in_target": True},
                {"symbol": "510050", "in_target": True},
            ],
        }
    )
    preparation.write_json_immutable(
        base / "unified-data/public/unified.json", signed({**AUTHORITY, "master": master})
    )
    database = base / "collection.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE collection_tasks(universe_version TEXT, frequency TEXT,"
            "symbol TEXT,trading_day TEXT,security_type TEXT,desired_end TEXT,completed_end TEXT,"
            "state TEXT,last_success_batch TEXT)"
        )
        connection.executemany(
            "INSERT INTO collection_tasks VALUES(?,?,?,?,?,?,?,?,?)",
            [
                (
                    "b" * 64,
                    "day",
                    "600036",
                    "2026-10-26",
                    "STOCK",
                    "2026-10-26T07:00:00+00:00",
                    "2026-10-26T07:00:00+00:00",
                    "SUCCEEDED",
                    batch["batch_id"],
                ),
                (
                    "b" * 64,
                    "day",
                    "510050",
                    "2026-10-26",
                    "ETF",
                    "2026-10-26T07:00:00+00:00",
                    None,
                    "FAILED",
                    None,
                ),
                (
                    "e" * 64,
                    "day",
                    "600036",
                    "2026-10-26",
                    "STOCK",
                    "2026-10-26T07:00:00+00:00",
                    "2026-10-26T07:00:00+00:00",
                    "SUCCEEDED",
                    "f" * 32,
                ),
            ],
        )
    return SimpleNamespace(
        base=base, previous=previous, database=database, batch=batch, fetch=fetch, lake=lake
    )


def run(data, *, ledger=True):
    return preparation.run(
        data.base,
        data.previous,
        revision="c" * 40,
        maximum=1,
        day="2026-10-26",
        collection_database=data.database if ledger else None,
    )


def test_exact_terminal_ledger_ignores_rotating_minute_snapshot_and_is_read_only(
    daily, monkeypatch
):
    before = daily.database.read_bytes()
    original = preparation.UnifiedBatchStore.load

    def adapt(*args, **kwargs):
        snapshots = list(
            (daily.base / "unified-data/qlib-preparation/input-scopes").glob("*/collection.json")
        )
        assert len(snapshots) == 1  # Metadata is durable before the first batch is read.
        return original(*args, **kwargs)

    monkeypatch.setattr(preparation.UnifiedBatchStore, "load", adapt)
    result = run(daily)
    assert result["prepared_symbols"] == result["qualified_current_daily_inputs"] == 1
    request = preparation.load_signed(
        daily.base / "unified-data/qlib-preparation" / result["request_sha256"] / "request.json"
    )
    assert request["universe_version"] == "b" * 64
    assert request["batches"] == {"600036": daily.batch["batch_id"]}
    assert request["batch_manifests"] == {daily.batch["batch_id"]: daily.batch["evidence_sha256"]}
    assert daily.database.read_bytes() == before
    with sqlite3.connect(daily.database) as connection:
        assert [
            r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ] == ["collection_tasks"]


def test_frozen_bindings_resume_after_collector_ids_and_progress_rotate(daily):
    first = run(daily)
    with sqlite3.connect(daily.database) as connection:
        connection.execute(
            "UPDATE collection_tasks SET last_success_batch=? WHERE state='SUCCEEDED'", ("f" * 32,)
        )
    # The later collector pointer is not silently substituted into an existing preparation.
    second = run(daily)
    assert second["request_sha256"] == first["request_sha256"]
    assert second["prepared_symbols"] == 1
    daily.fetch.assert_called_once()


@pytest.mark.parametrize("defect", ["pending", "missing", "wrong_universe"])
def test_incomplete_exact_daily_scope_waits_without_freezing_inputs_or_fetching(daily, defect):
    with sqlite3.connect(daily.database) as connection:
        if defect == "pending":
            connection.execute("UPDATE collection_tasks SET state='PLANNED' WHERE symbol='510050'")
        elif defect == "missing":
            connection.execute("DELETE FROM collection_tasks WHERE symbol='510050'")
        else:
            connection.execute("DELETE FROM collection_tasks WHERE universe_version=?", ("b" * 64,))
    assert run(daily)["state"] == "WAITING_FOR_CURRENT_DAILY_INPUTS"
    daily.fetch.assert_not_called()
    assert not list(
        (daily.base / "unified-data/qlib-preparation").glob("input-scopes/*/collection.json")
    )


def test_missing_daily_snapshot_without_ledger_waits_instead_of_keyerror(daily):
    assert run(daily, ledger=False)["state"] == "WAITING_FOR_CURRENT_DAILY_INPUTS"
    daily.fetch.assert_not_called()


def test_corrupt_actual_batch_is_not_a_qualified_input_despite_successful_task(daily):
    ref = daily.batch["normalized"]
    blob = daily.lake / "objects" / (ref["sha256"] + ref["suffix"])
    blob.write_bytes(b"X" * blob.stat().st_size)
    result = run(daily)
    assert result["qualified_current_daily_inputs"] == result["prepared_symbols"] == 0
    assert result["failures"][0]["reason"] == "CURRENT_DAILY_BATCH_VERIFICATION_FAILED"
    assert result["provider_published"] is False
    daily.fetch.assert_not_called()


def test_service_without_daily_snapshot_waits_before_job_intent(runtime):  # noqa: F811
    invoke, _, _, calls, base = runtime
    path = base / "unified-data/collection-progress.json"
    path.write_text(
        json.dumps(signed({**AUTHORITY, "required_through": {"5min": "2026-10-26T07:00:00+00:00"}}))
    )
    assert invoke()["state"] == "WAITING_FOR_CURRENT_DAILY_INPUTS"
    assert calls == []
    assert not (base / "unified-data/qlib-schedule/days/2026-10-26/request.json").exists()


def test_service_propagates_exact_day_universe_and_readonly_database(runtime, monkeypatch):  # noqa: F811
    _, current, prepared, calls, base = runtime
    current["bindings"] = {"observation_universe_version": "b" * 64}
    path = base / "unified-data/collection-progress.json"
    path.write_text(
        json.dumps(signed({**AUTHORITY, "required_through": {"5min": NOW.isoformat()}}))
    )
    seen = []

    def prepare(*args, **kwargs):
        seen.append(kwargs)
        return prepared

    monkeypatch.setattr(service, "prepare", prepare)
    monkeypatch.setattr(service, "daily_tasks", lambda *args: [{"state": "SUCCEEDED"}])
    preparation.write_json_atomic(base / "old/dataset.json", signed({**AUTHORITY, "stocks": []}))
    database = base / "actual-collection.sqlite"
    result = service.cycle(
        base, base / "db.sqlite", base / "python", "f" * 40, now=NOW, collection_database=database
    )
    assert result["state"] == "DAILY_VERSION_PUBLISHED"
    assert seen == [
        {
            "revision": "f" * 40,
            "maximum": 25,
            "collection_database": database,
            "day": "2026-10-26",
            "universe_version": "b" * 64,
        }
    ]
    assert calls == ["build"]


def test_empty_ledger_waits_before_day_intent_or_attempt(runtime):  # noqa: F811
    _, current, _, calls, base = runtime
    current["bindings"] = {"observation_universe_version": "b" * 64}
    preparation.write_json_atomic(
        base / "old/dataset.json", signed({**AUTHORITY, "stocks": [{"symbol": "600036"}]})
    )
    database = base / "collection.sqlite"
    with sqlite3.connect(database) as c:
        c.execute(
            "CREATE TABLE collection_tasks(symbol TEXT,security_type TEXT,state TEXT,"
            "desired_end TEXT,completed_end TEXT,last_success_batch TEXT,universe_version TEXT,"
            "frequency TEXT,trading_day TEXT)"
        )
    result = service.cycle(
        base, base / "db.sqlite", base / "python", "f" * 40, now=NOW, collection_database=database
    )
    assert result["state"] == "WAITING_FOR_CURRENT_DAILY_INPUTS" and calls == []
    assert not (base / "unified-data/qlib-schedule/days/2026-10-26/request.json").exists()
    assert not (base / "unified-data/qlib-schedule/days/2026-10-26/attempt.json").exists()


def test_transient_receipt_io_retries_frozen_ids_without_permanent_exclusion(daily, monkeypatch):
    original = preparation.UnifiedBatchStore.load
    attempted = []

    def read(store, identity):
        attempted.append(identity)
        if len(attempted) == 1:
            raise OSError("temporary mount read failure")
        return original(store, identity)

    monkeypatch.setattr(preparation.UnifiedBatchStore, "load", read)
    with pytest.raises(OSError, match="temporary mount"):
        run(daily)
    scope = next((daily.base / "unified-data/qlib-preparation/input-scopes").iterdir())
    assert (scope / "collection.json").exists() and not (scope / "request.json").exists()
    snapshot = (scope / "collection.json").read_bytes()
    recovered = run(daily)
    assert recovered["prepared_symbols"] == recovered["qualified_current_daily_inputs"] == 1
    assert (scope / "collection.json").read_bytes() == snapshot
    assert all(identity == daily.batch["batch_id"] for identity in attempted)


def test_due_new_work_precedes_slow_completed_checkpoint_validation(daily, monkeypatch):
    dataset = preparation.load_signed(daily.previous / "dataset.json")
    dataset["stocks"].append({**dataset["stocks"][0], "symbol": "600037"})
    dataset = signed({k: v for k, v in dataset.items() if k != "evidence_sha256"})
    preparation.write_json_atomic(daily.previous / "dataset.json", dataset)
    request = signed(
        {
            **AUTHORITY,
            "base_dataset_sha256": dataset["evidence_sha256"],
            "universe_version": "b" * 64,
            "day": "2026-10-26",
            "code_revision": "c" * 40,
            "batches": {"600036": daily.batch["batch_id"], "600037": daily.batch["batch_id"]},
            "batch_manifests": {daily.batch["batch_id"]: daily.batch["evidence_sha256"]},
            "excluded_current_inputs": {},
        }
    )
    output = daily.base / "unified-data/qlib-preparation" / request["evidence_sha256"]
    preparation.write_json_immutable(
        output / "stocks/600036/manifest.json", signed({"symbol": "600036"})
    )
    # This unit isolates work ordering after the source request has already been verified.
    monkeypatch.setattr(preparation, "preparation_request", lambda *args, **kwargs: request)
    elapsed = {"seconds": 0}
    monkeypatch.setattr(preparation, "time", SimpleNamespace(monotonic=lambda: elapsed["seconds"]))
    seen = []

    def adapt(previous, output, row, *args):
        seen.append(row["symbol"])
        if row["symbol"] == "600036":
            elapsed["seconds"] = 181
        else:
            preparation.write_json_immutable(
                output / "stocks/600037/manifest.json", signed({"symbol": "600037"})
            )
        return {"symbol": row["symbol"]}

    monkeypatch.setattr(preparation, "prepare_stock", adapt)
    result = run(daily)
    assert seen == ["600037", "600036"]
    assert (
        result["attempted_new_symbols"] == 1 and result["retry_queue"]["unattempted_symbols"] == 0
    )


def test_frozen_scope_binds_source_revision_and_metadata_digest(daily):
    first = run(daily)
    request = (
        daily.base / "unified-data/qlib-preparation" / first["request_sha256"] / "request.json"
    )
    before = request.read_bytes()
    scope = next((daily.base / "unified-data/qlib-preparation/input-scopes").iterdir())
    snapshot = preparation.load_signed(scope / "collection.json")
    snapshot["context"]["code_revision"] = "d" * 40
    snapshot = signed({k: v for k, v in snapshot.items() if k != "evidence_sha256"})
    preparation.write_json_atomic(scope / "collection.json", snapshot)
    with pytest.raises(ValueError, match="FROZEN_COLLECTION_CONTEXT_CHANGED"):
        run(daily)
    assert request.read_bytes() == before
