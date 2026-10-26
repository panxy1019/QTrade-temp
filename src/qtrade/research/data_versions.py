"""Immutable Qlib data admission and transactional research-only version heads."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.persistence.sqlite import _claim_single_process_owner

SCHEMA = 'qtrade.qlib-data-version.v1'
REQUIRED_FILES = {
    'dataset.json', 'preparation-request.json', 'provider-reload.json',
    'provider/calendars/day.txt', 'provider/calendars/day_future.txt',
    'provider/instruments/all.txt', 'provider/instruments/workflow_supported.txt',
}


def file_hash(path: Path) -> str:
    digest = sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def content_id(files: dict[str, str], bindings: dict[str, Any]) -> str:
    return str(signed({'files': files, 'bindings': bindings})['evidence_sha256'])


def verify_version(root: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads((root/'version.json').read_bytes())
    if (signed({k: v for k, v in value.items() if k != 'evidence_sha256'}) != value
            or any(value.get(k) != v for k, v in AUTHORITY.items())
            or value.get('schema_version') != SCHEMA or value.get('frequency') != 'day'
            or value.get('state') != 'RESEARCH_READY_QUALIFIED_SUBSET'
            or value.get('historical_pit_certified') is not False
            or value.get('external_execution_ready') is not False
            or value['version_id'] != content_id(value['files'], value['bindings'])
            or not set(value['files']) >= REQUIRED_FILES
            or not 7 <= len(value['files']) <= 50_000):
        raise ValueError('DATA_VERSION_MANIFEST_CONTRACT_INVALID')
    now = datetime.now(UTC)
    created = datetime.fromisoformat(value['created_at'])
    actual_end = str(date.fromisoformat(value['actual_end']))
    current, historical = value['current_symbols'], value['historical_symbols']
    if (created.tzinfo is None or created > now or actual_end > str(now.date())
            or not 1 <= len(current) <= len(historical) <= 6000
            or len(set(current)) != len(current) or len(set(historical)) != len(historical)
            or set(current)-set(historical)
            or any(re.fullmatch(r'\d{6}', s) is None for s in historical)
            or set(value['blocked_symbols']) != set(historical)-set(current)):
        raise ValueError('DATA_VERSION_CLOCK_OR_SCOPE_INVALID')
    boundaries = value['calendar_boundaries']
    if (not 1 <= len(boundaries) <= 3 or boundaries != sorted(set(boundaries))
            or any(str(date.fromisoformat(d)) <= actual_end for d in boundaries)):
        raise ValueError('DATA_VERSION_BOUNDARIES_MUST_NOT_BE_PRICE_DATES')
    for name, digest in value['files'].items():
        path = root/name
        if (not isinstance(name, str) or Path(name).is_absolute() or path.is_symlink()
                or not path.resolve().is_relative_to(root.resolve()) or not path.is_file()
                or re.fullmatch(r'[a-f0-9]{64}', digest) is None or file_hash(path) != digest):
            raise ValueError('DATA_VERSION_FILE_IDENTITY_MISMATCH')
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()
              and p != root/'version.json'}
    if actual != set(value['files']):
        raise ValueError('DATA_VERSION_UNBOUND_FILE')
    dataset = json.loads((root/'dataset.json').read_bytes())
    reload = json.loads((root/'provider-reload.json').read_bytes())
    for document in (dataset, reload):
        if (signed({k: v for k, v in document.items() if k != 'evidence_sha256'}) != document
                or any(document.get(k) != v for k, v in AUTHORITY.items())):
            raise ValueError('DATA_VERSION_EVIDENCE_HASH_INVALID')
    provider_id = content_id({k: v for k, v in value['files'].items() if k.startswith('provider/')},
                             {})
    if (dataset['end'] != actual_end or {r['symbol'] for r in dataset['stocks']} != set(historical)
            or dataset['accepted_symbols'] != len(historical)
            or dataset['incremental']['current_symbols'] != current
            or any(r['end'] != actual_end if r['symbol'] in current else r['end'] >= actual_end
                   for r in dataset['stocks'])
            or reload.get('schema_version') != 'qtrade.qlib-provider-reload.v1'
            or reload.get('dataset_sha256') != value['files']['dataset.json']
            or reload.get('provider_sha256') != provider_id
            or reload.get('checked_current_symbols') != len(current)
            or reload.get('actual_end') != actual_end
            or any(reload.get(k) is not True for k in (
                'checks_passed', 'placeholder_all_missing', 'unknown_current_prices_missing',
            ))):
        raise ValueError('DATA_VERSION_PROVIDER_RELOAD_BINDING_INVALID')
    calendar = (root/'provider/calendars/day.txt').read_text().splitlines()
    future = (root/'provider/calendars/day_future.txt').read_text().splitlines()
    if (calendar != sorted(set(calendar)) or future != calendar or actual_end not in calendar
            or [d for d in calendar if d > actual_end] != boundaries):
        raise ValueError('DATA_VERSION_REAL_AND_PLACEHOLDER_CALENDAR_DRIFT')
    return value


class DataVersionCatalog:
    def __init__(self, database: Path, archive_root: Path):
        if os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
            raise RuntimeError('LIVE_TRADING_MUST_REMAIN_DISABLED')
        self.database, self.archive_root = database.resolve(), archive_root.resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS process_owner (
                    singleton INTEGER PRIMARY KEY, process_id INTEGER, owner_token TEXT,
                    claimed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS data_versions (
                    version_id TEXT PRIMARY KEY, manifest_sha256 TEXT, actual_end TEXT,
                    admitted_at TEXT
                );
                CREATE TABLE IF NOT EXISTS data_heads (
                    frequency TEXT PRIMARY KEY, version_id TEXT
                );
                CREATE TABLE IF NOT EXISTS data_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE,
                    version_id TEXT, kind TEXT, payload TEXT
                );
            ''')
        _claim_single_process_owner(self.database)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.database, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('PRAGMA synchronous=FULL')
            c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def root(self, version_id: str) -> Path:
        if re.fullmatch(r'[a-f0-9]{64}', version_id) is None:
            raise ValueError('DATA_VERSION_ID_INVALID')
        root = self.archive_root/'versions'/version_id
        if root.is_symlink() or root.resolve().parent != (self.archive_root/'versions').resolve():
            raise ValueError('DATA_VERSION_ARCHIVE_PATH_ESCAPE')
        return root

    def admit(self, version_id: str, *, now: datetime) -> dict[str, Any]:
        if now.tzinfo is None:
            raise ValueError('DATA_VERSION_ADMISSION_CLOCK_REQUIRED')
        value = verify_version(self.root(version_id))
        if value['version_id'] != version_id:
            raise ValueError('DATA_VERSION_DIRECTORY_IDENTITY_MISMATCH')
        with self.connect() as c:
            prior = c.execute('SELECT * FROM data_versions WHERE version_id=?',
                              (version_id,)).fetchone()
            if prior:
                if prior['manifest_sha256'] != value['evidence_sha256']:
                    raise ValueError('DATA_VERSION_IMMUTABLE_CONFLICT')
                return value
            head = c.execute(
                'SELECT v.actual_end FROM data_heads h JOIN data_versions v '
                'ON h.version_id=v.version_id WHERE h.frequency=?', ('day',),
            ).fetchone()
            if head and value['actual_end'] < head['actual_end']:
                raise ValueError('DATA_VERSION_REWIND_REQUIRES_EXPLICIT_ROLLBACK')
            event = signed({**AUTHORITY, 'kind': 'QLIB_DATA_READY', 'version_id': version_id,
                            'manifest_sha256': value['evidence_sha256'],
                            'actual_end': value['actual_end'], 'admitted_at': now.isoformat(),
                            'scope': 'QUALIFIED_RESEARCH_SUBSET_NOT_FULL_PIT_OR_TRADE_READINESS'})
            c.execute('INSERT INTO data_versions VALUES(?,?,?,?)',
                      (version_id, value['evidence_sha256'], value['actual_end'], now.isoformat()))
            c.execute('INSERT INTO data_events(event_id,version_id,kind,payload) VALUES(?,?,?,?)',
                      (event['evidence_sha256'], version_id, 'QLIB_DATA_READY', json.dumps(event)))
            c.execute('INSERT INTO data_heads VALUES(?,?) ON CONFLICT(frequency) '
                      'DO UPDATE SET version_id=excluded.version_id', ('day', version_id))
        return value

    def head(self) -> dict[str, Any] | None:
        with self.connect() as c:
            row = c.execute('SELECT v.* FROM data_heads h JOIN data_versions v '
                            'ON h.version_id=v.version_id WHERE h.frequency=?', ('day',)).fetchone()
        if row is None:
            return None
        value = verify_version(self.root(row['version_id']))
        if value['evidence_sha256'] != row['manifest_sha256']:
            raise ValueError('DATA_VERSION_HEAD_MANIFEST_DRIFT')
        return value

    def events(self) -> list[dict[str, Any]]:
        with self.connect() as c:
            return [json.loads(r['payload']) for r in c.execute(
                'SELECT payload FROM data_events ORDER BY sequence')]

    def rollback(self, version_id: str, *, expected_head: str, actor: str,
                 now: datetime) -> None:
        if (not re.fullmatch(r'[A-Za-z0-9._-]{1,60}', actor) or now.tzinfo is None
                or version_id == expected_head):
            raise ValueError('DATA_ROLLBACK_EXPLICIT_OPERATOR_REQUIRED')
        value = verify_version(self.root(version_id))
        with self.connect() as c:
            current = c.execute('SELECT version_id FROM data_heads WHERE frequency=?',
                                ('day',)).fetchone()
            known = c.execute('SELECT manifest_sha256 FROM data_versions WHERE version_id=?',
                              (version_id,)).fetchone()
            if (current is None or current['version_id'] != expected_head or known is None
                    or known['manifest_sha256'] != value['evidence_sha256']):
                raise ValueError('DATA_ROLLBACK_HEAD_OR_VERSION_CHANGED')
            event = signed({**AUTHORITY, 'kind': 'QLIB_DATA_ROLLBACK', 'version_id': version_id,
                            'previous_head': expected_head, 'actor': actor,
                            'created_at': now.isoformat()})
            c.execute('UPDATE data_heads SET version_id=? WHERE frequency=?', (version_id, 'day'))
            c.execute('INSERT INTO data_events(event_id,version_id,kind,payload) VALUES(?,?,?,?)',
                      (event['evidence_sha256'], version_id, 'QLIB_DATA_ROLLBACK',
                       json.dumps(event)))
