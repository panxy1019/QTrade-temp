import base64
import hashlib
import io
import json
import os
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from qtrade.monitoring.data_freshness import signed
from qtrade.research.delisted_probe_plan import build_delisted_probe_plan, initial_probe_scope
from qtrade.research.official_master import reconcile
from qtrade.research.workflow import digest
from scripts import history_backfill_store as store_module
from scripts import plan_public_delisted_history as command
from scripts import unified_store
from scripts.history_backfill_store import HistoryStore, fields, request_for

NOW = datetime(2026, 10, 26, 15, tzinfo=UTC)
REVISION = "a" * 40
START, END = date(2016, 10, 24), date(2026, 10, 23)


@pytest.fixture(autouse=True)
def platform_writers(monkeypatch):
    if os.name != "nt":
        return
    monkeypatch.setattr(unified_store, "_fsync_directory", lambda _: None)
    for module in (command, store_module):
        original = module.write_json_immutable
        def write(path, value, original=original):
            try:
                original(path, value)
            except PermissionError:
                assert path.is_file() and json.loads(path.read_text(encoding="utf-8")) == value
        monkeypatch.setattr(module, "write_json_immutable", write)


@pytest.fixture
def inputs():
    sse = []
    master_rows = []
    for code, reported, vendor in (
        ("600111", "2020-08-17", "2020-08-17"),
        ("600222", "2020-08-17", "2020-08-14"),
        ("600333", "2020-08-17", "2020-08-17"),
        ("600444", "2010-08-17", "2010-08-17"),
    ):
        sse.append({"COMPANY_CODE": code, "A_STOCK_CODE": code, "STOCK_TYPE": "1",
                    "LIST_DATE": "1999-01-01", "DELIST_DATE": reported})
        master_rows.append({"code": "sh." + code, "type": "1", "status": "0",
                            "ipoDate": "1999-01-01", "outDate": vendor})
    sse.append(deepcopy(sse[2]))
    sse.append({"COMPANY_CODE": "900001", "A_STOCK_CODE": "", "STOCK_TYPE": "2",
                "LIST_DATE": "1999-01-01", "DELIST_DATE": "2020-08-17"})
    szse = [{"证券代码": "000111", "证券简称": "旧身份", "上市日期": "1999-01-01",
             "终止上市日期": "2023-03-06"}]
    master_rows.append({"code": "sz.000111", "type": "1", "status": "0",
                        "ipoDate": "1999-01-01", "outDate": "2023-03-06"})
    master = {"source": "BaoStock query_stock_basic", "historical_available_at": None,
              "observed_at": (NOW - timedelta(days=1)).isoformat(), "rows": master_rows}
    dataset = {"stocks": [{"symbol": "600999"}], "accepted_symbols": 1,
               "end": "2026-10-23"}
    raw_sse = json.dumps({"result": sse, "pageHelp": {"total": len(sse)}}).encode()
    buffer = io.BytesIO()
    pd.DataFrame(szse).to_excel(buffer, index=False)
    raw_szse = buffer.getvalue()
    raw = {"SSE": raw_sse, "SZSE": raw_szse}
    result = reconcile(sse, szse, master_rows, {"600999"})
    result = signed({
        **{k: v for k, v in result.items() if k != "evidence_sha256"},
        "observed_at": (NOW - timedelta(hours=1)).isoformat(),
        "master_sha256": digest(master), "master_observed_at": master["observed_at"],
        "dataset_sha256": digest(dataset), "probe_evidence_sha256": "b" * 64,
        "raw_sources": {exchange: {
            "raw_sha256": hashlib.sha256(payload).hexdigest(), "raw_bytes": len(payload),
            "rows": len(sse) if exchange == "SSE" else len(szse),
            "observed_at": (NOW - timedelta(hours=2)).isoformat(),
            "historical_available_at": None,
        } for exchange, payload in raw.items()},
    })
    return result, master, dataset, raw


def build(inputs, **kwargs):
    result, master, dataset, _ = inputs
    return build_delisted_probe_plan(result, master, dataset, requested_start=START,
                                     requested_end=END, now=NOW, code_revision=REVISION, **kwargs)


def test_only_unique_issue_free_reconciled_rows_compile_and_every_source_row_remains(inputs):
    before = deepcopy(inputs)
    plan = build(inputs)
    assert len(plan["decisions"]) == len(inputs[0]["records"]) == 7
    assert plan["probe_task_count"] == 2
    assert {task["instrument_id"] for task in plan["tasks"]} == {"SSE.600111", "SZSE.000111"}
    assert plan["decision_counts"]["QUARANTINED_REQUIRES_REVIEW"] == 4
    assert plan["decision_counts"]["OUTSIDE_REQUESTED_RESEARCH_HORIZON"] == 1
    assert all(task["execution_enabled"] is False for task in plan["tasks"])
    assert plan["historical_pit_certified"] is False
    assert plan["complete_historical_pit_symbols"] == plan["requested_queries"] == 0
    assert inputs == before


def test_claimed_delisting_selects_full_month_not_a_last_trade_cutoff(inputs):
    plan = build(inputs)
    task = next(task for task in plan["tasks"] if task["symbol"] == "600111")
    assert task["initial_window"] == ["2020-08-01", "2020-08-31"]
    assert task["requested_start"] == "2016-10-24"
    assert task["official_reported_delisting_date_claim"] == "2020-08-17"
    assert task["delisting_claim_used_as_last_trade_boundary"] is False
    assert task["listing_claim_used_as_authoritative_boundary"] is False
    assert task["historical_available_at"] is None
    for task in plan["tasks"]:
        scope = initial_probe_scope(task)
        span = date.fromisoformat(scope["end"]) - date.fromisoformat(scope["start"])
        assert 0 <= span.days < 31
        request = request_for(scope)
        assert request["frequency"] == "d" and request["adjustflag"] == "3"
        assert "tradestatus" in request["fields"] and "isST" not in request["fields"]


@pytest.mark.parametrize("kind", ["master", "dataset", "clock", "availability", "authority"])
def test_forged_binding_availability_or_authority_cannot_compile(inputs, kind):
    result, master, dataset, raw = deepcopy(inputs)
    if kind == "master":
        master["rows"][0]["status"] = "1"
    elif kind == "dataset":
        dataset["end"] = "2026-10-22"
    elif kind == "clock":
        result["observed_at"] = "2099-01-01T00:00:00+00:00"
    elif kind == "availability":
        result["historical_available_at"] = "2020-01-01T00:00:00+00:00"
    else:
        result["point_in_time"] = True
    result = signed({k: v for k, v in result.items() if k != "evidence_sha256"})
    with pytest.raises(ValueError, match="DELISTED_PLAN_SOURCE"):
        build((result, master, dataset, raw))


def write_inputs(base, inputs):
    result, master, dataset, raw = inputs
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
    path = base / "reconciliation.json"
    write(path, result)
    write(base / "pit-repair/master" / (digest(master) + ".json"), master)
    write(base / "qlib-data-20261023/dataset.json", dataset)
    for exchange, payload in raw.items():
        receipt = result["raw_sources"][exchange]
        target = base / "pit-independent-sources" / (
            receipt["raw_sha256"] + (".json" if exchange == "SSE" else ".xlsx"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return path


def compile_bundle(base, path, **kwargs):
    return command.run(base, path, requested_start=START, requested_end=END,
                       code_revision=REVISION, now=kwargs.get("now", NOW))


def test_offline_bundle_replays_both_raw_sources_and_remains_idempotent_after_restart(
    inputs, tmp_path, monkeypatch,
):
    from scripts import vendor_session

    def forbidden(*args, **kwargs):
        raise AssertionError("offline compilation must not acquire vendor sessions")
    monkeypatch.setattr(vendor_session, "baostock_session", forbidden)
    path = write_inputs(tmp_path, inputs)
    original = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    first = compile_bundle(tmp_path, path)
    root = tmp_path / "pit-repair/delisted-plans" / first["plan_sha256"]
    assert first["probe_task_count"] == 2 and first["downloaded_rows"] == 0
    assert command.verify_bundle(root, now=NOW) == first
    assert compile_bundle(tmp_path, path, now=NOW + timedelta(days=1)) == first
    assert all(p.read_bytes() == raw for p, raw in original.items())
    assert len(list(root.parent.iterdir())) == 1
    captured = json.loads((root / "official-raw.json").read_bytes())
    assert base64.b64decode(captured["SSE"]["bytes_base64"]) == inputs[3]["SSE"]


def test_partial_bundle_is_resumed_but_conflicting_existing_bytes_are_never_overwritten(
    inputs, tmp_path, monkeypatch,
):
    path = write_inputs(tmp_path, inputs)
    original_write = command.write_json_immutable
    def interrupted(target, value):
        if target.name == "dataset.json":
            raise RuntimeError("simulated interruption")
        original_write(target, value)
    monkeypatch.setattr(command, "write_json_immutable", interrupted)
    with pytest.raises(RuntimeError, match="interruption"):
        compile_bundle(tmp_path, path)
    root = next((tmp_path / "pit-repair/delisted-plans").iterdir())
    assert not (root / "manifest.json").exists()
    original_plan = (root / "plan.json").read_bytes()
    monkeypatch.setattr(command, "write_json_immutable", original_write)
    receipt = compile_bundle(tmp_path, path)
    assert receipt["execution_enabled"] is False
    assert (root / "plan.json").read_bytes() == original_plan
    (root / "plan.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="FILE_HASH_MISMATCH"):
        compile_bundle(tmp_path, path)
    assert (root / "plan.json").read_bytes() == b"{}"


@pytest.mark.parametrize("kind", ["official_bytes", "resigned_reconciliation"])
def test_reconciliation_cannot_hide_original_official_conflicts(inputs, tmp_path, kind):
    result, master, dataset, raw = deepcopy(inputs)
    if kind == "official_bytes":
        raw["SSE"] += b"corrupt"
    else:
        result["records"][1]["issues"] = []
        result["records"][1]["state"] = "DATES_AGREE_UNCERTIFIED"
        result = signed({k: v for k, v in result.items() if k != "evidence_sha256"})
    path = write_inputs(tmp_path, (result, master, dataset, raw))
    with pytest.raises(ValueError, match="BYTES_MISMATCH|RECONCILIATION_REPLAY_MISMATCH"):
        compile_bundle(tmp_path, path)
    assert not (tmp_path / "pit-repair/delisted-plans").exists()


def test_prepared_scope_uses_existing_store_and_replays_actual_day_content(inputs, tmp_path):
    task = build(inputs)["tasks"][0]
    scope = initial_probe_scope(task)
    raw = {"request": request_for(scope), "fields": list(fields("day")),
           "body_complete": True, "vendor_error_code": "0", "transport_error": None,
           "query_attempted": True,
           "rows": [["2020-08-03", "sh.600111", "10", "11", "9", "10",
                     "1000", "10000", "3", "1"]]}
    store = HistoryStore(tmp_path / "isolated-lake")
    attempt = {"attempt_id": "a" * 32, "started_at": NOW.isoformat(), "scope": scope}
    store.begin(attempt)
    receipt = store.seal(attempt, raw)
    assert receipt["quality"]["qualified_rows"] == 1
    assert receipt["historical_pit_certified"] is False
    assert receipt["historical_available_at"] is None
    assert store.load(attempt["attempt_id"]) == receipt
    assert len(store.qualified_rows(attempt["attempt_id"])) == 1


def test_resigned_bundle_cannot_claim_downloads_or_enable_execution(inputs, tmp_path):
    path = write_inputs(tmp_path, inputs)
    manifest = compile_bundle(tmp_path, path)
    root = tmp_path / "pit-repair/delisted-plans" / manifest["plan_sha256"]
    for change in ({"execution_enabled": True}, {"historical_pit_certified": True},
                   {"downloaded_rows": 1}, {"point_in_time": 0},
                   {"sealed_at": "2099-01-01T00:00:00+00:00"}):
        forged = signed({**{k: v for k, v in manifest.items() if k != "evidence_sha256"},
                         **change})
        (root / "manifest.json").write_text(json.dumps(forged), encoding="utf-8")
        with pytest.raises(ValueError, match="MANIFEST_INVALID"):
            command.verify_bundle(root, now=NOW)


@pytest.mark.parametrize("start,end", [
    (date(2026, 10, 24), date(2026, 10, 23)),
    (date(2000, 1, 1), END), (START, date(2026, 10, 26)),
])
def test_task_horizon_must_be_closed_ordered_and_at_most_ten_years(inputs, start, end):
    result, master, dataset, _ = inputs
    with pytest.raises(ValueError, match="CLOCK_RANGE"):
        build_delisted_probe_plan(result, master, dataset, requested_start=start,
                                   requested_end=end, now=NOW, code_revision=REVISION)
