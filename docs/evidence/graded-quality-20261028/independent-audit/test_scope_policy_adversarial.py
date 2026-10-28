"""Independent contract probes for approved scope mode, local fixtures only."""

import json
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.data_quality_policy import MODE, SCHEMA, USES, load_policy, symbol_scope, validate_policy
from scripts import build_qlib_daily_version as build_module
from scripts import prepare_qlib_incremental as preparation
from scripts import run_qlib_data_service as service

UNIVERSE = "b"*64
DAY = "2026-10-27"
CLOSE = DAY+"T07:00:00+00:00"
NOW = datetime(2026, 10, 28, 2, tzinfo=UTC)


def policy(dataset):
    return signed({**AUTHORITY, "schema_version": SCHEMA, "mode": MODE, "state": "APPROVED",
                   "uses": sorted(USES), "symbol_scope_sha256": symbol_scope(dataset),
                   "universe_version": UNIVERSE, "paper_constraints_changed": False,
                   "model_activation_authorized": False, "approved_at": "2026-10-26T00:00:00+00:00",
                   "effective_from_day": DAY, "approved_by": "external-fixture-human",
                   "authorization_kind": "EXPLICIT_HUMAN_ACTION", "authorization_message_sha256": "d"*64})


@pytest.fixture
def scope(tmp_path, monkeypatch):
    def write(path, value, **kwargs):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    monkeypatch.setattr(preparation, "write_json_immutable", write)
    dataset = signed({**AUTHORITY, "stocks": [{"symbol": "600036"}], "end": "2026-10-26"})
    master = signed({**AUTHORITY, "universe_version": UNIVERSE, "securities": [
        {"symbol": "600036", "in_target": True, "security_type": "STOCK"},
        {"symbol": "510050", "in_target": True, "security_type": "ETF"}]})
    write(tmp_path/"unified-data/public/unified.json", signed({**AUTHORITY, "master": master}))
    database = tmp_path/"fixture.sqlite"
    with sqlite3.connect(database) as c:
        c.execute("CREATE TABLE collection_tasks(universe_version,frequency,symbol,trading_day,security_type,state,desired_end,completed_end,last_success_batch)")
        c.executemany("INSERT INTO collection_tasks VALUES(?,?,?,?,?,?,?,?,?)", [
            (UNIVERSE, "day", "600036", DAY, "STOCK", "SUCCEEDED", CLOSE, CLOSE, "c"*32),
            (UNIVERSE, "day", "510050", DAY, "ETF", "PLANNED", CLOSE, None, None),
            (UNIVERSE, "5min", "600036", DAY, "STOCK", "FAILED", CLOSE, None, None)])
    return SimpleNamespace(base=tmp_path, dataset=dataset, database=database)


def lookup(scope, approval=None):
    return preparation.daily_tasks(scope.base, scope.dataset, DAY, UNIVERSE, scope.database, approval)


def test_none_preserves_old_wait_but_explicit_approval_ignores_unrelated_day_and_minute(scope):
    before = scope.database.read_bytes()
    assert load_policy(scope.base) is None
    assert lookup(scope) is None
    rows = lookup(scope, policy(scope.dataset))
    assert [row["symbol"] for row in rows] == ["600036"]
    assert scope.database.read_bytes() == before


@pytest.mark.parametrize("defect", ["missing", "pending", "running", "interrupted", "wrong_day", "wrong_universe"])
def test_approved_scope_never_turns_missing_required_input_into_success(scope, defect):
    with sqlite3.connect(scope.database) as c:
        if defect == "missing":
            c.execute("DELETE FROM collection_tasks WHERE symbol='600036' AND frequency='day'")
        elif defect in {"pending", "running", "interrupted"}:
            state = {"pending": "PLANNED", "running": "RUNNING", "interrupted": "INTERRUPTED"}[defect]
            c.execute("UPDATE collection_tasks SET state=? WHERE symbol='600036' AND frequency='day'", (state,))
        elif defect == "wrong_day":
            c.execute("UPDATE collection_tasks SET trading_day='2026-10-26' WHERE frequency='day'")
        else:
            c.execute("UPDATE collection_tasks SET universe_version=? WHERE frequency='day'", ("e"*64,))
    assert lookup(scope, policy(scope.dataset)) is None


@pytest.mark.parametrize("field,value", [("desired_end", "2026-10-26T07:00:00+00:00"),
                                         ("completed_end", "2026-10-27T06:55:00+00:00"),
                                         ("last_success_batch", "bad-batch")])
def test_approval_cannot_override_exact_clock_or_batch_identity(scope, field, value):
    with sqlite3.connect(scope.database) as c:
        c.execute(f"UPDATE collection_tasks SET {field}=? WHERE symbol='600036' AND frequency='day'", (value,))
    with pytest.raises(ValueError):
        lookup(scope, policy(scope.dataset))


@pytest.mark.parametrize("field,value", [("state", "PROPOSED"), ("mode", "OTHER_MODE"),
    ("schema_version", "qtrade.quality-scope-policy.future"), ("uses", ["DAILY_RESEARCH"]),
    ("uses", sorted(USES)+["PAPER"]), ("paper_constraints_changed", True),
    ("model_activation_authorized", True), ("live_trading_enabled", 0),
    ("universe_version", "e"*64), ("symbol_scope_sha256", "e"*64),
    ("authorization_kind", "INFERRED_STANDING_CONSENT"),
    ("authorization_message_sha256", "0"*64), ("approved_by", ""),
    ("approved_at", "2099-01-01T00:00:00+00:00"), ("approved_at", "2026-10-26T00:00:00"),
    ("effective_from_day", "2026-10-28")])
def test_invalid_approval_fails_before_old_scope_fallback(scope, field, value):
    approval = policy(scope.dataset)
    approval[field] = value
    approval = signed({k: v for k, v in approval.items() if k != "evidence_sha256"})
    with pytest.raises(ValueError):
        lookup(scope, approval)


def test_hash_and_dataset_member_changes_cannot_reuse_scope_authorization(scope):
    approval = policy(scope.dataset)
    approval["approved_by"] = "tampered-without-rehash"
    with pytest.raises(ValueError):
        lookup(scope, approval)
    approval = policy(scope.dataset)
    changed = deepcopy(scope.dataset)
    changed["stocks"].append({"symbol": "600037"})
    with pytest.raises(ValueError):
        validate_policy(approval, changed, UNIVERSE, DAY, NOW)


def test_explicit_failed_input_is_terminal_and_preserves_no_success_pointer(scope):
    with sqlite3.connect(scope.database) as c:
        c.execute("UPDATE collection_tasks SET state='FAILED',completed_end=NULL,last_success_batch=NULL WHERE symbol='600036' AND frequency='day'")
    rows = lookup(scope, policy(scope.dataset))
    assert len(rows) == 1 and rows[0]["state"] == "FAILED" and rows[0]["last_success_batch"] is None


def test_new_scope_freezes_separate_approval_bound_bytes_without_rewriting_legacy(scope):
    with sqlite3.connect(scope.database) as c:
        c.execute("UPDATE collection_tasks SET state='FAILED',completed_end=NULL,last_success_batch=NULL WHERE frequency='day'")
    class NoAcceptedBatches:
        def load(self, identity):
            raise AssertionError("FAILED input cannot load an admitted batch")
    kwargs = {"day": DAY, "revision": "a"*40, "universe": UNIVERSE,
              "collection_database": scope.database, "store": NoAcceptedBatches()}
    legacy = preparation.preparation_request(scope.base, scope.dataset, **kwargs)
    frozen = {p: p.read_bytes() for p in (scope.base/"unified-data/qlib-preparation/input-scopes").rglob("*.json")}
    approval = policy(scope.dataset)
    approved = preparation.preparation_request(scope.base, scope.dataset, input_scope_policy=approval, **kwargs)
    assert approved["evidence_sha256"] != legacy["evidence_sha256"]
    assert approved["input_scope_policy_sha256"] == approval["evidence_sha256"]
    assert approved["input_scope_policy"] == approval
    assert approved["batches"] == {} and approved["excluded_current_inputs"] == {"600036": "NO_QUALIFIED_CURRENT_DAILY_BATCH"}
    for path, original in frozen.items():
        assert path.read_bytes() == original


def test_valid_approval_keeps_global_current_collection_priority(scope, monkeypatch):
    now = datetime(2026, 10, 27, 12, tzinfo=UTC)
    current = {"version_id": "f"*64, "actual_end": "2026-10-26",
               "bindings": {"observation_universe_version": UNIVERSE}}
    monkeypatch.setattr(service, "head", lambda *args: (scope.base/"old", current))
    monkeypatch.setattr(service, "current_epoch", lambda *args: "e"*64)
    monkeypatch.setattr(service, "collection_idle", lambda *args: False)
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    monkeypatch.setattr(service, "write_json_atomic", write)
    path = scope.base/"data-quality/approved-scope-policy.json"
    path.parent.mkdir()
    path.write_text(json.dumps(policy(scope.dataset)))
    result = service.cycle(scope.base, scope.base/"control.sqlite", scope.base/"python", "a"*40,
                           now=now, collection_database=scope.database)
    assert result["state"] == "WAITING_FOR_CURRENT_COLLECTION" and result["provider_published"] is False


def test_incomplete_new_policy_binding_cannot_be_treated_as_legacy(scope, monkeypatch):
    request = signed({**AUTHORITY, "batches": {"600036": "c"*32}, "day": DAY,
                      "universe_version": UNIVERSE, "input_scope_policy_sha256": "d"*64})
    folder = scope.base/request["evidence_sha256"]
    folder.mkdir()
    (folder/"request.json").write_text(json.dumps(request))
    (scope.base/"old").mkdir()
    (scope.base/"old/dataset.json").write_text(json.dumps(scope.dataset))
    monkeypatch.setattr(build_module.shutil, "disk_usage", lambda _: SimpleNamespace(free=200*1024**3))
    with pytest.raises(ValueError, match="SCOPE|POLICY|APPROVAL"):
        build_module.build(scope.base, scope.base/"old", folder, scope.base/"control.sqlite",
                           revision="a"*40, qlib_python=scope.base/"python")


def test_exact_990_stock_scope_ignores_unrelated_extra_task(scope):
    symbols = [str(600000+i) for i in range(990)]
    scope.dataset = signed({**AUTHORITY, "stocks": [{"symbol": s} for s in symbols]})
    with sqlite3.connect(scope.database) as c:
        c.execute("DELETE FROM collection_tasks WHERE symbol='600036'")
        c.executemany("INSERT INTO collection_tasks VALUES(?,?,?,?,?,?,?,?,?)", [
            (UNIVERSE, "day", s, DAY, "STOCK", "SUCCEEDED", CLOSE, CLOSE, "c"*32) for s in symbols])
    master = signed({**AUTHORITY, "universe_version": UNIVERSE, "securities": [
        *[{"symbol": s, "in_target": True, "security_type": "STOCK"} for s in symbols],
        {"symbol": "510050", "in_target": True, "security_type": "ETF"}]})
    (scope.base/"unified-data/public/unified.json").write_text(json.dumps(signed({**AUTHORITY, "master": master})))
    assert lookup(scope) is None
    rows = lookup(scope, policy(scope.dataset))
    assert len(rows) == 990 and {r["symbol"] for r in rows} == set(symbols)
