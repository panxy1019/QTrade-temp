from datetime import UTC, date, datetime, timedelta
from unittest.mock import Mock

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from scripts.unified_queue import UnifiedCollectionQueue
from scripts.unified_store import SCHEMA

NOW = datetime(2026, 10, 26, 9, tzinfo=UTC)
VERSION = "a" * 64
CALENDAR = Calendar({"valid_from": "2026-10-08", "valid_through": "2026-10-31",
                     "weekday_closures": []})


def master(size=10):
    return signed({**AUTHORITY, "universe_version": VERSION, "target_actual": size,
                   "securities": [{"symbol": f"600{i:03d}", "in_target": True,
                                   "security_type": "STOCK", "status": "ACTIVE_OBSERVED",
                                   "issues": []} for i in range(size)]})


def claim(queue, frequency="day", limit=5, now=NOW):
    return queue.claim(VERSION, frequency, now.date(), now=now,
                       code_revision="b" * 40, limit=limit)


def receipt(batch, failures=()):
    spec = batch["spec"]
    return signed({**AUTHORITY, "schema_version": SCHEMA, "batch_id": batch["batch_id"],
                   "spec": spec, "bar_count": len(spec["symbols"]) - len(failures),
                   "outcomes": {s: {"state": "FAILED" if s in failures else "SUCCEEDED",
                                    "start": None if s in failures else spec["target_end"],
                                    "end": None if s in failures else spec["target_end"],
                                    "bar_count": 0 if s in failures else 1,
                                    "error_code": "SOURCE_UNAVAILABLE" if s in failures else None}
                                for s in spec["symbols"]}})


def test_plans_are_idempotent_and_daily_minute_results_are_independent(tmp_path):
    q = UnifiedCollectionQueue(tmp_path / 'queue.sqlite')
    q.plan(master(), CALENDAR, NOW)
    q.plan(master(), CALENDAR, NOW)
    assert len(q.snapshot(VERSION)["tasks"]) == 20
    day = claim(q)
    q.finish(receipt(day), now=NOW)
    q.finish(receipt(day), now=NOW)
    minute = claim(q, "5min")
    assert minute["spec"]["symbols"] == day["spec"]["symbols"]
    q.finish(receipt(minute, minute["spec"]["symbols"]), now=NOW)
    tasks = q.snapshot(VERSION)["tasks"]
    assert sum(r["state"] == 'SUCCEEDED' for r in tasks) == 5
    assert sum(r["state"] == 'FAILED' for r in tasks) == 5
    assert q.snapshot(VERSION)["data_ready_events"] == 1


def test_persistent_failures_do_not_starve_new_symbols_and_retries_keep_backoff(tmp_path):
    q = UnifiedCollectionQueue(tmp_path / 'queue.sqlite')
    q.plan(master(12), CALENDAR, NOW)
    first = claim(q)
    q.finish(receipt(first, first["spec"]["symbols"]), now=NOW)
    second = claim(q, now=NOW + timedelta(seconds=61))
    assert len(set(second["spec"]["symbols"]) & set(first["spec"]["symbols"])) == 1
    q.finish(receipt(second, second["spec"]["symbols"]), now=NOW + timedelta(seconds=61))
    row = next(r for r in q.snapshot(VERSION)["tasks"] if r["symbol"] == '600000'
               and r["frequency"] == 'day')
    assert row["attempts"] == 2
    assert datetime.fromisoformat(row["next_attempt_at"]) == NOW + timedelta(seconds=181)
    assert row["last_success_batch"] is None


def test_restart_replays_sealed_batch_or_marks_missing_attempt_interrupted(tmp_path):
    db = tmp_path / 'queue.sqlite'
    q = UnifiedCollectionQueue(db)
    q.plan(master(), CALENDAR, NOW)
    batch = claim(q)
    store = Mock()
    store.batch_path.return_value.is_file.return_value = True
    store.load.return_value = receipt(batch)
    restored = UnifiedCollectionQueue(db)
    assert restored.recover(store, now=NOW)["replayed"] == 1
    assert claim(restored)["spec"]["symbols"] != batch["spec"]["symbols"]
    store.batch_path.return_value.is_file.return_value = False
    assert restored.recover(store, now=NOW)["interrupted"] == 1
    assert any(r["state"] == 'INTERRUPTED' for r in restored.snapshot(VERSION)["tasks"])


def test_new_intraday_target_advances_plan_but_keeps_previous_success(tmp_path):
    q = UnifiedCollectionQueue(tmp_path / 'queue.sqlite')
    morning = NOW.replace(hour=2)  # 10:00 Shanghai; latest completed 09:55
    ends = q.plan(master(1), CALENDAR, morning)
    assert 'day' not in ends
    first = claim(q, '5min', now=morning)
    q.finish(receipt(first), now=morning)
    assert claim(q, '5min', now=morning) is None
    q.plan(master(1), CALENDAR, morning + timedelta(minutes=5))
    second = claim(q, '5min', now=morning + timedelta(minutes=5))
    assert second["spec"]["after_by_symbol"] == {"600000": first["spec"]["target_end"]}
    q.finish(receipt(second, ['600000']), now=morning + timedelta(minutes=5))
    row = q.snapshot(VERSION)["tasks"][0]
    assert row["last_success_batch"] == first["batch_id"]
    assert row["completed_end"] == first["spec"]["target_end"]


def test_wrong_specs_corrupt_universe_and_unknown_receipts_do_not_advance_queue(tmp_path):
    q = UnifiedCollectionQueue(tmp_path / 'queue.sqlite')
    bad = master()
    bad["target_actual"] = 9
    with pytest.raises(ValueError, match="IDENTITY"):
        q.plan(bad, CALENDAR, NOW)
    q.plan(master(), CALENDAR, NOW)
    batch = claim(q)
    forged = receipt(batch)
    forged["spec"]["symbols"] = ['600001']
    with pytest.raises(ValueError):
        q.finish(forged, now=NOW)
    assert q.snapshot(VERSION)["data_ready_events"] == 0


def test_old_dates_remain_archived_but_snapshot_is_bounded_to_current_scope(tmp_path):
    q = UnifiedCollectionQueue(tmp_path / 'queue.sqlite')
    q.plan(master(1), CALENDAR, NOW)
    q.plan(master(1), CALENDAR, NOW + timedelta(days=1))
    snapshot = q.snapshot(VERSION)
    assert len(snapshot["tasks"]) == 2
    assert {r['trading_day'] for r in snapshot['tasks']} == {'2026-10-27'}
    assert q.claim(VERSION, 'day', date(2026, 10, 26), now=NOW,
                   code_revision='b' * 40) is not None
