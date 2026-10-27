import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from unittest.mock import Mock

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from scripts.unified_queue import UnifiedCollectionQueue, failure_delay
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
    assert row['consecutive_failures'] == 2
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


def test_new_grids_do_not_reset_oldest_progress_or_starve_the_remaining_pool(tmp_path):
    q = UnifiedCollectionQueue(tmp_path / 'queue.sqlite')
    morning = NOW.replace(hour=2)
    q.plan(master(4), CALENDAR, morning)
    first = claim(q, '5min', limit=2, now=morning)
    q.finish(receipt(first), now=morning)
    later = morning + timedelta(minutes=5)
    q.plan(master(4), CALENDAR, later)
    remaining = claim(q, '5min', limit=2, now=later)
    assert set(remaining['spec']['symbols']).isdisjoint(first['spec']['symbols'])
    assert remaining['spec']['target_end'] > first['spec']['target_end']
    q.finish(receipt(remaining), now=later)
    newest = later + timedelta(minutes=5)
    q.plan(master(4), CALENDAR, newest)
    oldest = claim(q, '5min', limit=2, now=newest)
    assert oldest['spec']['symbols'] == first['spec']['symbols']
    assert all(v == first['spec']['target_end']
               for v in oldest['spec']['after_by_symbol'].values())
    # A grid update cannot rewrite an already claimed batch's clock or row boundary.
    q.plan(master(4), CALENDAR, newest + timedelta(minutes=5))
    q.finish(receipt(oldest), now=newest + timedelta(minutes=5))
    row = next(t for t in q.snapshot(VERSION)['tasks'] if t['symbol'] == '600000')
    assert row['state'] == 'PLANNED'
    assert row['completed_end'] == oldest['spec']['target_end']
    assert row['desired_end'] > row['completed_end']


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


def test_legacy_price_only_admission_is_retained_but_requeued_once_for_amount(tmp_path):
    q = UnifiedCollectionQueue(tmp_path / 'queue.sqlite')
    q.plan(master(1), CALENDAR, NOW)
    old = claim(q)
    value = receipt(old)
    value['outcomes']['600000']['missing_amount_bars'] = 1
    value = signed({k: v for k, v in value.items() if k != 'evidence_sha256'})
    q.finish(value, now=NOW)
    store = Mock()
    store.load.return_value = value
    assert q.recheck_daily_amounts(store, now=NOW) == 1
    assert q.recheck_daily_amounts(store, now=NOW) == 0
    store.load.assert_called_once_with(old['batch_id'])
    row = next(r for r in q.snapshot(VERSION)['tasks'] if r['frequency'] == 'day')
    assert row['state'] == 'FAILED' and row['accepted_bar_count'] == 0
    assert q.snapshot(VERSION)['data_ready_events'] == 1  # Original evidence remains.
    retry = claim(q)
    assert retry['spec']['after_by_symbol']['600000'] is None


def successful_minute_history(queue, count=3):
    now = NOW.replace(hour=2)
    for _ in range(count):
        queue.plan(master(1), CALENDAR, now)
        batch = claim(queue, '5min', now=now)
        queue.finish(receipt(batch), now=now)
        now += timedelta(minutes=5)
    return now, batch


def fail_minute(queue, now):
    queue.plan(master(1), CALENDAR, now)
    batch = claim(queue, '5min', now=now)
    failed = receipt(batch, batch['spec']['symbols'])
    queue.finish(failed, now=now)
    queue.finish(failed, now=now)  # Replaying a receipt does not extend the streak.
    return batch


def minute_task(queue):
    return next(row for row in queue.snapshot(VERSION)['tasks'] if row['frequency'] == '5min')


def make_legacy(queue, *, custom_deadline=False):
    """Remove the new column/marker and reproduce the old cumulative-attempt deadline."""
    task = minute_task(queue)
    with queue.connect() as connection:
        latest = connection.execute("SELECT completed_at FROM collection_batches "
                                    "WHERE state='COMPLETE' ORDER BY rowid DESC LIMIT 1").fetchone()
        deadline = datetime.fromisoformat(latest['completed_at']) + timedelta(
            seconds=failure_delay(task['attempts']) + (37 if custom_deadline else 0))
        connection.execute("UPDATE collection_tasks SET next_attempt_at=? WHERE frequency='5min'",
                           (deadline.isoformat(timespec='seconds'),))
        connection.execute("DELETE FROM collection_metadata WHERE key='failure_streak_v1'")
        connection.execute('ALTER TABLE collection_tasks DROP COLUMN consecutive_failures')
        batches = [dict(row) for row in connection.execute(
            'SELECT rowid,* FROM collection_batches')]
    return deadline, batches


def test_successes_do_not_inflate_first_failure_and_recovery_resets_streak(tmp_path):
    queue = UnifiedCollectionQueue(tmp_path/'queue.sqlite')
    now, success = successful_minute_history(queue)
    fail_minute(queue, now)
    task = minute_task(queue)
    assert task['attempts'] == 4 and task['consecutive_failures'] == 1
    assert datetime.fromisoformat(task['next_attempt_at']) == now + timedelta(seconds=60)
    assert task['last_success_batch'] == success['batch_id']
    now += timedelta(seconds=61)
    fail_minute(queue, now)
    task = minute_task(queue)
    assert task['attempts'] == 5 and task['consecutive_failures'] == 2
    assert datetime.fromisoformat(task['next_attempt_at']) == now + timedelta(seconds=120)
    now += timedelta(seconds=121)
    recovered = claim(queue, '5min', now=now)
    queue.finish(receipt(recovered), now=now)
    assert minute_task(queue)['consecutive_failures'] == 0
    now += timedelta(minutes=5)
    fail_minute(queue, now)
    task = minute_task(queue)
    assert task['attempts'] == 7 and task['consecutive_failures'] == 1
    assert datetime.fromisoformat(task['next_attempt_at']) == now + timedelta(seconds=60)


@pytest.mark.parametrize('failure_count', [1, 2, 10])
def test_legacy_migration_reconstructs_proven_streak_and_real_failure_deadline_once(
    tmp_path, failure_count,
):
    database = tmp_path/'queue.sqlite'
    queue = UnifiedCollectionQueue(database)
    now, success = successful_minute_history(queue)
    # Completed batches for other identities cannot inflate this symbol's streak.
    with queue.connect() as connection:
        for index, change in enumerate(({'symbols': ['600001']}, {'frequency': 'day'},
                                       {'universe_version': 'c'*64},
                                       {'target_end': '2026-10-25T02:10:00+00:00'})):
            spec = {**success['spec'], **change}
            connection.execute('INSERT INTO collection_batches VALUES(?,?,?,?,?,?)',
                               (f'noise-{index}', json.dumps(spec), 'COMPLETE',
                                now.isoformat(), now.isoformat(), 'd'*64))
    for index in range(failure_count):
        fail_minute(queue, now)
        if index < failure_count - 1:
            now += timedelta(seconds=failure_delay(index + 1) + 1)
    previous = minute_task(queue)
    old_deadline, batches = make_legacy(queue)
    restored = UnifiedCollectionQueue(database)
    task = minute_task(restored)
    assert task['attempts'] == 3 + failure_count
    assert task['consecutive_failures'] == min(9, failure_count)
    corrected = now + timedelta(seconds=failure_delay(failure_count))
    assert datetime.fromisoformat(task['next_attempt_at']) == corrected
    assert corrected <= old_deadline
    assert task['last_success_batch'] == previous['last_success_batch']
    assert task['completed_end'] == previous['completed_end']
    with restored.connect() as connection:
        assert batches == [dict(row) for row in connection.execute(
            'SELECT rowid,* FROM collection_batches')]
        marker = connection.execute("SELECT value FROM collection_metadata "
                                    "WHERE key='failure_streak_v1'").fetchone()['value']
        metadata = json.loads(marker)
        assert metadata['proven_streaks'] == 1
        assert metadata['repaired_retry_deadlines'] == (failure_count < 9)
        connection.execute("UPDATE collection_tasks SET next_attempt_at=? WHERE frequency='5min'",
                           (old_deadline.isoformat(timespec='seconds'),))
    # The one-time migration never overrides a later operator/custom scheduling change.
    again = UnifiedCollectionQueue(database)
    assert datetime.fromisoformat(minute_task(again)['next_attempt_at']) == old_deadline
    with again.connect() as connection:
        assert connection.execute("SELECT value FROM collection_metadata "
                                  "WHERE key='failure_streak_v1'").fetchone()['value'] == marker


def test_migration_preserves_unproven_or_custom_retry_deadlines(tmp_path):
    for suffix, successes, custom, missing_anchor in [('never', 0, False, False),
                                                     ('custom', 3, True, False),
                                                     ('missing', 3, False, True)]:
        database = tmp_path/(suffix+'.sqlite')
        queue = UnifiedCollectionQueue(database)
        now = NOW.replace(hour=2)
        if successes:
            now, _ = successful_minute_history(queue, successes)
        fail_minute(queue, now)
        deadline, _ = make_legacy(queue, custom_deadline=custom)
        if missing_anchor:
            with sqlite3.connect(database) as connection:
                connection.execute("UPDATE collection_tasks SET last_success_batch='missing' "
                                   "WHERE frequency='5min'")
        restored = UnifiedCollectionQueue(database)
        task = minute_task(restored)
        assert datetime.fromisoformat(task['next_attempt_at']) == deadline
        assert task['consecutive_failures'] == (1 if custom else successes + 1)


def test_migration_preserves_failed_streak_through_running_and_interrupted_recovery(tmp_path):
    database = tmp_path/'queue.sqlite'
    queue = UnifiedCollectionQueue(database)
    now, _ = successful_minute_history(queue)
    fail_minute(queue, now)
    now += timedelta(seconds=61)
    claim(queue, '5min', now=now)
    make_legacy(queue)
    restored = UnifiedCollectionQueue(database)
    assert minute_task(restored)['consecutive_failures'] == 1
    store = Mock()
    store.batch_path.return_value.is_file.return_value = False
    restored.recover(store, now=now)
    assert minute_task(restored)['consecutive_failures'] == 1
    now += timedelta(seconds=61)
    fail_minute(restored, now)
    task = minute_task(restored)
    assert task['consecutive_failures'] == 2
    assert datetime.fromisoformat(task['next_attempt_at']) == now + timedelta(seconds=120)
