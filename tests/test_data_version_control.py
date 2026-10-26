import json
import sqlite3
from datetime import UTC, datetime

import pytest
from test_data_versions import version

from qtrade.monitoring.data_freshness import signed
from qtrade.research.data_version_control import (
    apply,
    command,
    read_catalog,
    validate_public_catalog,
)
from qtrade.research.data_versions import DataVersionCatalog


def setup(tmp_path):
    archive = tmp_path/'archive'
    first, root, _ = version(archive)
    second, _, _ = version(archive, end='2020-01-06', boundary='2020-01-07', nonce='b')
    db = tmp_path/'local.sqlite'
    catalog = DataVersionCatalog(db, archive)
    for identity in (first, second):
        catalog.admit(identity, now=datetime.now(UTC))
    request = {'request_id': 'a'*32, 'version_id': first, 'expected_head': second,
               'expected_event_id': catalog.events()[-1]['evidence_sha256'],
               'actor': 'operator', 'reason': 'DATA_QUALITY_REVIEW'}
    return db, archive, catalog, request, root


def test_catalog_is_owner_free_bounded_and_epoch_changes_on_rollback(tmp_path, monkeypatch):
    db, archive, catalog, request, _ = setup(tmp_path)
    def no_owner(*args):
        raise AssertionError('Read must not claim ownership')
    monkeypatch.setattr('qtrade.research.data_versions._claim_single_process_owner', no_owner)
    before = read_catalog(db, archive, limit=1)
    assert len(before['versions']) == len(before['history']) == 1
    assert before['total_versions'] == 2 and before['versions_truncated']
    assert before['head_version_id'] == request['expected_head']
    page = read_catalog(db, archive, before_sequence=before['next_before_sequence'])
    assert len(page['history']) == 1
    receipt = apply(catalog, request, now=datetime.now(UTC))
    after = read_catalog(db, archive)
    assert after['head_version_id'] == request['version_id']
    assert before['head_event_id'] != after['head_event_id'] == receipt['event_id']


def test_rollback_retry_is_idempotent_even_after_other_head_transition(tmp_path):
    db, archive, catalog, request, _ = setup(tmp_path)
    receipt = apply(catalog, request, now=datetime.now(UTC))
    catalog.rollback(request['expected_head'], expected_head=request['version_id'],
                     actor='operator', now=datetime.now(UTC))
    assert apply(catalog, request, now=datetime.now(UTC)) == receipt
    assert read_catalog(db, archive)['head_version_id'] == request['expected_head']
    with pytest.raises(ValueError, match='REQUEST_ID_CONFLICT'):
        apply(catalog, {**request, 'reason': 'PROVIDER_REGRESSION'}, now=datetime.now(UTC))


def test_rollback_receipt_and_event_commit_together(tmp_path):
    db, archive, catalog, request, _ = setup(tmp_path)
    with catalog.connect() as c:
        c.execute("CREATE TRIGGER refuse_receipt BEFORE INSERT ON data_rollback_commands "
                  "BEGIN SELECT RAISE(ABORT, 'fault'); END")
    with pytest.raises(sqlite3.IntegrityError):
        apply(catalog, request, now=datetime.now(UTC))
    view = read_catalog(db, archive)
    assert view['head_version_id'] == request['expected_head']
    assert len(view['history']) == 2


def test_changed_provider_bytes_and_stale_cas_cannot_change_head(tmp_path):
    db, archive, catalog, request, root = setup(tmp_path)
    rejected = apply(catalog, {**request, 'expected_head': 'f'*64,
                              'request_id': 'b'*32}, now=datetime.now(UTC))
    assert rejected['error_code'] == 'DATA_ROLLBACK_HEAD_OR_VERSION_CHANGED'
    (root/'provider/calendars/day.txt').write_text('corrupt')
    rejected = apply(catalog, request, now=datetime.now(UTC))
    assert rejected['state'] == 'REJECTED'
    assert rejected['error_code'] == 'DATA_ROLLBACK_TARGET_VERIFICATION_FAILED'
    assert apply(catalog, request, now=datetime.now(UTC)) == rejected
    assert read_catalog(db, archive)['head_version_id'] == request['expected_head']


def test_manifest_metadata_corruption_fails_closed(tmp_path):
    db, archive, _, _, root = setup(tmp_path)
    path = root/'version.json'
    value = json.loads(path.read_bytes())
    value['actual_end'] = '2020-01-01'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='MANIFEST_CHANGED'):
        read_catalog(db, archive)


def test_same_version_after_roundtrip_does_not_reuse_approval_epoch(tmp_path):
    db, archive, catalog, request, _ = setup(tmp_path)
    catalog.rollback(request['version_id'], expected_head=request['expected_head'],
                     actor='operator', now=datetime.now(UTC))
    catalog.rollback(request['expected_head'], expected_head=request['version_id'],
                     actor='operator', now=datetime.now(UTC))
    receipt = apply(catalog, request, now=datetime.now(UTC))
    assert receipt['state'] == 'REJECTED'
    assert receipt['error_code'] == 'DATA_ROLLBACK_HEAD_EVENT_CHANGED'
    assert read_catalog(db, archive)['head_version_id'] == request['expected_head']


def test_head_event_must_describe_current_head(tmp_path):
    db, archive, catalog, request, _ = setup(tmp_path)
    with catalog.connect() as c:
        c.execute('UPDATE data_events SET version_id=? WHERE sequence=2',
                  (request['version_id'],))
    with pytest.raises(ValueError, match='HEAD_EVENT_CHANGED'):
        read_catalog(db, archive)
    assert apply(catalog, request, now=datetime.now(UTC))['state'] == 'REJECTED'


def test_receipts_are_published_and_old_schema_is_readable(tmp_path):
    db, archive, catalog, request, _ = setup(tmp_path)
    receipt = apply(catalog, request, now=datetime.now(UTC))
    assert read_catalog(db, archive)['receipts'] == [receipt]
    with catalog.connect() as c:
        c.execute('DROP TABLE data_rollback_commands')
    assert read_catalog(db, archive)['receipts'] == []


def test_cli_receipt_replay_preserves_existing_bytes(tmp_path):
    from scripts.data_version_control import write_receipt
    path = tmp_path/'receipt.json'
    path.write_text('{"state":"APPLIED"}')
    original = path.read_bytes()
    write_receipt(path, {'state': 'APPLIED'})
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match='OUTPUT_RECEIPT_CONFLICT'):
        write_receipt(path, {'state': 'REJECTED'})
    assert path.read_bytes() == original


@pytest.mark.parametrize('change', ['authority', 'head', 'version', 'history', 'receipt', 'extra'])
def test_public_transport_cannot_rehash_invalid_contract(tmp_path, change):
    db, archive, catalog, request, _ = setup(tmp_path)
    apply(catalog, request, now=datetime.now(UTC))
    value = read_catalog(db, archive)
    if change == 'authority':
        value['live_trading_enabled'] = True
    elif change == 'head':
        value['head_event_id'] = 'f'*64
    elif change == 'version':
        value['versions'][0]['current_symbols'] = 7000
    elif change == 'history':
        value['history'][0]['sequence'] = 0
    elif change == 'receipt':
        value['receipts'][0]['state'] = 'EXECUTED'
    else:
        value['private_path'] = '/private'
    value.pop('evidence_sha256')
    with pytest.raises(ValueError):
        validate_public_catalog(signed(value))


@pytest.mark.parametrize('patch', [
    {'actor': 'root; shell'}, {'reason': 'anything'}, {'request_id': '../escape'},
    {'version_id': 'a'}, {'extra': True}, {'actor': 1},
])
def test_command_rejects_nonallowlisted_fields(tmp_path, patch):
    _, _, _, request, _ = setup(tmp_path)
    with pytest.raises(ValueError, match='COMMAND_INVALID'):
        command({**request, **patch})


@pytest.mark.parametrize('limit', [0, 101, True, '20'])
def test_catalog_rejects_unbounded_pages(tmp_path, limit):
    with pytest.raises(ValueError, match='PAGE_INVALID'):
        read_catalog(tmp_path/'missing', tmp_path, limit=limit)
