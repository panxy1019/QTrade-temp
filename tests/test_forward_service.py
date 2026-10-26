import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from qtrade.domain import RuntimeMode
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.forward_paper import ForwardPaperBook as RealBook
from qtrade.research.forward_paper import ForwardTarget, ObservedSecurity, ObservedSession
from qtrade.research.forward_publication import validate_forward_publication
from scripts import run_qlib_forward_service as service
from scripts.qlib_forward_paper_adapter import public_book_summary


class Book:
    recovered = []

    def __init__(self, root, *, strategy_id, mode, **kwargs):
        root.mkdir(parents=True, exist_ok=True)
        self.strategy_id, self.mode = strategy_id, mode
        self.db = sqlite3.connect(root / "forward.sqlite")
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS targets "
            "(id TEXT PRIMARY KEY,payload TEXT,execution_at TEXT); "
            "CREATE TABLE IF NOT EXISTS sessions(day TEXT,target_id TEXT,status TEXT)"
        )
        self.bundle = SimpleNamespace(
            runtime=SimpleNamespace(status=SimpleNamespace(running=False)),
            ledger=SimpleNamespace(account=SimpleNamespace(positions={})),
            report_incident=lambda _: setattr(self.bundle.runtime.status, "running", False),
        )

    def recover(self, *, actor):
        self.recovered.append((self.strategy_id, actor))
        self.bundle.runtime.status.running = True

    def freeze(self, target, *, now):
        if now >= target.execution_at:
            raise ValueError("TARGET_TOO_LATE")
        from dataclasses import asdict

        self.db.execute(
            "INSERT OR IGNORE INTO targets VALUES(?,?,?)",
            (
                target.identity,
                json.dumps(asdict(target), default=str),
                target.execution_at.isoformat(),
            ),
        )
        self.db.commit()

    def observe(self, target, session, *, now):
        self.db.execute(
            "INSERT INTO sessions VALUES(?,?,'COMPLETE')",
            (session.trading_day.isoformat(), target.identity),
        )
        self.db.commit()


@pytest.fixture
def environment(tmp_path, monkeypatch):
    def atomic(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def immutable(path, value):
        if path.exists():
            raise FileExistsError(path)
        atomic(path, value)

    monkeypatch.setattr(service, "write_json_atomic", atomic)
    monkeypatch.setattr(service, "write_json_immutable", immutable)
    monkeypatch.setattr(service, "ForwardPaperBook", Book)
    monkeypatch.setattr(service, "current_epoch", lambda *args: "e" * 64)
    monkeypatch.setattr(service, "validate_forward_publication", lambda value, **kwargs: value)
    monkeypatch.setattr(service.shutil, "disk_usage", lambda _: SimpleNamespace(free=200 * 1024**3))
    monkeypatch.setattr(
        service,
        "public_book_summary",
        lambda book, **kwargs: {"strategy_id": book.strategy_id, "mode": book.mode.value},
    )
    Book.recovered = []

    def create(**kwargs):
        for name in ("data", "models"):
            sqlite3.connect(tmp_path / name).close()
        return service.ForwardService(
            tmp_path / "base",
            tmp_path / "local",
            tmp_path / "workflow",
            tmp_path / "models",
            tmp_path / "data",
            "a" * 40,
            **kwargs,
        )

    return create


def target():
    now = datetime.now(UTC)
    return ForwardTarget(
        "momentum20",
        "b" * 64,
        "c" * 64,
        "a" * 40,
        now - timedelta(seconds=5),
        now + timedelta(days=1),
        {"SH600036": Decimal(".5")},
        ("Research only",),
    )


def link():
    return {
        "kind": "signal-family",
        "artifact_id": "b" * 64,
        "manifest_sha256": "d" * 64,
        "strategy_id": "momentum20",
        "model_id": None,
        "data_event_id": "e" * 64,
    }


def test_only_explicit_pristine_paper_initializes_never_restart(environment):
    instance = environment(modes=(RuntimeMode.PAPER,), initialize_paper="owner")
    instance.register(target(), link(), now=datetime.now(UTC))
    assert Book.recovered == [("momentum20", "owner")]
    restored = environment(modes=(RuntimeMode.PAPER,), initialize_paper="owner")
    assert Book.recovered == [("momentum20", "owner")]
    assert all(not b.bundle.runtime.status.running for b in restored.books.values())
    assert restored.cycle(stopped=True)["state"] == "STOPPED"


def test_default_shadow_never_recovers_or_initializes_paper(environment):
    instance = environment()
    instance.register(target(), link(), now=datetime.now(UTC))
    assert [b.mode for b in instance.books.values()] == [RuntimeMode.SHADOW]
    assert not Book.recovered


def test_stale_new_inputs_do_not_prevent_processing_existing_frozen_book(environment, monkeypatch):
    instance = environment()
    instance.register(target(), link(), now=datetime.now(UTC))

    def failure(now):
        raise ValueError("FORWARD_SERVICE_CURRENT_INPUTS_STALE")

    monkeypatch.setattr(instance, "current_targets", failure)
    observed = []
    monkeypatch.setattr(instance, "observe_book", lambda book, **kwargs: observed.append(book))
    result = instance.cycle()
    assert len(observed) == 1
    assert result["state"] == "DEGRADED"
    assert result["errors"][0]["code"] == "FORWARD_SERVICE_CURRENT_INPUTS_STALE"


def test_storage_reserve_blocks_freeze_and_observe(environment, monkeypatch):
    instance = environment()
    monkeypatch.setattr(service.shutil, "disk_usage", lambda _: SimpleNamespace(free=99 * 1024**3))
    monkeypatch.setattr(instance, "current_targets", lambda _: pytest.fail("storage gate skipped"))
    result = instance.cycle()
    assert result["errors"][0]["code"] == "FORWARD_SERVICE_STORAGE_RESERVE"


def test_errors_in_one_book_do_not_skip_other_books(environment, monkeypatch):
    instance = environment(modes=(RuntimeMode.SHADOW, RuntimeMode.PAPER))
    instance.register(target(), link(), now=datetime.now(UTC))
    monkeypatch.setattr(instance, "current_targets", lambda _: [])
    visited = []

    def observe(book, **kwargs):
        visited.append(book.mode)
        if book.mode is RuntimeMode.SHADOW:
            raise ValueError("FORWARD_SERVICE_MISSING_SESSION")

    monkeypatch.setattr(instance, "observe_book", observe)
    result = instance.cycle()
    assert len(visited) == 2 and result["state"] == "HUMAN_RECOVERY_REQUIRED"
    assert result["errors"][0]["code"] == "FORWARD_SERVICE_MISSING_SESSION"


def test_epoch_changed_before_freeze_rejects_prospective_admission(environment, monkeypatch):
    instance = environment()
    monkeypatch.setattr(service, "current_epoch", lambda *args: "f" * 64)
    with pytest.raises(ValueError, match="EPOCH_CHANGED"):
        instance.register(target(), link(), now=datetime.now(UTC))
    assert instance.books == {}


def test_missing_observation_day_does_not_skip_ahead(environment, monkeypatch):
    instance = environment()
    item = target()
    instance.register(item, link(), now=datetime.now(UTC))
    now = item.execution_at + timedelta(days=2)
    monkeypatch.setattr(instance.calendar, "expected", lambda *_: now)
    progress = signed({**AUTHORITY, "required_through": {"day": now.isoformat()}, "tasks": []})
    service.write_json_atomic(instance.base / "unified-data/collection-progress.json", progress)
    book = next(iter(instance.books.values()))
    with pytest.raises(ValueError, match="MARKET_DAY_UNAVAILABLE"):
        instance.observe_book(book, now=now)
    assert book.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_live_and_unrequested_paper_initialization_are_rejected(environment, monkeypatch):
    with pytest.raises(ValueError, match="CONFIGURATION_INVALID"):
        environment(initialize_paper="owner")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(ValueError, match="CONFIGURATION_INVALID"):
        environment()


def test_atomic_admission_reserves_both_ledgers_without_mutation(environment, monkeypatch):
    instance = environment()
    original = Book.freeze
    checked = []

    def freeze(book, item, *, now):
        for database in (instance.data_database, instance.model_database):
            with (
                sqlite3.connect(database, timeout=0) as competing,
                pytest.raises(sqlite3.OperationalError, match="locked"),
            ):
                competing.execute("BEGIN IMMEDIATE")
            checked.append(database)
        original(book, item, now=now)

    monkeypatch.setattr(Book, "freeze", freeze)
    before = [p.read_bytes() for p in (instance.data_database, instance.model_database)]
    instance.register(target(), link(), now=datetime.now(UTC))
    assert len(checked) == 2
    assert [p.read_bytes() for p in (instance.data_database, instance.model_database)] == before


def test_epoch_change_during_freeze_leaves_unadmitted_target(environment, monkeypatch):
    instance = environment()
    original = Book.freeze
    item = target()

    def freeze(book, item, *, now):
        original(book, item, now=now)
        monkeypatch.setattr(service, "current_epoch", lambda *args: "f" * 64)

    monkeypatch.setattr(Book, "freeze", freeze)
    with pytest.raises(ValueError, match="EPOCH_CHANGED"):
        instance.register(item, link(), now=datetime.now(UTC))
    identity, book = next(iter(instance.books.items()))
    assert not (instance.root / identity / "admissions" / (item.identity + ".json")).exists()
    with pytest.raises(ValueError, match="ADMISSION_RECONCILIATION_REQUIRED"):
        instance.observe_book(book, now=item.execution_at + timedelta(days=1))
    monkeypatch.setattr(service, "current_epoch", lambda *args: "e" * 64)
    with pytest.raises(ValueError, match="ADMISSION_RECONCILIATION_REQUIRED"):
        instance.register(item, link(), now=datetime.now(UTC))


def test_restart_shadow_excludes_persisted_paper(environment):
    instance = environment(modes=(RuntimeMode.PAPER, RuntimeMode.SHADOW))
    instance.register(target(), link(), now=datetime.now(UTC))
    restored = environment()
    assert [book.mode for book in restored.books.values()] == [RuntimeMode.SHADOW]


def test_signed_real_book_and_service_publication_contract(environment, monkeypatch):
    monkeypatch.setattr(service, "ForwardPaperBook", RealBook)
    monkeypatch.setattr(service, "public_book_summary", public_book_summary)
    monkeypatch.setattr(service, "validate_forward_publication", validate_forward_publication)
    instance = environment(modes=(RuntimeMode.SHADOW, RuntimeMode.PAPER), initialize_paper="owner")
    item = target()
    instance.register(item, link(), now=datetime.now(UTC))
    monkeypatch.setattr(instance, "current_targets", lambda now: [(item, link())])
    result = instance.cycle()
    assert result["state"] == "RUNNING" and len(result["books"]) == 2
    for book in result["books"]:
        assert book["target_count"] == 1 and book["sessions"] == []
        assert book["frozen_targets"][0]["weight_count"] == 1
        assert book["frozen_targets"][0]["weights"][0]["weight"] == "0.5"
    stopped = instance.cycle(stopped=True)
    assert validate_forward_publication(stopped)["state"] == "STOPPED"
    # Synthetic test clocks exercise schema/accounting, never production forward evidence.
    day = item.execution_at.date()
    market = ObservedSession(
        day,
        day - timedelta(days=1),
        day + timedelta(days=1),
        item.execution_at,
        item.execution_at + timedelta(hours=1),
        item.execution_at + timedelta(hours=2),
        "f" * 64,
        "9" * 64,
        {
            "SH600036": ObservedSecurity(
                "SH600036",
                "SSE",
                Decimal("10"),
                Decimal("11"),
                1000000,
                100,
                Decimal(".01"),
                None,
                None,
                None,
                None,
                None,
                "8" * 64,
            ),
        },
    )
    for book in instance.books.values():
        book.observe(item, market, now=market.observed_at)
    completed = instance.cycle(now=market.observed_at)
    assert validate_forward_publication(completed, now=market.observed_at)["state"] == "RUNNING"
    for book in completed["books"]:
        receipt = book["sessions"][0]
        assert receipt["blocked_count"] == 1 and receipt["fill_count"] == 0
        assert (
            receipt["forward_return"] is None
            if book["mode"] == "SHADOW"
            else (Decimal(receipt["forward_return"]) == 0)
        )


def test_collection_database_replays_exact_old_day_and_stays_read_only(environment, tmp_path):
    database = tmp_path / "collection.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE collection_tasks "
            "(symbol TEXT,state TEXT,last_success_batch TEXT,"
            "frequency TEXT,trading_day TEXT)"
        )
        connection.executemany(
            "INSERT INTO collection_tasks VALUES(?,?,?,?,?)",
            [
                ("600036", "SUCCEEDED", "a" * 32, "day", "2026-10-26"),
                ("600036", "SUCCEEDED", "b" * 32, "day", "2026-10-27"),
            ],
        )
    before = database.read_bytes()
    instance = environment(collection_database=database)
    rows = instance.collection_tasks("2026-10-26", ["SH600036"])
    assert rows == [{"symbol": "600036", "state": "SUCCEEDED", "last_success_batch": "a" * 32}]
    assert database.read_bytes() == before
    with (
        service.readonly(database) as connection,
        pytest.raises(sqlite3.OperationalError, match="readonly"),
    ):
        connection.execute("DELETE FROM collection_tasks")


def test_source_discovery_uses_verified_current_epoch_and_actual_archive(environment, monkeypatch):
    instance = environment()
    item = target()
    now = datetime.now(UTC)
    online = signed(
        {
            **AUTHORITY,
            "worker": {"heartbeat_at": now.isoformat(), "source_revision": "a" * 40},
            "data_version": {
                "version_id": item.data_version,
                "data_event_id": "e" * 64,
                "actual_end": instance.calendar.expected(now, "daily").date().isoformat(),
            },
        }
    )
    service.write_json_atomic(instance.base / "workflow/public/online.json", online)
    with sqlite3.connect(instance.workflow_database) as connection:
        connection.execute("CREATE TABLE jobs(id TEXT, request TEXT, result TEXT, state TEXT)")
        connection.execute(
            "INSERT INTO jobs VALUES(?,?,?,?)",
            (
                "a" * 32,
                json.dumps(
                    {
                        "kind": "QLIB_SIGNAL_JOB",
                        "data_version_id": item.data_version,
                        "data_event_id": "e" * 64,
                    }
                ),
                json.dumps({"signal_family_id": item.signal_id}),
                "COMPLETE",
            ),
        )
    archive = instance.base / "workflow/jobs" / ("a" * 32) / "output" / item.signal_id
    service.write_json_atomic(
        archive / "manifest.json", signed({**AUTHORITY, "bindings": {"data_event_id": "e" * 64}})
    )
    monkeypatch.setattr(service, "active_approval", lambda _: None)
    verified = []

    def verify(path, **kwargs):
        verified.append((path, kwargs))
        return [(item, link())]

    monkeypatch.setattr(service, "signal_targets", verify)
    values = instance.current_targets(now)
    assert verified == [(archive, {"data_version": item.data_version, "source_revision": "a" * 40})]
    assert values[0][1]["data_event_id"] == "e" * 64
    assert values[0][1]["approval_sha256"] is None
    online["worker"]["heartbeat_at"] = (now - timedelta(hours=1)).isoformat()
    service.write_json_atomic(
        instance.base / "workflow/public/online.json",
        signed({k: v for k, v in online.items() if k != "evidence_sha256"}),
    )
    with pytest.raises(ValueError, match="WORKER_STALE"):
        instance.current_targets(now)


@pytest.fixture
def discovery_sources(environment, monkeypatch):
    instance, item, now = environment(), target(), datetime.now(UTC)
    approval = {"model_id": "7" * 64, "evidence_sha256": "8" * 64}
    model = replace(item, strategy_id="approved_model_" + approval["model_id"], signal_id="9" * 64)
    model_link = {
        **link(),
        "kind": "approved-prediction",
        "strategy_id": model.strategy_id,
        "artifact_id": model.signal_id,
        "model_id": approval["model_id"],
    }
    online = signed(
        {
            **AUTHORITY,
            "worker": {"heartbeat_at": now.isoformat(), "source_revision": "a" * 40},
            "data_version": {
                "version_id": item.data_version,
                "data_event_id": "e" * 64,
                "actual_end": instance.calendar.expected(now, "daily").date().isoformat(),
            },
            "auto_predict": {"current_prediction_id": model.signal_id},
        }
    )
    service.write_json_atomic(instance.base / "workflow/public/online.json", online)
    with sqlite3.connect(instance.workflow_database) as connection:
        connection.execute("CREATE TABLE jobs(id TEXT,request TEXT,result TEXT,state TEXT)")
        connection.execute(
            "INSERT INTO jobs VALUES(?,?,?,?)",
            (
                "a" * 32,
                json.dumps(
                    {
                        "kind": "QLIB_SIGNAL_JOB",
                        "data_version_id": item.data_version,
                        "data_event_id": "e" * 64,
                    }
                ),
                json.dumps({"signal_family_id": item.signal_id}),
                "COMPLETE",
            ),
        )
    signal_root = instance.base / "workflow/jobs" / ("a" * 32) / "output" / item.signal_id
    prediction_root = (
        instance.base / "online-research/control/predictions" / model.signal_id / "output"
    )
    prediction_root /= model.signal_id
    service.write_json_atomic(
        signal_root / "manifest.json",
        signed({**AUTHORITY, "bindings": {"data_event_id": "e" * 64}}),
    )
    service.write_json_atomic(
        prediction_root / "manifest.json",
        signed(
            {
                **AUTHORITY,
                "bindings": {
                    "data_event_id": "e" * 64,
                    "approval_sha256": approval["evidence_sha256"],
                },
            }
        ),
    )
    monkeypatch.setattr(service, "active_approval", lambda _: approval)
    monkeypatch.setattr(service, "signal_targets", lambda *args, **kwargs: [(item, link())])
    monkeypatch.setattr(service, "prediction_target", lambda *args, **kwargs: (model, model_link))
    return SimpleNamespace(
        instance=instance,
        signal=item,
        model=model,
        now=now,
        approval=approval,
        signal_root=signal_root,
        prediction_root=prediction_root,
    )


@pytest.mark.parametrize("source", ["signal", "prediction"])
@pytest.mark.parametrize("failure", ["missing", "corrupt"])
def test_discovery_isolates_independent_sources_and_publishes_diagnostics(
    discovery_sources, monkeypatch, source, failure
):
    data = discovery_sources
    if failure == "missing":
        getattr(data, source + "_root").joinpath("manifest.json").unlink()
    else:

        def corrupt(*args, **kwargs):
            raise ValueError("credential=never-expose-discovery-secret")

        monkeypatch.setattr(
            service, "signal_targets" if source == "signal" else "prediction_target", corrupt
        )
    result = data.instance.cycle(now=data.now)
    expected = data.model if source == "signal" else data.signal
    assert result["state"] == "DEGRADED"
    assert [book["strategy_id"] for book in result["books"]] == [expected.strategy_id]
    assert [error["code"] for error in result["errors"]] == [
        "FORWARD_SERVICE_" + source.upper() + "_DISCOVERY_FAILED"
    ]
    assert (
        sum(
            book.db.execute("SELECT COUNT(*) FROM targets").fetchone()[0]
            for book in data.instance.books.values()
        )
        == 1
    )
    assert "never-expose-discovery-secret" not in json.dumps(result)
    diagnostics = list((data.instance.root / "diagnostics").glob("*.json"))
    assert diagnostics and all(
        "never-expose-discovery-secret" not in p.read_text() for p in diagnostics
    )


@pytest.mark.parametrize("gate", ["epoch", "approval"])
def test_discovery_common_final_gate_rejects_all_targets(discovery_sources, monkeypatch, gate):
    data = discovery_sources
    verify = service.prediction_target

    def change_context(*args, **kwargs):
        if gate == "epoch":
            monkeypatch.setattr(service, "current_epoch", lambda *args: "f" * 64)
        else:
            monkeypatch.setattr(service, "active_approval", lambda _: None)
        return verify(*args, **kwargs)

    monkeypatch.setattr(service, "prediction_target", change_context)
    result = data.instance.cycle(now=data.now)
    assert result["books"] == [] and data.instance.books == {}
    assert [error["code"] for error in result["errors"]] == [
        "FORWARD_SERVICE_DATA_EPOCH_CHANGED"
        if gate == "epoch"
        else "FORWARD_SERVICE_APPROVAL_CHANGED"
    ]


def test_discovery_reports_missing_sources_and_clears_transient_failures(discovery_sources):
    data = discovery_sources
    manifest = data.signal_root / "manifest.json"
    original = manifest.read_bytes()
    manifest.unlink()
    assert data.instance.cycle(now=data.now)["state"] == "DEGRADED"
    manifest.write_bytes(original)
    recovered = data.instance.cycle(now=data.now)
    assert recovered["state"] == "RUNNING" and recovered["errors"] == []
    assert len(recovered["books"]) == 2
    with sqlite3.connect(data.instance.workflow_database) as connection:
        connection.execute("DELETE FROM jobs")
    missing = data.instance.cycle(now=data.now)
    assert [error["code"] for error in missing["errors"]] == ["FORWARD_SERVICE_SIGNAL_UNAVAILABLE"]


def test_invalid_book_does_not_prevent_other_books_restoring(environment):
    instance = environment(modes=(RuntimeMode.PAPER, RuntimeMode.SHADOW))
    instance.register(target(), link(), now=datetime.now(UTC))
    paper = next(key for key, value in instance.roster.items() if value["mode"] == "PAPER")
    instance.roster[paper]["strategy_id"] = "tampered"
    service.write_json_atomic(
        instance.root / "roster.json", signed({**AUTHORITY, "books": instance.roster})
    )
    restored = environment(modes=(RuntimeMode.PAPER, RuntimeMode.SHADOW))
    assert len(restored.books) == 1
    assert restored.startup_errors[0]["code"] == "FORWARD_SERVICE_BOOK_RESTORE_FAILED"


def test_diagnostics_do_not_persist_exception_messages(environment, monkeypatch):
    instance = environment()

    def fail(now):
        raise RuntimeError("credential=never-record-this-secret")

    monkeypatch.setattr(instance, "current_targets", fail)
    result = instance.cycle()
    assert result["errors"][0]["code"] == "RuntimeError"
    assert "never-record-this-secret" not in json.dumps(result)
    for path in (instance.root / "diagnostics").glob("*.json"):
        assert "never-record-this-secret" not in path.read_text()


def data_ledger(instance, item):
    with sqlite3.connect(instance.data_database) as c:
        c.executescript(
            "CREATE TABLE data_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,"
            "event_id TEXT,version_id TEXT,payload TEXT);"
            "CREATE TABLE data_heads(frequency TEXT,version_id TEXT);"
            "CREATE TABLE data_versions(version_id TEXT,manifest_sha256 TEXT)"
        )
        c.execute(
            "INSERT INTO data_events(event_id,version_id,payload) VALUES(?,?,?)",
            (
                "e" * 64,
                item.data_version,
                json.dumps(
                    signed(
                        {**AUTHORITY, "kind": "QLIB_DATA_READY", "version_id": item.data_version}
                    )
                ),
            ),
        )
        c.execute("INSERT INTO data_heads VALUES(?,?)", ("day", item.data_version))
        c.execute("INSERT INTO data_versions VALUES(?,?)", (item.data_version, "d" * 64))


def test_observation_rejects_any_rollback_after_frozen_epoch(environment):
    instance, item = environment(), target()
    data_ledger(instance, item)
    instance.check_observation_context(item, link())
    with sqlite3.connect(instance.data_database) as c:
        c.execute(
            "INSERT INTO data_events(event_id,version_id,payload) VALUES(?,?,?)",
            (
                "f" * 64,
                item.data_version,
                json.dumps(
                    signed(
                        {**AUTHORITY, "kind": "QLIB_DATA_ROLLBACK", "version_id": item.data_version}
                    )
                ),
            ),
        )
    with pytest.raises(ValueError, match="ROLLBACK_REQUIRES_REVIEW"):
        instance.check_observation_context(item, link())


def test_observation_allows_only_verified_descendant_data(environment):
    instance = environment()

    def version(dataset):
        # Minimal signed lineage manifests exercise this bounded lineage guard;
        # full Provider/source verification belongs to immutable data admission.
        from hashlib import sha256

        raw = json.dumps(dataset).encode()
        files = {"dataset.json": sha256(raw).hexdigest()}
        identity = service.content_id(files, {})
        root = instance.base / "unified-data/qlib-versions/versions" / identity
        root.mkdir(parents=True)
        (root / "dataset.json").write_bytes(raw)
        manifest = signed({**AUTHORITY, "version_id": identity, "files": files, "bindings": {}})
        service.write_json_atomic(root / "version.json", manifest)
        return identity, manifest["evidence_sha256"]

    frozen = signed({**AUTHORITY, "end": "2026-10-26"})
    old_id, _ = version(frozen)
    item = replace(target(), data_version=old_id)
    data_ledger(instance, item)
    descendant = signed(
        {**AUTHORITY, "end": "2026-10-27", "ancestor_dataset_sha256": [frozen["evidence_sha256"]]}
    )
    new_id, manifest_sha = version(descendant)
    with sqlite3.connect(instance.data_database) as c:
        c.execute("INSERT INTO data_versions VALUES(?,?)", (new_id, manifest_sha))
        c.execute("UPDATE data_heads SET version_id=?", (new_id,))
    instance.check_observation_context(item, link())
    unrelated_id, manifest_sha = version(signed({**AUTHORITY, "end": "2026-10-28"}))
    with sqlite3.connect(instance.data_database) as c:
        c.execute("INSERT INTO data_versions VALUES(?,?)", (unrelated_id, manifest_sha))
        c.execute("UPDATE data_heads SET version_id=?", (unrelated_id,))
    with pytest.raises(ValueError, match="DATA_NOT_DESCENDANT"):
        instance.check_observation_context(item, link())


@pytest.fixture
def recovery_service(environment, monkeypatch):
    instance = environment(modes=(RuntimeMode.PAPER, RuntimeMode.SHADOW))
    instance.register(target(), link(), now=datetime.now(UTC))
    monkeypatch.setattr(instance, "current_targets", lambda _: [])
    monkeypatch.setattr(instance, "observe_book", lambda *args, **kwargs: None)
    instance.cycle()
    identities = {book.mode: identity for identity, book in instance.books.items()}
    return SimpleNamespace(
        instance=instance,
        paper=identities[RuntimeMode.PAPER],
        shadow=identities[RuntimeMode.SHADOW],
    )


def test_explicit_recovery_prepare_does_not_open_or_recover_book_then_live_consumes_once(
    recovery_service,
):
    data = recovery_service
    request = service.prepare_recovery_request(
        data.instance.root, book_id=data.paper, actor="owner"
    )
    assert Book.recovered == []
    identity = request["request_id"]
    assert request["instance_id"] == data.instance.instance_id
    assert data.instance.recover_requested_books() == []
    assert Book.recovered == [("momentum20", "owner")]
    assert not data.instance.books[data.shadow].bundle.runtime.status.running
    consumed = service.load(data.instance.root / "recovery/consumed" / (identity + ".json"))
    receipt = service.load(data.instance.root / "recovery/receipts" / (identity + ".json"))
    assert consumed["state"] == "CONSUMED"
    assert consumed["request_sha256"] == request["evidence_sha256"]
    assert receipt["state"] == "SUCCEEDED"
    data.instance.books[data.paper].bundle.runtime.status.running = False
    assert data.instance.recover_requested_books() == []
    assert Book.recovered == [("momentum20", "owner")]
    with pytest.raises(ValueError, match="REQUEST_ID_REUSED"):
        service.prepare_recovery_request(
            data.instance.root, book_id=data.paper, actor="owner", request_id=identity
        )


@pytest.mark.parametrize(
    "defect",
    [
        "expired",
        "future",
        "wrong_book",
        "shadow",
        "old_instance",
        "extra",
        "long_ttl",
        "actor",
    ],
)
def test_invalid_recovery_request_is_rejected_and_never_replayed(recovery_service, defect):
    data = recovery_service
    request = service.prepare_recovery_request(
        data.instance.root, book_id=data.paper, actor="owner"
    )
    now = datetime.now(UTC)
    updates = {
        "expired": {
            "issued_at": (now - timedelta(minutes=10)).isoformat(),
            "expires_at": (now - timedelta(minutes=5)).isoformat(),
        },
        "future": {
            "issued_at": (now + timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(minutes=6)).isoformat(),
        },
        "wrong_book": {"book_id": "1" * 64},
        "shadow": {"book_id": data.shadow},
        "old_instance": {"instance_id": "1" * 32},
        "extra": {"auto_recover": True},
        "long_ttl": {"expires_at": (now + timedelta(hours=1)).isoformat()},
        "actor": {"actor": "not a valid actor"},
    }
    altered = signed(
        {**{k: v for k, v in request.items() if k != "evidence_sha256"}, **updates[defect]}
    )
    service.write_json_atomic(
        data.instance.root / "recovery/requests" / (request["request_id"] + ".json"), altered
    )
    assert data.instance.recover_requested_books() == ["FORWARD_SERVICE_RECOVERY_REQUEST_REJECTED"]
    assert Book.recovered == []
    assert data.instance.recover_requested_books() == []
    receipt = service.load(
        data.instance.root / "recovery/receipts" / (request["request_id"] + ".json")
    )
    assert receipt["state"] == "REJECTED"


def test_prepare_rejects_nonpaper_and_stale_service_without_writing_request(recovery_service):
    data = recovery_service
    with pytest.raises(ValueError, match="BOOK_OR_INSTANCE_INVALID"):
        service.prepare_recovery_request(data.instance.root, book_id=data.shadow, actor="owner")
    instance_file = data.instance.root / "service-instance.json"
    instance = service.load(instance_file)
    service.write_json_atomic(
        instance_file,
        signed(
            {
                **{k: v for k, v in instance.items() if k != "evidence_sha256"},
                "heartbeat_at": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
            }
        ),
    )
    with pytest.raises(ValueError, match="BOOK_OR_INSTANCE_INVALID"):
        service.prepare_recovery_request(data.instance.root, book_id=data.paper, actor="owner")
    assert not list((data.instance.root / "recovery/requests").glob("*.json"))
    assert Book.recovered == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("live_trading_enabled", 0),
        ("external_order_transmission", 0.0),
        ("point_in_time", 0),
        ("real_data_paper_readiness", True),
        ("classification", "TRADE_READY"),
    ],
)
def test_recovery_requires_exact_authority_values_and_types(recovery_service, field, value):
    data = recovery_service
    request = service.prepare_recovery_request(
        data.instance.root, book_id=data.paper, actor="owner"
    )
    changed = signed({**{k: v for k, v in request.items() if k != "evidence_sha256"}, field: value})
    service.write_json_atomic(
        data.instance.root / "recovery/requests" / (request["request_id"] + ".json"), changed
    )
    assert data.instance.recover_requested_books() == ["FORWARD_SERVICE_RECOVERY_REQUEST_REJECTED"]
    assert Book.recovered == []


def test_recovery_failure_is_durable_halted_and_not_retried(recovery_service, monkeypatch):
    data = recovery_service
    request = service.prepare_recovery_request(
        data.instance.root, book_id=data.paper, actor="owner"
    )
    attempted = []

    def failure(*, actor):
        attempted.append(actor)
        raise ValueError("credential=never-record-recovery-secret")

    monkeypatch.setattr(data.instance.books[data.paper], "recover", failure)
    assert data.instance.recover_requested_books() == [
        "FORWARD_SERVICE_RECOVERY_RECONCILIATION_FAILED"
    ]
    assert data.instance.recover_requested_books() == []
    assert attempted == ["owner"]
    assert not data.instance.books[data.paper].bundle.runtime.status.running
    receipt = service.load(
        data.instance.root / "recovery/receipts" / (request["request_id"] + ".json")
    )
    assert receipt["state"] == "FAILED" and "never-record-recovery-secret" not in json.dumps(
        receipt
    )


def test_crash_after_consume_never_retries_in_live_or_restarted_service(
    recovery_service, environment, monkeypatch
):
    data = recovery_service
    request = service.prepare_recovery_request(
        data.instance.root, book_id=data.paper, actor="owner"
    )
    consumed = data.instance.root / "recovery/consumed" / (request["request_id"] + ".json")
    attempted = []

    def crash(*, actor):
        assert consumed.exists()
        attempted.append(actor)
        raise SystemExit("synthetic process crash")

    monkeypatch.setattr(data.instance.books[data.paper], "recover", crash)
    with pytest.raises(SystemExit, match="synthetic process crash"):
        data.instance.recover_requested_books()
    expected = ["FORWARD_SERVICE_RECOVERY_CONSUMED_REQUIRES_REVIEW"]
    assert data.instance.recover_requested_books() == expected
    restored = environment(modes=(RuntimeMode.PAPER, RuntimeMode.SHADOW))
    assert restored.recover_requested_books() == expected
    assert not restored.books[data.paper].bundle.runtime.status.running
    assert attempted == ["owner"] and Book.recovered == []


def test_unconsumed_request_cannot_recover_a_restarted_service(recovery_service, environment):
    data = recovery_service
    service.prepare_recovery_request(data.instance.root, book_id=data.paper, actor="owner")
    restored = environment(modes=(RuntimeMode.PAPER, RuntimeMode.SHADOW))
    assert restored.instance_id != data.instance.instance_id
    assert restored.recover_requested_books() == ["FORWARD_SERVICE_RECOVERY_REQUEST_REJECTED"]
    assert not restored.books[data.paper].bundle.runtime.status.running
    assert Book.recovered == []


def test_recovery_prepare_cli_only_writes_request(recovery_service, monkeypatch, capsys):
    data = recovery_service
    monkeypatch.setattr(
        service.sys,
        "argv",
        [
            "forward-service",
            "--local-root",
            str(data.instance.root),
            "--prepare-recovery-book",
            data.paper,
            "--recovery-actor",
            "owner",
            "--recovery-request-id",
            "2" * 32,
        ],
    )
    monkeypatch.setattr(
        service, "ForwardService", lambda *args, **kwargs: pytest.fail("opened book")
    )
    service.main()
    request = json.loads(capsys.readouterr().out)
    assert request["request_id"] == "2" * 32
    assert request["book_id"] == data.paper and Book.recovered == []


def test_live_explicit_recovery_runs_real_reconciliation_and_preserves_pending_gate(
    environment, monkeypatch
):
    monkeypatch.setattr(service, "ForwardPaperBook", RealBook)
    instance = environment(modes=(RuntimeMode.PAPER,))
    item = target()
    instance.register(item, link(), now=datetime.now(UTC))
    monkeypatch.setattr(instance, "current_targets", lambda _: [])
    monkeypatch.setattr(instance, "observe_book", lambda *args, **kwargs: None)
    assert instance.cycle()["state"] == "HUMAN_RECOVERY_REQUIRED"
    identity, book = next(iter(instance.books.items()))
    service.prepare_recovery_request(instance.root, book_id=identity, actor="test-human")
    assert instance.recover_requested_books() == []
    assert book.bundle.runtime.status.running and book.bundle.risk.state.value == "NORMAL"
    book.bundle.runtime.kill(actor="test-human", reason="synthetic uncertain receipt")
    book.db.execute(
        "INSERT INTO sessions VALUES (?,?,?,'PENDING',NULL,?)",
        ("2026-10-27", item.identity, "fixture", "{}"),
    )
    service.prepare_recovery_request(instance.root, book_id=identity, actor="test-human")
    assert instance.recover_requested_books() == ["FORWARD_SERVICE_RECOVERY_RECONCILIATION_FAILED"]
    assert not book.bundle.runtime.status.running and book.bundle.risk.state.value == "HALTED"
    assert (
        book.db.execute("SELECT COUNT(*) FROM sessions WHERE status='PENDING'").fetchone()[0] == 1
    )
