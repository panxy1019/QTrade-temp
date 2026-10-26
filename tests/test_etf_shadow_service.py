from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research import etf_research_contract as contract
from qtrade.research import etf_trend
from qtrade.research.data_versions import file_hash
from qtrade.research.etf_shadow_publication import validate_etf_shadow_publication
from scripts import run_etf_shadow_service as service
from scripts.unified_store import UnifiedBatchStore

FROZEN = datetime(2026, 10, 26, 10, tzinfo=UTC)
OBSERVED = datetime(2026, 10, 27, 10, tzinfo=UTC)
REVISION = 'c'*40


def write(path, value, **kwargs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def immutable(path, value):
    if path.exists():
        raise FileExistsError(path)
    write(path, value)


def resign(value):
    return signed({key: item for key, item in value.items() if key != 'evidence_sha256'})


@pytest.fixture
def runtime(monkeypatch):
    # Synthetic source fixtures prove service behavior, never real-session acceptance.
    directory = Path(service.REPO.anchor) if os.name == 'nt' else None
    with TemporaryDirectory(prefix='etfsh-', dir=directory) as temporary:
        root = Path(temporary)
        base, local = root/'b', root/'l'
        archives = base/'online-research/etf-trend'
        archives.mkdir(parents=True)
        calendar = Calendar(service.REPO/'configs/observer_calendar_2026.json')
        clock = [FROZEN]
        monkeypatch.setenv('LIVE_TRADING_ENABLED', 'false')
        monkeypatch.setattr(service, 'utc_now', lambda: clock[0])
        monkeypatch.setattr(service.shutil, 'disk_usage', lambda _: SimpleNamespace(
            free=200*1024**3))

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                value = datetime(2026, 10, 30, 12, tzinfo=UTC)
                return value.astimezone(tz) if tz else value.replace(tzinfo=None)

        monkeypatch.setattr(etf_trend, 'datetime', Clock)
        if os.name == 'nt':
            monkeypatch.setattr('scripts.unified_store._fsync_directory', lambda _: None)
            monkeypatch.setattr(service, 'write_json_atomic', write)
            monkeypatch.setattr(service, 'write_json_immutable', immutable)
            monkeypatch.setattr(contract, 'write_json_immutable', immutable)

        def archive(day=26, *, revision='b'*40, negative=False, missing=False, latest=True):
            stamp = datetime(2026, 10, day, 9, tzinfo=UTC)
            captures = []
            for symbol in ('510300', '159915'):
                rows = []
                for index, session in enumerate(etf_trend.sessions(calendar, date(2026, 10, day))):
                    close = Decimal('2')+Decimal(index)*Decimal('-.01' if negative else '.01')
                    rows.append({'day': session, 'open': str(close),
                                 'high': str(close+Decimal('.1')), 'low': str(close-Decimal('.1')),
                                 'close': str(close), 'volume': '1000'})
                if missing:
                    rows.pop(4)
                captures.append({'source_id': 'sina_klines', 'request': {
                    'symbol': ('sh' if symbol.startswith('5') else 'sz')+symbol,
                    'scale': '240', 'ma': 'no', 'datalen': '63'},
                    'received_at': stamp.isoformat(), 'body': json.dumps(rows).encode(),
                    'status_code': 200, 'transport_error': None, 'body_complete': True})
            bindings = {'master_sha256': 'a'*64, 'as_of': str(stamp.date()),
                        'code_revision': revision,
                        'calendar_sha256': signed(calendar.document)['evidence_sha256'],
                        'contract': 'ETF_CLOSE_DIAGNOSTIC_V1'}
            identity = signed(bindings)['evidence_sha256']
            path = archives/identity
            path.mkdir()
            raw = UnifiedBatchStore(path/'capture-store').capture(captures)
            write(path/'receipt.json', signed({**AUTHORITY, 'bindings': bindings, 'raw': raw}))
            value = etf_trend.admit(captures, ['510300', '159915'], calendar, stamp.date(), stamp)
            value.update(bindings=bindings, receipt_sha256=file_hash(path/'receipt.json'))
            write(path/'diagnostic.json', resign(value))
            if latest:
                write(archives/'latest.json', signed({**AUTHORITY, 'diagnostic_id': identity}))
            return path

        def run(revision=REVISION, stop=None):
            return service.cycle(base, local, revision, stop=stop, now=clock[0])

        yield SimpleNamespace(base=base, local=local, archives=archives, calendar=calendar,
                              clock=clock, archive=archive, run=run)


def test_actual_raw_archive_freeze_and_signed_publication(runtime):
    source = runtime.archive()
    value = runtime.run()
    assert value['state'] == 'RUNNING' and value['errors'] == []
    assert validate_etf_shadow_publication(value, now=FROZEN) == value
    target = value['entries'][0]['target']
    assert target['bindings']['diagnostic_id'] == source.name
    assert target['frozen_at'] == FROZEN.isoformat()
    assert value['entries'][0]['observation'] is None
    assert len(target['weights']) == 2
    assert all(value[key] == [] for key in ('orders', 'fills', 'holdings'))
    assert all(value[key] is None for key in ('cash', 'net_asset_value', 'forward_return'))
    assert json.loads((runtime.base/'workflow/public/etf-forward.json').read_bytes()) == value
    assert not (runtime.base/'workflow/public/forward.json').exists()
    assert not list(runtime.local.rglob('*.sqlite'))


def test_restart_replays_original_revision_without_second_target(runtime):
    runtime.archive()
    first = runtime.run()['entries'][0]['target']
    path = runtime.local/'targets'/first['target_id']/'target.json'
    original = path.read_bytes()
    runtime.clock[0] += timedelta(hours=1)
    replay = runtime.run(revision='d'*40)
    assert replay['source_revision'] == 'd'*40
    assert replay['entries'][0]['target'] == first
    assert path.read_bytes() == original
    assert len(list(runtime.local.glob('targets/*/target.json'))) == 1


def test_missed_open_cannot_create_target_or_even_intent(runtime):
    runtime.archive()
    runtime.clock[0] = datetime(2026, 10, 27, 2, tzinfo=UTC)
    value = runtime.run()
    assert 'ETF_SHADOW_PROSPECTIVE_FREEZE_MISSED' in value['errors']
    assert value['entries'] == []
    assert not list(runtime.local.rglob('intent.json'))
    assert not list(runtime.local.glob('targets/*/target.json'))


def test_crash_after_intent_does_not_backdate_missing_target(runtime, monkeypatch):
    runtime.archive()
    original = service.freeze_etf_target
    monkeypatch.setattr(service, 'freeze_etf_target', lambda *a, **k: (_ for _ in ()).throw(
        OSError('interrupted write')))
    assert runtime.run()['entries'] == []
    assert len(list(runtime.local.rglob('intent.json'))) == 1
    monkeypatch.setattr(service, 'freeze_etf_target', original)
    runtime.clock[0] = datetime(2026, 10, 27, 2, tzinfo=UTC)
    result = runtime.run()
    assert 'ETF_SHADOW_TARGET_OR_OBSERVATION_REPLAY_FAILED' in result['errors']
    assert result['entries'] == [] and not list(runtime.local.glob('targets/*/target.json'))


def test_crash_after_target_before_binding_reuses_target_after_open(runtime, monkeypatch):
    runtime.archive()
    original = service.record

    def fail(path, *args):
        if path.name == 'target-binding.json':
            raise OSError('binding interrupted')
        return original(path, *args)

    monkeypatch.setattr(service, 'record', fail)
    assert runtime.run()['entries'] == []
    target_path = next(runtime.local.glob('targets/*/target.json'))
    original_bytes = target_path.read_bytes()
    monkeypatch.setattr(service, 'record', original)
    runtime.clock[0] = datetime(2026, 10, 27, 2, tzinfo=UTC)
    value = runtime.run(revision='d'*40)
    assert len(value['entries']) == 1 and value['errors'] == []
    assert target_path.read_bytes() == original_bytes


def test_production_freeze_samples_real_clock_after_raw_replay(runtime, monkeypatch):
    runtime.archive()
    runtime.clock[0] = datetime(2026, 10, 27, 1, 29, 59, tzinfo=UTC)
    original = contract._inputs

    def crossing_open(*args):
        value = original(*args)
        runtime.clock[0] += timedelta(seconds=2)
        return value

    monkeypatch.setattr(contract, '_inputs', crossing_open)
    monkeypatch.setattr(contract, '_clock', lambda now: now or runtime.clock[0])
    # No injected cycle time exercises the same path as the production CLI.
    value = service.cycle(runtime.base, runtime.local, REVISION)
    assert value['entries'] == []
    assert 'ETF_SHADOW_TARGET_OR_OBSERVATION_REPLAY_FAILED' in value['errors']
    assert not list(runtime.local.glob('targets/*/target.json'))


def test_late_durable_target_write_is_never_exported_or_resurrected(runtime, monkeypatch):
    runtime.archive()
    runtime.clock[0] = datetime(2026, 10, 27, 1, 29, 59, tzinfo=UTC)
    original = contract.write_json_immutable

    def crossing_open(path, value):
        original(path, value)
        if path.name == 'target.json':
            runtime.clock[0] += timedelta(seconds=2)

    monkeypatch.setattr(contract, 'write_json_immutable', crossing_open)
    monkeypatch.setattr(contract, '_clock', lambda now: now or runtime.clock[0])
    value = service.cycle(runtime.base, runtime.local, REVISION)
    assert value['entries'] == []
    assert 'ETF_SHADOW_TARGET_OR_OBSERVATION_REPLAY_FAILED' in value['errors']
    assert len(list(runtime.local.glob('targets/*/target.json'))) == 1
    assert not list(runtime.local.glob('targets/*/admission.json'))
    assert service.cycle(runtime.base, runtime.local, 'd'*40)['entries'] == []
    assert not list(runtime.local.rglob('target-binding.json'))


def test_exact_next_session_observation_is_raw_replayed_and_idempotent(runtime):
    runtime.archive()
    target = runtime.run()['entries'][0]['target']
    observed_source = runtime.archive(27)
    runtime.clock[0] = OBSERVED
    value = runtime.run()
    old = next(row for row in value['entries'] if row['target']['target_id'] == target['target_id'])
    observation = old['observation']
    assert observation['bindings']['diagnostic_id'] == observed_source.name
    assert observation['session'] == '2026-10-27' and len(observation['observed_closes']) == 2
    assert observation['fills'] == [] and observation['forward_return'] is None
    paths = list(runtime.local.glob('targets/*/observations/*.json'))
    assert len(paths) == 1
    before = paths[0].read_bytes()
    runtime.clock[0] += timedelta(minutes=1)
    replay = runtime.run(revision='d'*40)
    assert replay['entries'][1]['observation'] == observation
    assert paths[0].read_bytes() == before
    assert validate_etf_shadow_publication(replay, now=runtime.clock[0]) == replay


def test_missing_next_session_is_reported_and_later_day_does_not_substitute(runtime):
    runtime.archive()
    runtime.run()
    runtime.archive(28)
    runtime.clock[0] = datetime(2026, 10, 28, 10, tzinfo=UTC)
    value = runtime.run()
    assert 'ETF_SHADOW_WAITING_EXACT_NEXT_SESSION_SOURCE' in value['errors']
    assert value['entries'][-1]['observation'] is None
    assert not list(runtime.local.glob('targets/*/observations/*.json'))


def test_later_latest_pointer_still_finds_exact_archived_observation_day(runtime):
    runtime.archive()
    target = runtime.run()['entries'][0]['target']
    expected = runtime.archive(27)
    runtime.archive(28)
    runtime.clock[0] = datetime(2026, 10, 28, 10, tzinfo=UTC)
    value = runtime.run()
    entry = next(row for row in value['entries']
                 if row['target']['target_id'] == target['target_id'])
    assert entry['observation']['bindings']['diagnostic_id'] == expected.name
    assert entry['observation']['session'] == '2026-10-27'


def test_older_pending_session_is_processed_outside_public_window(runtime, monkeypatch):
    runtime.archive()
    runtime.run()
    runtime.archive(27)
    runtime.archive(28)
    runtime.clock[0] = datetime(2026, 10, 28, 10, tzinfo=UTC)
    monkeypatch.setattr(service, 'PUBLIC_ENTRIES', 1)
    value = runtime.run()
    assert len(value['entries']) == 1
    assert value['entries'][0]['target']['bindings']['as_of'] == '2026-10-28'
    assert len(list(runtime.local.rglob('completed.json'))) == 1
    assert len(list(runtime.local.glob('targets/*/observations/*.json'))) == 1
    assert runtime.run()['errors'] == []


def test_crash_after_observation_source_intent_replays_pinned_source(runtime, monkeypatch):
    runtime.archive()
    runtime.run()
    expected = runtime.archive(27)
    runtime.clock[0] = OBSERVED
    original = service.observe_etf_shadow
    monkeypatch.setattr(service, 'observe_etf_shadow', lambda *a, **k: (_ for _ in ()).throw(
        OSError('observation interrupted')))
    assert 'ETF_SHADOW_TARGET_OR_OBSERVATION_REPLAY_FAILED' in runtime.run()['errors']
    assert len(list(runtime.local.rglob('observation-source.json'))) == 1
    assert not list(runtime.local.glob('targets/*/observations/*.json'))
    # A later release/archive cannot replace the already pinned next-session source.
    runtime.archive(27, revision='e'*40)
    monkeypatch.setattr(service, 'observe_etf_shadow', original)
    value = runtime.run(revision='d'*40)
    assert value['entries'][-1]['observation']['bindings']['diagnostic_id'] == expected.name


def test_corrupt_completed_observation_is_not_replaced_or_claimed(runtime):
    runtime.archive()
    runtime.run()
    runtime.archive(27)
    runtime.clock[0] = OBSERVED
    runtime.run()
    path = next(runtime.local.glob('targets/*/observations/*.json'))
    value = json.loads(path.read_bytes())
    value['forward_return'] = '0.15'
    write(path, resign(value))
    before = path.read_bytes()
    publication = runtime.run()
    assert 'ETF_SHADOW_TARGET_OR_OBSERVATION_REPLAY_FAILED' in publication['errors']
    assert all(entry['observation'] is None for entry in publication['entries'])
    assert path.read_bytes() == before


def test_future_source_receipt_never_pins_an_intent(runtime):
    runtime.archive()
    runtime.clock[0] = FROZEN-timedelta(hours=2)
    value = runtime.run()
    assert 'ETF_SHADOW_CURRENT_SOURCE_INVALID' in value['errors']
    assert value['entries'] == [] and not list(runtime.local.rglob('intent.json'))


def test_publication_window_is_byte_bounded_and_reports_truncation(runtime, monkeypatch):
    runtime.archive()
    original = runtime.run()
    empty = service.publish(runtime.base, runtime.local, REVISION, [], set(), Event(),
                            lambda: FROZEN)
    entry = original['entries'][0]
    maximum = len(json.dumps(empty, separators=(',', ':')).encode())+500
    monkeypatch.setattr(service, 'MAXIMUM_BYTES', maximum)
    value = service.publish(runtime.base, runtime.local, REVISION, [entry], set(), Event(),
                            lambda: FROZEN)
    assert value['entries'] == []
    assert value['errors'] == ['ETF_SHADOW_PUBLICATION_WINDOW_TRUNCATED']
    assert len(json.dumps(value, separators=(',', ':')).encode()) <= maximum


def test_archive_scan_limit_fails_before_any_target(runtime, monkeypatch):
    runtime.archive()
    monkeypatch.setattr(service, 'MAX_ARCHIVES', 0)
    with pytest.raises(ValueError, match='ARCHIVE_SCAN_BOUND'):
        runtime.run()
    assert not list(runtime.local.rglob('intent.json'))


def test_two_verified_next_session_archives_are_ambiguous_until_explicit_binding(runtime):
    runtime.archive()
    runtime.run()
    runtime.archive(27)
    runtime.archive(27, revision='e'*40, latest=False)
    runtime.clock[0] = OBSERVED
    value = runtime.run()
    assert 'ETF_SHADOW_OBSERVATION_SOURCE_AMBIGUOUS' in value['errors']
    assert value['entries'][-1]['observation'] is None
    assert not list(runtime.local.rglob('observation-source.json'))


def test_corrupt_source_does_not_discard_independent_verified_target(runtime):
    runtime.archive()
    first = runtime.run()['entries'][0]['target']
    source = runtime.archive(27)
    (source/'receipt.json').write_text('{}')
    runtime.clock[0] = OBSERVED
    value = runtime.run()
    assert value['state'] == 'DEGRADED'
    assert value['entries'][0]['target'] == first
    assert 'ETF_SHADOW_CURRENT_SOURCE_INVALID' in value['errors']
    assert 'ETF_SHADOW_OBSERVATION_SOURCE_INVALID' in value['errors']


def test_storage_reserve_and_stop_prevent_new_targets(runtime, monkeypatch):
    runtime.archive()
    monkeypatch.setattr(service.shutil, 'disk_usage', lambda _: SimpleNamespace(free=90*1024**3))
    result = runtime.run()
    assert result['errors'] == ['ETF_SHADOW_STORAGE_RESERVE']
    assert not list(runtime.local.rglob('intent.json'))
    monkeypatch.setattr(service.shutil, 'disk_usage', lambda _: SimpleNamespace(free=200*1024**3))
    stop = Event()
    stop.set()
    assert runtime.run(stop=stop)['state'] == 'STOPPED'
    assert not list(runtime.local.rglob('intent.json'))


def test_stop_replays_existing_evidence_but_never_adds_observation(runtime):
    runtime.archive()
    target = runtime.run()['entries'][0]['target']
    runtime.archive(27)
    runtime.clock[0] = OBSERVED
    stop = Event()
    stop.set()
    value = runtime.run(stop=stop)
    assert value['state'] == 'STOPPED'
    assert value['entries'][0] == {'target': target, 'observation': None}
    assert not list(runtime.local.rglob('observation-source.json'))


def test_unknown_input_is_not_a_cash_target_but_verified_negative_signal_can_be_empty(runtime):
    runtime.archive(missing=True)
    result = runtime.run()
    assert result['entries'] == []
    assert 'ETF_SHADOW_CURRENT_SOURCE_UNAVAILABLE' in result['errors']
    assert not list(runtime.local.rglob('intent.json'))


def test_verified_no_positive_input_is_a_target_with_no_account_values(runtime):
    runtime.archive(negative=True)
    value = runtime.run()
    assert value['state'] == 'RUNNING'
    assert value['entries'][0]['target']['weights'] == []
    assert value['entries'][0]['target']['cash'] is None


def test_changed_intent_and_forged_authority_fail_closed(runtime):
    runtime.archive()
    runtime.run()
    path = next(runtime.local.rglob('intent.json'))
    value = json.loads(path.read_bytes())
    value['implementation_revision'] = 'd'*40
    write(path, resign(value))
    result = runtime.run()
    assert result['entries'] == []
    assert 'ETF_SHADOW_TARGET_OR_OBSERVATION_REPLAY_FAILED' in result['errors']
    value['live_trading_enabled'] = 0
    write(path, resign(value))
    assert runtime.run()['entries'] == []


def test_configuration_has_no_live_or_naive_clock_override(runtime, monkeypatch):
    monkeypatch.setenv('LIVE_TRADING_ENABLED', 'true')
    with pytest.raises(ValueError, match='CONFIGURATION'):
        runtime.run()
    monkeypatch.setenv('LIVE_TRADING_ENABLED', 'false')
    with pytest.raises(ValueError, match='AWARE_CLOCK'):
        service.cycle(runtime.base, runtime.local, REVISION, now=datetime(2026, 10, 26))


def test_cli_exposes_no_future_clock_or_paper_recovery_option(monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', ['run_etf_shadow_service', '--help'])
    with pytest.raises(SystemExit) as error:
        service.main()
    assert error.value.code == 0
    help_text = capsys.readouterr().out
    assert '--now' not in help_text and '--initialize-paper' not in help_text
    assert '--recover' not in help_text and '--source-revision' in help_text
