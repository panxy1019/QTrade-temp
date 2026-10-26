"""Allowlisted rollback commands and owner-free bounded version catalogue reads."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.data_versions import SCHEMA, DataVersionCatalog


def command(value: dict[str, Any]) -> dict[str, Any]:
    """Validate inner payload; authenticated queue envelopes are checked by the ingress."""
    if (not isinstance(value, dict)
            or set(value) != {'request_id', 'version_id', 'expected_head', 'expected_event_id',
                              'actor', 'reason'}
            or any(not isinstance(v, str) for v in value.values())
            or re.fullmatch('[a-f0-9]{32}', value['request_id']) is None
            or any(re.fullmatch('[a-f0-9]{64}', value[k]) is None
                   for k in ('version_id', 'expected_head', 'expected_event_id'))
            or re.fullmatch('[A-Za-z0-9._-]{1,60}', value['actor']) is None
            or value['reason'] not in {'DATA_QUALITY_REVIEW', 'PROVIDER_REGRESSION',
                                      'RESEARCH_REPRODUCTION'}
            or value['version_id'] == value['expected_head']):
        raise ValueError('DATA_ROLLBACK_COMMAND_INVALID')
    return dict(value)


def apply(catalog: DataVersionCatalog, value: dict[str, Any], *, now: datetime
          ) -> dict[str, Any]:
    value = command(value)
    return catalog.rollback(value['version_id'], expected_head=value['expected_head'],
                            actor=value['actor'], now=now, request=value)


def read_catalog(database: Path, archive_root: Path, *, limit: int = 20,
                 before_sequence: int | None = None) -> dict[str, Any]:
    """Read one bounded snapshot without claiming the single writer's process lease.

    Manifest digests are checked; costly Provider byte verification occurs at mutation.
    Head event identity must bind freshness, including an A -> B -> A transition.
    """
    if (type(limit) is not int or not 1 <= limit <= 100
            or (before_sequence is not None
                and (type(before_sequence) is not int or before_sequence < 1))):
        raise ValueError('DATA_CATALOG_PAGE_INVALID')
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True) as c:
        c.row_factory = sqlite3.Row
        c.execute('BEGIN')
        head = c.execute("SELECT version_id FROM data_heads WHERE frequency='day'").fetchone()
        epoch = c.execute('SELECT event_id,sequence,version_id,payload FROM data_events '
                          'ORDER BY sequence DESC '
                          'LIMIT 1').fetchone()
        versions = c.execute('SELECT * FROM data_versions ORDER BY admitted_at DESC, '
                             'version_id DESC LIMIT ?', (limit,)).fetchall()
        events = c.execute('SELECT sequence,payload FROM data_events WHERE sequence < ? '
                           'ORDER BY sequence DESC LIMIT ?',
                           (before_sequence or 9223372036854775807, limit)).fetchall()
        total = c.execute('SELECT COUNT(*) FROM data_versions').fetchone()[0]
        has_receipts = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                "AND name='data_rollback_commands'").fetchone()
        receipts = ([json.loads(r[0]) for r in c.execute(
            'SELECT receipt FROM data_rollback_commands ORDER BY rowid DESC LIMIT 100')]
            if has_receipts else [])
    if bool(head) != bool(epoch):
        raise ValueError('DATA_CATALOG_HEAD_EVENT_MISSING')
    if epoch:
        latest = json.loads(epoch['payload'])
        if (head is None or epoch['version_id'] != head['version_id']
                or latest.get('version_id') != head['version_id']
                or latest.get('evidence_sha256') != epoch['event_id']
                or latest.get('kind') not in {'QLIB_DATA_READY', 'QLIB_DATA_ROLLBACK'}
                or signed({k: v for k, v in latest.items() if k != 'evidence_sha256'}) != latest
                or any(latest.get(k) != v for k, v in AUTHORITY.items())):
            raise ValueError('DATA_CATALOG_HEAD_EVENT_CHANGED')
    rows = []
    for row in versions:
        identity = row['version_id']
        if re.fullmatch('[a-f0-9]{64}', identity) is None:
            raise ValueError('DATA_CATALOG_IDENTITY_INVALID')
        root = archive_root/'versions'/identity
        path = root/'version.json'
        if (root.is_symlink() or path.is_symlink()
                or not path.resolve().is_relative_to(archive_root.resolve())
                or path.stat().st_size > 16*1024*1024):
            raise ValueError('DATA_CATALOG_MANIFEST_PATH_INVALID')
        value = json.loads(path.read_bytes())
        if (signed({k: v for k, v in value.items() if k != 'evidence_sha256'}) != value
                or value.get('evidence_sha256') != row['manifest_sha256']
                or value.get('version_id') != identity
                or value.get('actual_end') != row['actual_end']
                or value.get('schema_version') != SCHEMA
                or any(value.get(k) != v for k, v in AUTHORITY.items())):
            raise ValueError('DATA_CATALOG_MANIFEST_CHANGED')
        rows.append({'version_id': identity, 'actual_end': row['actual_end'],
            'admitted_at': row['admitted_at'], 'manifest_sha256': row['manifest_sha256'],
            'is_head': bool(head and head['version_id'] == identity),
            'current_symbols': len(value['current_symbols']),
            'historical_symbols': len(value['historical_symbols']),
            'blocked_symbols': len(value['blocked_symbols']),
            'historical_pit_certified': False, 'external_execution_ready': False,
            'verification': 'MANIFEST_IDENTITY_ONLY_FULL_BYTES_CHECKED_BEFORE_ROLLBACK'})
    history = []
    for row in events:
        event = json.loads(row['payload'])
        if (signed({k: v for k, v in event.items() if k != 'evidence_sha256'}) != event
                or any(event.get(k) != v for k, v in AUTHORITY.items())):
            raise ValueError('DATA_CATALOG_EVENT_CHANGED')
        history.append({'sequence': row['sequence'], **event})
    result = signed({**AUTHORITY, 'schema_version': 'qtrade.qlib-data-catalog.v1',
        'head_version_id': head['version_id'] if head else None,
        'head_event_id': epoch['event_id'] if epoch else None,
        'head_sequence': epoch['sequence'] if epoch else None,
        'versions': rows, 'total_versions': total, 'versions_truncated': total > limit,
        'history': history, 'next_before_sequence': events[-1]['sequence'] if events else None,
        'rollback_requires_authenticated_command': True, 'receipts': receipts})
    return validate_public_catalog(result)


def validate_public_catalog(value: dict[str, Any]) -> dict[str, Any]:
    """Validate the bounded public transport; it grants no execution authority."""
    def identity(item: Any) -> bool:
        return isinstance(item, str) and re.fullmatch('[a-f0-9]{64}', item) is not None

    def evidence(item: Any) -> bool:
        return bool(isinstance(item, dict)
            and all(item.get(k) == v for k, v in AUTHORITY.items())
            and signed({k: v for k, v in item.items() if k != 'evidence_sha256'}) == item)

    fields = {*AUTHORITY, 'evidence_sha256', 'schema_version', 'head_version_id',
        'head_event_id', 'head_sequence', 'versions', 'total_versions', 'versions_truncated',
        'history', 'next_before_sequence', 'rollback_requires_authenticated_command', 'receipts'}
    if (not evidence(value) or set(value) != fields
            or len(json.dumps(value)) > 1024*1024
            or value['schema_version'] != 'qtrade.qlib-data-catalog.v1'
            or value['rollback_requires_authenticated_command'] is not True
            or type(value['total_versions']) is not int or value['total_versions'] < 0
            or any(not isinstance(value[k], list) or len(value[k]) > 100
                   for k in ('versions', 'history', 'receipts'))
            or value['versions_truncated'] is not (value['total_versions'] > len(value['versions']))
            or value['total_versions'] < len(value['versions'])):
        raise ValueError('DATA_PUBLIC_CATALOG_INVALID')
    head = value['head_version_id']
    if ((head is None and (value['head_event_id'] is not None or value['head_sequence'] is not None
                          or value['total_versions'] != 0))
            or (head is not None and (not identity(head) or not identity(value['head_event_id'])
                or type(value['head_sequence']) is not int or value['head_sequence'] < 1))):
        raise ValueError('DATA_PUBLIC_CATALOG_HEAD_INVALID')
    seen = set()
    row_fields = {'version_id', 'actual_end', 'admitted_at', 'manifest_sha256', 'is_head',
                  'current_symbols', 'historical_symbols', 'blocked_symbols',
                  'historical_pit_certified', 'external_execution_ready', 'verification'}
    for row in value['versions']:
        if (not isinstance(row, dict) or set(row) != row_fields
                or not identity(row['version_id']) or row['version_id'] in seen
                or not identity(row['manifest_sha256'])
                or row['is_head'] is not (row['version_id'] == head)
                or any(type(row[k]) is not int for k in (
                    'current_symbols', 'historical_symbols', 'blocked_symbols'))
                or not 1 <= row['current_symbols'] <= row['historical_symbols'] <= 6000
                or row['blocked_symbols'] != row['historical_symbols']-row['current_symbols']
                or row['historical_pit_certified'] is not False
                or row['external_execution_ready'] is not False
                or row['verification'] !=
                    'MANIFEST_IDENTITY_ONLY_FULL_BYTES_CHECKED_BEFORE_ROLLBACK'):
            raise ValueError('DATA_PUBLIC_CATALOG_VERSION_INVALID')
        if (str(date.fromisoformat(row['actual_end'])) != row['actual_end']
                or datetime.fromisoformat(row['admitted_at']).tzinfo is None):
            raise ValueError('DATA_PUBLIC_CATALOG_CLOCK_INVALID')
        seen.add(row['version_id'])
    previous = None
    for entry in value['history']:
        if not isinstance(entry, dict):
            raise ValueError('DATA_PUBLIC_CATALOG_HISTORY_INVALID')
        event = {k: v for k, v in entry.items() if k != 'sequence'}
        sequence = entry.get('sequence')
        if (not evidence(event) or not identity(event.get('version_id'))
                or event.get('kind') not in {'QLIB_DATA_READY', 'QLIB_DATA_ROLLBACK'}
                or type(sequence) is not int or sequence < 1
                or (previous is not None and sequence >= previous)
                or value['head_sequence'] is None or sequence > value['head_sequence']):
            raise ValueError('DATA_PUBLIC_CATALOG_HISTORY_INVALID')
        if sequence == value['head_sequence'] and (
                event['version_id'] != head or event['evidence_sha256'] != value['head_event_id']):
            raise ValueError('DATA_PUBLIC_CATALOG_HISTORY_HEAD_DRIFT')
        base_fields = {*AUTHORITY, 'evidence_sha256', 'kind', 'version_id'}
        if event['kind'] == 'QLIB_DATA_READY':
            ready_fields = base_fields | {'manifest_sha256', 'actual_end', 'admitted_at', 'scope'}
            if (set(event) != ready_fields
                    or not identity(event['manifest_sha256'])
                    or str(date.fromisoformat(event['actual_end'])) != event['actual_end']
                    or datetime.fromisoformat(event['admitted_at']).tzinfo is None
                    or event['scope'] !=
                        'QUALIFIED_RESEARCH_SUBSET_NOT_FULL_PIT_OR_TRADE_READINESS'):
                raise ValueError('DATA_PUBLIC_CATALOG_HISTORY_INVALID')
        else:
            legacy_fields = base_fields | {'previous_head', 'actor', 'created_at'}
            if (set(event) not in (legacy_fields, legacy_fields | {'request_id', 'reason'})
                    or not identity(event['previous_head'])
                    or not isinstance(event['actor'], str)
                    or re.fullmatch('[A-Za-z0-9._-]{1,60}', event['actor']) is None
                    or datetime.fromisoformat(event['created_at']).tzinfo is None
                    or ('request_id' in event and event['request_id'] is not None
                        and (not isinstance(event['request_id'], str)
                             or re.fullmatch('[a-f0-9]{32}', event['request_id']) is None))
                    or ('reason' in event and event['reason'] not in {
                        'OPERATOR_ROLLBACK', 'DATA_QUALITY_REVIEW', 'PROVIDER_REGRESSION',
                        'RESEARCH_REPRODUCTION'})):
                raise ValueError('DATA_PUBLIC_CATALOG_HISTORY_INVALID')
        previous = sequence
    if value['next_before_sequence'] != previous:
        raise ValueError('DATA_PUBLIC_CATALOG_CURSOR_INVALID')
    requests = set()
    for receipt in value['receipts']:
        if (not evidence(receipt) or receipt.get('state') not in {'APPLIED', 'REJECTED'}
                or not isinstance(receipt.get('request_id'), str)
                or re.fullmatch('[a-f0-9]{32}', receipt['request_id']) is None
                or receipt['request_id'] in requests
                or not identity(receipt.get('command_sha256'))
                or not identity(receipt.get('version_id'))
                or datetime.fromisoformat(receipt['at']).tzinfo is None):
            raise ValueError('DATA_PUBLIC_CATALOG_RECEIPT_INVALID')
        if receipt['state'] == 'APPLIED':
            expected = {*AUTHORITY, 'evidence_sha256', 'state', 'version_id', 'previous_head',
                'event_id', 'request_id', 'command_sha256', 'at',
                'predictions_require_head_event_revalidation'}
            if (set(receipt) != expected or not identity(receipt['previous_head'])
                    or not identity(receipt['event_id'])
                    or receipt['predictions_require_head_event_revalidation'] is not True):
                raise ValueError('DATA_PUBLIC_CATALOG_RECEIPT_INVALID')
        else:
            expected = {*AUTHORITY, 'evidence_sha256', 'state', 'request_id', 'command_sha256',
                        'version_id', 'expected_head', 'error_code', 'at'}
            if (set(receipt) != expected or not identity(receipt['expected_head'])
                    or receipt['error_code'] not in {'DATA_ROLLBACK_HEAD_EVENT_CHANGED',
                        'DATA_ROLLBACK_HEAD_OR_VERSION_CHANGED',
                        'DATA_ROLLBACK_TARGET_VERIFICATION_FAILED',
                        'DATA_VERSION_DIRECTORY_IDENTITY_MISMATCH'}):
                raise ValueError('DATA_PUBLIC_CATALOG_RECEIPT_INVALID')
        requests.add(receipt['request_id'])
    return value
