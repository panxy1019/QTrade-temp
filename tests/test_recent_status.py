from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from qtrade.monitoring.data_freshness import Calendar, signed
from qtrade.research.workflow import digest
from scripts import collect_recent_status as collector

NOW = datetime(2026, 10, 28, 1, 0, tzinfo=UTC)
REVISION = 'a' * 40
DATASET = {'stocks': [{'symbol': '000001'}, {'symbol': '600621'}], 'end': '2026-10-23'}


class Clock(datetime):
    value = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.value.astimezone(tz)


class Result:
    error_code = '0'
    fields = ['date', 'code', 'tradestatus', 'isST']

    def __init__(self, values):
        self.values = iter(values)

    def next(self):
        self.row = next(self.values, None)
        return self.row is not None

    def get_row_data(self):
        return [self.row[k] for k in self.fields]


@pytest.fixture
def setup(tmp_path_factory, monkeypatch):
    # Keep content-addressed paths below Windows' legacy path-length boundary.
    tmp_path = tmp_path_factory.mktemp('rs')
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def immutable(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x') as stream:
            json.dump(value, stream)

    @contextmanager
    def lease(**kwargs):
        yield

    monkeypatch.setattr(collector, 'datetime', Clock)
    monkeypatch.setattr(collector, 'write_json_atomic', write)
    monkeypatch.setattr(collector, 'write_json_immutable', immutable)
    from scripts import pit_collection_queue
    monkeypatch.setattr(pit_collection_queue, 'write_json_atomic', write)
    monkeypatch.setattr(collector, 'baostock_session', lease)
    monkeypatch.setattr(collector.shutil, 'disk_usage', lambda _: SimpleNamespace(free=101*1024**3))
    monkeypatch.setattr(collector.importlib.metadata, 'version', lambda _: '0.9.3-test')
    monkeypatch.setenv('LIVE_TRADING_ENABLED', 'false')
    Clock.value = NOW
    calendar = Calendar(collector.REPO / 'configs/observer_calendar_2026.json')
    requests = []

    def query(**request):
        requests.append(request)
        days = collector.window_scope(DATASET, calendar, Clock.value)['expected_days']
        return Result([{'code': request['code'], 'date': day, 'tradestatus': '0', 'isST': '0'}
                       for day in days])

    vendor = SimpleNamespace(query_history_k_data_plus=query,
                             login=lambda: SimpleNamespace(error_code='0'), logout=lambda: None)
    return tmp_path, calendar, vendor, requests


def run(setup, **kwargs):
    base, calendar, vendor, _ = setup
    return collector.collect_pass(base, DATASET, calendar, revision=REVISION,
                                  vendor=vendor, **kwargs)


def test_recent_window_resumes_and_keeps_legacy_backfill_unchanged(setup):
    base, _, _, requests = setup
    legacy = base / 'pit-repair/status/000001.json'
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b'original historical bytes')
    first = run(setup, batch_size=1)
    assert first['state'] == 'PARTIAL'
    assert first['collection_progress']['completed_symbols'] == 1
    root = base / 'recent-status/windows' / first['window_sha256']
    original = (root / 'status/000001.json').read_bytes()
    second = run(setup, batch_size=1)
    assert second['state'] == 'COMPLETE' and len(requests) == 2
    assert (root / 'status/000001.json').read_bytes() == original
    assert legacy.read_bytes() == b'original historical bytes'
    assert second['scope']['end'] == '2026-10-27'
    assert len(second['status_checksums']) == 2
    record = json.loads(original)
    assert record['system_available_at'] == record['observed_at'] == NOW.isoformat()
    assert record['historical_available_at'] is None
    assert record['certified_at_decision'] is False
    assert record['raw_transport_bytes_captured'] is False
    Clock.value += timedelta(seconds=1)
    third = run(setup)
    assert third['outcome'] == 'NO_QUERY_NEEDED' and len(requests) == 2


def test_missing_day_keeps_response_but_cannot_complete_or_infer_suspension(setup):
    base, _, vendor, requests = setup
    vendor.query_history_k_data_plus = lambda **_: Result([])
    value = run(setup)
    assert value['state'] == 'PARTIAL'
    assert value['collection_progress']['completed_symbols'] == 0
    root = base / 'recent-status/windows' / value['window_sha256']
    assert len(list((root / 'responses').glob('*.json'))) == 1
    assert not (root / 'status').exists()
    ledger = json.loads((root / 'symbol-attempts.json').read_bytes())
    assert ledger['entries']['000001']['state'] == 'FAILED'
    again = run(setup)
    # The second new symbol is attempted while the first stays in backoff.
    assert again['collection_progress']['waiting_retry_symbols'] == 2
    assert again['collection_progress']['completed_symbols'] == 0
    assert requests == []


def test_vendor_lease_busy_never_logs_in_or_counts_attempts(setup, monkeypatch):
    _, _, vendor, requests = setup

    @contextmanager
    def busy(**kwargs):
        raise BlockingIOError
        yield  # pragma: no cover

    def forbidden():
        pytest.fail('anonymous vendor session must not start without its lease')

    monkeypatch.setattr(collector, 'baostock_session', busy)
    vendor.login = forbidden
    result = run(setup)
    assert result['outcome'] == 'VENDOR_LEASE_BUSY_NO_QUERY_SENT'
    assert result['collection_progress']['unattempted_symbols'] == 2
    assert requests == []


def test_new_close_uses_new_window_without_rewriting_original_observations(setup):
    base, _, _, requests = setup
    first = run(setup)
    old = base / 'recent-status/windows' / first['window_sha256'] / 'status/000001.json'
    original = old.read_bytes()
    Clock.value = datetime(2026, 10, 28, 8, 30, tzinfo=UTC)
    second = run(setup)
    assert second['scope']['end'] == '2026-10-28'
    assert second['window_sha256'] != first['window_sha256']
    assert second['collection_progress']['completed_symbols'] == 2
    assert len(requests) == 4 and old.read_bytes() == original


@pytest.mark.parametrize('mutation', ['hash', 'window', 'clock', 'missing_day', 'symbol'])
def test_cached_content_must_revalidate_before_reuse(setup, mutation):
    base, _, _, requests = setup
    first = run(setup)
    path = base / 'recent-status/windows' / first['window_sha256'] / 'status/000001.json'
    value = json.loads(path.read_bytes())
    if mutation == 'hash':
        value['rows'][0]['isST'] = '1'
    else:
        value.pop('evidence_sha256')
        if mutation == 'window':
            value['window_sha256'] = 'f' * 64
        elif mutation == 'clock':
            value['observed_at'] = '2099-01-01T00:00:00+00:00'
        elif mutation == 'missing_day':
            value['rows'].pop()
        else:
            value['symbol'] = '600621'
        value = signed(value)
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        run(setup)
    assert len(requests) == 2


@pytest.mark.parametrize('stocks', [[], [{'symbol': '510300'}],
                                    [{'symbol': '000001'}, {'symbol': '000001'}]])
def test_scope_rejects_empty_duplicates_and_unsupported_funds(setup, stocks):
    _, calendar, _, _ = setup
    with pytest.raises(ValueError, match='SCOPE_INVALID'):
        collector.window_scope({'stocks': stocks}, calendar, NOW)


def test_window_uses_verified_calendar_and_closed_daily_cutoff(setup):
    _, calendar, _, _ = setup
    scope = collector.window_scope(DATASET, calendar, NOW)
    assert scope['expected_days'] == ['2026-10-23', '2026-10-26', '2026-10-27']
    assert scope['source_dataset_sha256'] == digest(DATASET)
    with pytest.raises(ValueError, match='CALENDAR_OUTSIDE'):
        collector.window_scope(DATASET, calendar, datetime(2027, 1, 1, tzinfo=UTC))


def test_watcher_restart_obeys_durable_cadence(setup):
    base, _, _, _ = setup
    assert collector.next_pass_delay(base, NOW) == 0
    run(setup)
    assert collector.next_pass_delay(base, NOW) == 300
    assert collector.next_pass_delay(base, NOW + timedelta(seconds=299)) == 1
    assert collector.next_pass_delay(base, NOW + timedelta(seconds=301)) == 0
    with pytest.raises(ValueError, match='SCHEDULE_CLOCK'):
        collector.next_pass_delay(base, NOW - timedelta(seconds=1))


def test_corrupt_schedule_cannot_release_another_vendor_batch(setup):
    base, _, _, _ = setup
    run(setup)
    path = base / 'recent-status/current.json'
    value = json.loads(path.read_bytes())
    value['observed_at'] = '2020-01-01T00:00:00+00:00'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='SCHEDULE_IDENTITY'):
        collector.next_pass_delay(base, NOW)


def test_gap_priority_reorders_only_new_work_without_changing_the_window_identity(setup):
    base, calendar, vendor, requests = setup
    scope = collector.window_scope(DATASET, calendar, NOW)
    result = collector.collect_pass(base, DATASET, calendar, batch_size=1,
        revision=REVISION, vendor=vendor, priority_symbols=('600621',))
    assert requests[0]['code'] == 'sh.600621'
    assert result['window_sha256'] == digest(scope)
    assert result['collection_progress']['completed_symbols'] == 1
    assert not (base / 'pit-repair').exists()
