import json
import sqlite3
from datetime import UTC, datetime

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.data_versions import (
    SCHEMA,
    DataVersionCatalog,
    content_id,
    file_hash,
    verify_version,
)


def version(archive, *, end='2020-01-02', boundary='2020-01-03', nonce='a'):
    stage = archive/('partial-'+nonce)
    (stage/'provider/calendars').mkdir(parents=True)
    (stage/'provider/instruments').mkdir()
    for name in ('day.txt', 'day_future.txt'):
        (stage/'provider/calendars'/name).write_text(end+'\n'+boundary+'\n')
    for name in ('all.txt', 'workflow_supported.txt'):
        (stage/'provider/instruments'/name).write_text('SH600036\t2017-01-02\t'+end+'\n')
    dataset = signed({**AUTHORITY, 'end': end, 'accepted_symbols': 2,
                      'stocks': [{'symbol': '600036', 'end': end},
                                 {'symbol': '000001', 'end': '2019-12-31'}],
                      'incremental': {'current_symbols': ['600036']}})
    (stage/'dataset.json').write_text(json.dumps(dataset))
    (stage/'preparation-request.json').write_text(json.dumps(signed({'nonce': nonce})))
    files = {p.relative_to(stage).as_posix(): file_hash(p)
             for p in stage.rglob('*') if p.is_file()}
    reload = signed({**AUTHORITY, 'schema_version': 'qtrade.qlib-provider-reload.v1',
                     'dataset_sha256': files['dataset.json'], 'checked_current_symbols': 1,
                     'provider_sha256': content_id({k: v for k, v in files.items()
                                                   if k.startswith('provider/')}, {}),
                     'actual_end': end, 'checks_passed': True, 'placeholder_all_missing': True,
                     'unknown_current_prices_missing': True})
    (stage/'provider-reload.json').write_text(json.dumps(reload))
    files['provider-reload.json'] = file_hash(stage/'provider-reload.json')
    bindings = {'nonce': nonce}
    identity = content_id(files, bindings)
    value = signed({**AUTHORITY, 'schema_version': SCHEMA, 'version_id': identity,
                    'state': 'RESEARCH_READY_QUALIFIED_SUBSET', 'frequency': 'day',
                    'historical_pit_certified': False, 'external_execution_ready': False,
                    'created_at': datetime.now(UTC).isoformat(), 'actual_end': end,
                    'current_symbols': ['600036'], 'historical_symbols': ['000001', '600036'],
                    'blocked_symbols': ['000001'], 'calendar_boundaries': [boundary],
                    'files': files, 'bindings': bindings})
    (stage/'version.json').write_text(json.dumps(value))
    target = archive/'versions'/identity
    target.parent.mkdir(exist_ok=True)
    stage.rename(target)
    return identity, target, value


def test_admission_is_atomic_idempotent_and_head_is_revalidated_after_restart(tmp_path):
    identity, root, value = version(tmp_path/'archive')
    cat = DataVersionCatalog(tmp_path/'local.sqlite', tmp_path/'archive')
    assert cat.head() is None
    assert cat.admit(identity, now=datetime.now(UTC)) == value
    cat.admit(identity, now=datetime.now(UTC))
    restored = DataVersionCatalog(tmp_path/'local.sqlite', tmp_path/'archive')
    assert restored.head() == value and len(restored.events()) == 1
    (root/'dataset.json').write_text('corrupt')
    with pytest.raises(ValueError, match='FILE_IDENTITY'):
        restored.head()


def test_event_failure_rolls_back_version_and_head_then_replay_commits_once(tmp_path):
    archive = tmp_path/'archive'
    first, _, original = version(archive)
    second, _, value = version(archive, end='2020-01-06', boundary='2020-01-07', nonce='b')
    cat = DataVersionCatalog(tmp_path/'local.sqlite', archive)
    cat.admit(first, now=datetime.now(UTC))
    with cat.connect() as c:
        c.execute("CREATE TRIGGER reject_event BEFORE INSERT ON data_events "
                  "BEGIN SELECT RAISE(ABORT,'controlled failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        cat.admit(second, now=datetime.now(UTC))
    assert cat.head() == original and len(cat.events()) == 1
    with cat.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM data_versions').fetchone()[0] == 1
        c.execute('DROP TRIGGER reject_event')
    cat.admit(second, now=datetime.now(UTC))
    assert cat.head() == value and len(cat.events()) == 2


def test_rollback_requires_admitted_version_expected_head_and_explicit_actor(tmp_path):
    archive = tmp_path/'archive'
    first, _, value = version(archive)
    second, _, _ = version(archive, end='2020-01-06', boundary='2020-01-07', nonce='b')
    cat = DataVersionCatalog(tmp_path/'local.sqlite', archive)
    cat.admit(first, now=datetime.now(UTC))
    cat.admit(second, now=datetime.now(UTC))
    with pytest.raises(ValueError, match='OPERATOR'):
        cat.rollback(first, expected_head=second, actor='', now=datetime.now(UTC))
    with pytest.raises(ValueError, match='HEAD'):
        cat.rollback(first, expected_head='a'*64, actor='operator', now=datetime.now(UTC))
    cat.rollback(first, expected_head=second, actor='operator', now=datetime.now(UTC))
    assert cat.head() == value
    assert cat.events()[-1]['kind'] == 'QLIB_DATA_ROLLBACK'
    assert cat.events()[-1]['external_order_transmission'] is False
    cat.admit(second, now=datetime.now(UTC))
    assert cat.head() == value  # Idempotent replay cannot undo a human rollback.


@pytest.mark.parametrize('change', ['authority', 'path', 'unbound', 'raw', 'reload', 'boundary'])
def test_inconsistent_versions_cannot_advance_catalog_even_with_outer_rehash(tmp_path, change):
    archive = tmp_path/'archive'
    identity, root, value = version(archive)
    if change == 'authority':
        value['live_trading_enabled'] = True
    elif change == 'path':
        value['files']['../escape'] = 'a'*64
    elif change == 'unbound':
        (root/'unbound.bin').write_bytes(b'unbound')
    elif change == 'raw':
        (root/'provider/calendars/day.txt').write_text('changed')
    elif change == 'reload':
        reload = json.loads((root/'provider-reload.json').read_text())
        reload['checked_current_symbols'] = 2
        (root/'provider-reload.json').write_text(json.dumps(signed({
            k: v for k, v in reload.items() if k != 'evidence_sha256'})))
        value['files']['provider-reload.json'] = file_hash(root/'provider-reload.json')
    else:
        value['calendar_boundaries'] = [value['actual_end']]
    value.pop('evidence_sha256')
    new_id = content_id(value['files'], value['bindings'])
    value['version_id'] = new_id
    if new_id != identity:
        destination = root.with_name(new_id)
        root.rename(destination)
        root, identity = destination, new_id
    (root/'version.json').write_text(json.dumps(signed(value)))
    cat = DataVersionCatalog(tmp_path/'local.sqlite', archive)
    with pytest.raises(ValueError):
        cat.admit(identity, now=datetime.now(UTC))
    assert cat.head() is None and cat.events() == []


def test_older_new_admission_requires_explicit_rollback_and_paths_are_bounded(tmp_path):
    archive = tmp_path/'archive'
    first, _, _ = version(archive)
    second, _, _ = version(archive, end='2020-01-06', boundary='2020-01-07', nonce='b')
    cat = DataVersionCatalog(tmp_path/'local.sqlite', archive)
    cat.admit(second, now=datetime.now(UTC))
    with pytest.raises(ValueError, match='REWIND'):
        cat.admit(first, now=datetime.now(UTC))
    with pytest.raises(ValueError, match='ID_INVALID'):
        cat.root('../escape')
    assert verify_version(cat.root(second))['version_id'] == second
