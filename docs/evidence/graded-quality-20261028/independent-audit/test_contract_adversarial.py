"""Independent read-only probes against the current repository contract.

All fixtures/outputs live below the externally supplied pytest basetemp.
No network, broker, runtime owner, production database or cluster access.
The two overbroad-wait cases characterize CURRENT behavior, not acceptance
of the proposed scoped-admission implementation.
"""

import json
import sqlite3
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.forward_paper import ObservedSecurity
from qtrade.research.forward_prediction import expected_codes, rank_scores
from qtrade.research.source_constraints import capture_source, restrict_rows
from qtrade.research.workflow import canonical, digest
from scripts.build_qlib_daily_version import append_benchmark
from scripts.prepare_qlib_incremental import daily_tasks
from scripts.run_qlib_data_service import collection_idle

DAY = "2026-10-27"
UNIVERSE = "b" * 64
CLOSE = "2026-10-27T07:00:00+00:00"
NOW = datetime(2026, 10, 28, 0, tzinfo=UTC)


@pytest.fixture
def ledger(tmp_path):
    database = tmp_path / "fixture.sqlite"
    with sqlite3.connect(database) as c:
        c.execute("CREATE TABLE collection_tasks(universe_version TEXT, frequency TEXT, "
                  "symbol TEXT, trading_day TEXT, security_type TEXT, state TEXT, "
                  "desired_end TEXT, completed_end TEXT, last_success_batch TEXT)")
        c.execute("INSERT INTO collection_tasks VALUES(?,?,?,?,?,?,?,?,?)",
                  (UNIVERSE, "day", "600036", DAY, "STOCK", "SUCCEEDED", CLOSE, CLOSE, "c"*32))
    dataset = {"stocks": [{"symbol": "600036"}]}
    return tmp_path, database, dataset


def lookup(ledger):
    base, database, dataset = ledger
    return daily_tasks(base, dataset, DAY, UNIVERSE, database)


def add_task(ledger, *, frequency="day", symbol="510050", day=DAY,
             universe=UNIVERSE, state="PLANNED"):
    with sqlite3.connect(ledger[1]) as c:
        c.execute("INSERT INTO collection_tasks VALUES(?,?,?,?,?,?,?,?,?)",
                  (universe, frequency, symbol, day, "ETF", state, CLOSE, None, None))


def publish_master(ledger):
    base = ledger[0]
    path = base / "unified-data/public/unified.json"
    path.parent.mkdir(parents=True)
    master = signed({**AUTHORITY, "universe_version": UNIVERSE,
                     "securities": [{"symbol": "600036", "in_target": True},
                                    {"symbol": "510050", "in_target": True}]})
    path.write_bytes(canonical(signed({**AUTHORITY, "master": master})))


@pytest.mark.parametrize("state", ["PLANNED", "RUNNING", "FAILED", "INTERRUPTED"])
def test_unrelated_5min_state_is_already_ignored_by_daily_ledger(ledger, state):
    add_task(ledger, frequency="5min", state=state)
    before = ledger[1].read_bytes()
    assert [r["symbol"] for r in lookup(ledger)] == ["600036"]
    assert ledger[1].read_bytes() == before


@pytest.mark.parametrize("scope", ["wrong_day", "wrong_universe"])
def test_unrelated_daily_scope_is_ignored(ledger, scope):
    add_task(ledger, day="2026-10-26" if scope == "wrong_day" else DAY,
             universe="e"*64 if scope == "wrong_universe" else UNIVERSE)
    assert [r["symbol"] for r in lookup(ledger)] == ["600036"]


def test_current_master_contract_waits_on_unrelated_dataset_etf(ledger):
    publish_master(ledger)
    add_task(ledger)
    assert lookup(ledger) is None  # Proposed scoped mode must change this, after approval.


def test_current_query_also_waits_on_extra_nonterminal_day_task_without_master(ledger):
    add_task(ledger)
    assert lookup(ledger) is None


@pytest.mark.parametrize("state", ["PLANNED", "RUNNING", "INTERRUPTED"])
def test_required_current_day_nonterminal_never_becomes_silent_exclusion(ledger, state):
    with sqlite3.connect(ledger[1]) as c:
        c.execute("UPDATE collection_tasks SET state=?", (state,))
    assert lookup(ledger) is None


def test_missing_required_current_day_never_uses_previous_day_success(ledger):
    with sqlite3.connect(ledger[1]) as c:
        c.execute("UPDATE collection_tasks SET trading_day='2026-10-26'")
    assert lookup(ledger) is None


def test_explicit_required_failure_is_terminal_but_not_success(ledger):
    with sqlite3.connect(ledger[1]) as c:
        c.execute("UPDATE collection_tasks SET state='FAILED',completed_end=NULL,last_success_batch=NULL")
    tasks = lookup(ledger)
    assert tasks[0]["state"] == "FAILED" and tasks[0]["last_success_batch"] is None


@pytest.mark.parametrize("field,value,code", [
    ("desired_end", "2026-10-26T07:00:00+00:00", "DESIRED_DAY"),
    ("completed_end", "2026-10-27T06:55:00+00:00", "COMPLETED_DAY"),
    ("last_success_batch", "not-a-batch", "SUCCESS_BATCH"),
])
def test_current_day_success_has_exact_clock_and_batch_contract(ledger, field, value, code):
    with sqlite3.connect(ledger[1]) as c:
        c.execute(f"UPDATE collection_tasks SET {field}=?", (value,))
    with pytest.raises(ValueError, match=code):
        lookup(ledger)


@pytest.mark.parametrize("state,offset,expected", [
    ("WAITING", 0, True), ("COLLECTING", 0, False), ("PREPARING", 0, False),
    ("WAITING", -301, False), ("WAITING", 1, False),
])
def test_current_service_coordination_is_global_not_frequency_specific(tmp_path, state, offset, expected):
    path = tmp_path / "unified-data/service-state.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"state": state, "heartbeat_at": (NOW+timedelta(seconds=offset)).isoformat(),
                                "active_frequency": "5min"}))
    assert collection_idle(tmp_path, NOW) is expected


@pytest.fixture
def evidence(tmp_path):
    root = tmp_path / "source"
    (root / "master").mkdir(parents=True)
    (root / "status").mkdir()
    master = {"source": "BaoStock query_stock_basic", "observed_at": NOW.isoformat(),
              "historical_available_at": None, "rows": [{"code": "sh.600036"}]}
    status = signed({"source": "BaoStock raw daily status backfill", "symbol": "600036",
                     "historical_available_at": None, "certified_at_decision": False,
                     "observed_at": NOW.isoformat(), "rows": [
                         {"date": DAY, "code": "sh.600036", "tradestatus": "1", "isST": "0",
                          "open": "10", "close": "10", "volume": "1000", "amount": "10000"}]})
    (root / "master" / (digest(master)+".json")).write_bytes(canonical(master))
    (root / "status/600036.json").write_bytes(canonical(status))
    coverage = signed({**AUTHORITY, "schema_version": "qtrade.pit-repair.v1", "observed_at": NOW.isoformat(),
                       "historical_pit_certified": False, "historical_available_at_known": False,
                       "master_sha256": digest(master), "master_rows": 1, "status_symbols": 1,
                       "status_rows": 1, "source_checksums": {"600036.json": digest(status)}})
    (root / "coverage.json").write_bytes(canonical(coverage))
    return root, status, coverage


def test_resigned_wrong_source_schema_is_still_globally_rejected(evidence):
    root, _, coverage = evidence
    coverage["schema_version"] = "qtrade.pit-repair.unsupported"
    (root / "coverage.json").write_bytes(canonical(signed({k: v for k, v in coverage.items() if k != "evidence_sha256"})))
    with pytest.raises(ValueError, match="BACKFILL_AUTHORITY_INVALID"):
        capture_source(root, now=NOW)


def test_unrelated_corrupt_status_is_global_integrity_failure(evidence):
    root, status, _ = evidence
    status["symbol"] = "600037"
    (root / "status/600036.json").write_bytes(canonical(status))
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        capture_source(root, now=NOW)


def bar(day=DAY):
    return {"date": day, "raw_open": 10, "raw_close": 10, "raw_volume": 1000,
            "amount": 10000, "buy_block": 0.0, "sell_block": 0.0}


@pytest.mark.parametrize("state,reason", [("0", "SUSPENDED"), ("", "UNKNOWN"), (None, "UNKNOWN")])
def test_bad_trade_state_blocks_matching_symbol_day_only(evidence, state, reason):
    _, status, _ = evidence
    status["rows"][0]["tradestatus"] = state
    rows, _, ledger = restrict_rows([bar(), bar("2026-10-26")], "600036", status)
    assert rows[0]["buy_block"] == rows[0]["sell_block"] == 1.0
    assert reason in ledger[0]["reason"]
    assert rows[1] == bar("2026-10-26")


def test_raw_source_conflict_blocks_both_without_repairing_price(evidence):
    _, status, _ = evidence
    status["rows"][0]["close"] = "20"
    original = bar()
    rows, _, ledger = restrict_rows([original], "600036", status)
    assert rows[0]["buy_block"] == rows[0]["sell_block"] == 1.0
    assert rows[0]["raw_close"] == original["raw_close"] == 10
    assert ledger[0]["reason"] == "RAW_SOURCE_CONFLICT_BLOCK_BOTH"


@pytest.mark.parametrize("field", ["suspended", "st", "upper_limit", "lower_limit", "corporate_action_clear"])
def test_unknown_execution_fact_stays_blocked_even_if_research_data_exists(field):
    security = ObservedSecurity("600036", "SSE", D(10), D(10), 1000, 100, D("0.01"),
                                False, False, D(11), D(9), True, "evidence", 1000)
    assert security.block_reason() is None
    assert replace(security, **{field: None}).block_reason() == "UNKNOWN_TRADING_CONSTRAINTS"


def test_current_missing_symbol_cannot_get_a_stale_prediction():
    codes = expected_codes(["SH600036\t2020-01-01\t2026-10-27",
                            "SH600037\t2020-01-01\t2026-10-27"], ["600036"], DAY)
    assert codes == ["SH600036"]
    with pytest.raises(ValueError, match="COVERAGE"):
        rank_scores([{"instrument": "SH600037", "score": 1.0}], codes)


@pytest.mark.parametrize("defect", ["missing_current", "old_basis_conflict"])
def test_benchmark_is_global_daily_dependency_and_cannot_be_waived(defect):
    old = [{"date": "2026-10-26", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100}]
    rows = [{**old[0], "day": "2026-10-26"},
            {"day": DAY, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100}]
    if defect == "missing_current":
        rows.pop()
    else:
        rows[0]["close"] = 20
    with pytest.raises(ValueError):
        append_benchmark(deepcopy(old), json.dumps(rows).encode(), end=DAY)
