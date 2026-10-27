"""Explicit maintenance-window repair of recent prior-session Sina minute gaps."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from qtrade.api.unified_data import validate_publication
from qtrade.market_data.akshare import SHANGHAI
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.persistence.sqlite import _process_is_alive
from scripts.collect_unified_data import collect_batch, fetch
from scripts.run_history_backfill import priority_reason
from scripts.unified_queue import UnifiedCollectionQueue, clock
from scripts.unified_store import UnifiedBatchStore
from scripts.vendor_session import baostock_session

REPO = Path(__file__).resolve().parents[1]
TRAILING_ROWS = 240  # Three prior sessions + current-session suffix + one session of margin.
PROVIDER_CAP = 1970


def _read(database: Path) -> sqlite3.Connection:
    if database.is_symlink() or not database.is_file():
        raise ValueError('REPAIR_EXISTING_DATABASE_REQUIRED')
    connection = sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    return connection


def _days(calendar: Calendar, now: datetime) -> list[str]:
    local = now.astimezone(SHANGHAI).date()
    calendar.is_open(local)  # An expired calendar never authorizes repair.
    day = local-timedelta(days=1)
    result: list[str] = []
    while len(result) < 3 and day >= calendar.start:
        if calendar.is_open(day):
            result.append(str(day))
        day -= timedelta(days=1)
    return result


def _tasks(database: Path, universe: str, days: list[str], symbols: set[str]
           ) -> list[dict[str, Any]]:
    if len(days) > 3:
        raise ValueError('REPAIR_SESSION_SCOPE_BOUND')
    if not days:
        return []
    placeholders = ','.join('?' for _ in days)
    with closing(_read(database)) as connection:
        rows = [dict(row) for row in connection.execute(
            "SELECT * FROM collection_tasks WHERE universe_version=? AND frequency='5min' "
            f"AND trading_day IN ({placeholders})", (universe, *days))]
    result = []
    for row in rows:
        if row['trading_day'] not in days or row['symbol'] not in symbols:
            continue
        target = datetime.combine(date.fromisoformat(row['trading_day']), time(15), SHANGHAI)
        desired = datetime.fromisoformat(row['desired_end'])
        completed = datetime.fromisoformat(row['completed_end']) if row['completed_end'] else None
        if (desired.tzinfo is None or desired.astimezone(SHANGHAI).date() != target.date()
                or desired > target or completed is not None and (
                    completed.tzinfo is None
                    or completed.astimezone(SHANGHAI).date() != target.date()
                    or completed > desired)):
            raise ValueError('REPAIR_TASK_CLOCK_INVALID')
        if (row['state'] in ('PLANNED', 'FAILED', 'INTERRUPTED', 'SUCCEEDED')
                and row['active_batch'] is None and (row['completed_end'] is None
                or datetime.fromisoformat(row['completed_end']) < target)):
            result.append(row)
    return result


def _existing_migration(database: Path) -> None:
    """The repair command must never trigger the queue's global legacy migration."""
    with closing(_read(database)) as connection:
        columns = {row['name']: row for row in connection.execute(
            'PRAGMA table_info(collection_tasks)')}
        column = columns.get('consecutive_failures')
        metadata = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                      "AND name='collection_metadata'").fetchone()
        if (column is None or column['type'].upper() != 'INTEGER' or not column['notnull']
                or metadata is None or connection.execute(
                    "SELECT 1 FROM collection_metadata WHERE key='failure_streak_v1'"
                ).fetchone() is None):
            raise ValueError('REPAIR_EXISTING_FAILURE_MIGRATION_REQUIRED')


def _owner_available(database: Path) -> None:
    with closing(_read(database)) as connection:
        owner = connection.execute(
            'SELECT process_id FROM process_owner WHERE singleton=1').fetchone()
        running = connection.execute(
            "SELECT 1 FROM collection_tasks WHERE state='RUNNING' LIMIT 1").fetchone()
    if owner and owner[0] != os.getpid() and _process_is_alive(owner[0]):
        raise ValueError('REPAIR_CURRENT_COLLECTOR_OWNER_ALIVE')
    if running:
        raise ValueError('REPAIR_UNRESOLVED_RUNNING_TASKS')


def repair(base: Path, database: Path, *, revision: str, apply: bool = False,
           max_batches: int = 2, now: datetime | None = None, source: Any = fetch,
           _lease: Any = baostock_session, _priority: Any = priority_reason
           ) -> dict[str, Any]:
    """Plan is read-only. CLI exposes no source, clock, priority or lease overrides."""
    if (os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false'
            or re.fullmatch('[a-f0-9]{40}', revision) is None
            or not 1 <= max_batches <= 3 or not 0 < TRAILING_ROWS <= PROVIDER_CAP):
        raise ValueError('REPAIR_SAFETY_OR_RESOURCE_BOUND')
    def current() -> datetime:
        return now if now is not None else datetime.now(UTC)

    started = current()
    if started.tzinfo is None:
        raise ValueError('REPAIR_CLOCK_REQUIRES_TIMEZONE')
    publication_path = base/'unified-data/public/unified.json'
    raw_publication = publication_path.read_bytes()
    publication = validate_publication(json.loads(raw_publication))
    master = publication['master']
    symbols = {row['symbol'] for row in master['securities'] if row['in_target']}
    calendar = Calendar(REPO/'configs/observer_calendar_2026.json')
    days = _days(calendar, started)
    rows = _tasks(database, master['universe_version'], days, symbols)
    report: dict[str, Any] = {**AUTHORITY, 'schema_version': 'qtrade.incremental-gap-repair.v1',
        'state': 'PLAN', 'apply': apply, 'observed_at': started.isoformat(),
        'universe_version': master['universe_version'], 'previous_sessions': days,
        'code_revision': revision, 'master_sha256': master.get('evidence_sha256'),
        'calendar_sha256': signed(calendar.document)['evidence_sha256'],
        'candidate_count': len(rows), 'max_batches': max_batches,
        'trailing_rows': TRAILING_ROWS, 'batches': [], 'historical_pit_certified': False,
        'limitations': ['Maintenance window only; no current collector ownership bypass',
                        'Absent source grids remain unresolved; no inferred security status',
                        'No source substitution, history stitching or freshness promotion']}

    def summary(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{key: row[key] for key in ('symbol', 'trading_day', 'completed_end',
                                           'desired_end', 'state', 'next_attempt_at')}
                for row in values[:100]]

    report['candidates'] = summary(rows)
    if not apply:
        return signed(report)
    if os.environ.get('QTRADE_AKSHARE_NONCOMMERCIAL_ACK', '').lower() != 'true':
        raise ValueError('REPAIR_NONCOMMERCIAL_ACK_REQUIRED')

    def allowed() -> None:
        _existing_migration(database)
        if _priority(base, current()) or _days(calendar, current()) != days:
            raise ValueError('REPAIR_CURRENT_PRIORITY_NOT_IDLE')
        _owner_available(database)
        if publication_path.read_bytes() != raw_publication:
            raise ValueError('REPAIR_UNIVERSE_OR_PUBLICATION_CHANGED')
        if shutil.disk_usage(base).free < 100*1024**3:
            raise ValueError('REPAIR_STORAGE_RESERVE')

    def trailing_source(source_id: str, params: dict[str, str]) -> dict[str, Any]:
        if (source_id != 'sina_klines' or params['scale'] != '5'
                or params['ma'] != 'no' or not 1 <= int(params['datalen']) <= 96):
            raise ValueError('REPAIR_SOURCE_SCOPE_INVALID')
        result: dict[str, Any] = source(source_id, {**params, 'datalen': str(TRAILING_ROWS)})
        return result

    try:
        allowed()
        with _lease(blocking=False):
            allowed()
            queue = UnifiedCollectionQueue(database)  # Existing ownership check remains mandatory.
            store = UnifiedBatchStore(base/'unified-data/lake')
            for _ in range(max_batches):
                allowed()
                rows = _tasks(database, master['universe_version'], days, symbols)
                due = [row for row in rows
                       if datetime.fromisoformat(row['next_attempt_at']) <= current()]
                if not due:
                    break
                # Match claim(limit=1): retries precede fresh work, then oldest progress first.
                day = min(row['trading_day'] for row in due)
                candidates = [row for row in due if row['trading_day'] == day]
                retry = [row for row in candidates if row['state'] in ('FAILED', 'INTERRUPTED')]
                selected = min(retry or candidates, key=lambda row: (
                    row['completed_end'] is not None, row['completed_end'] or '',
                    row['next_attempt_at'], row['symbol']))
                target = clock(datetime.combine(date.fromisoformat(day), time(15), SHANGHAI))
                with queue.connect() as connection:
                    connection.execute("UPDATE collection_tasks SET desired_end=?,state=CASE "
                        "WHEN state='SUCCEEDED' THEN 'PLANNED' ELSE state END "
                        "WHERE universe_version=? AND frequency='5min' "
                        "AND symbol=? AND trading_day=?",
                        (target, master['universe_version'], selected['symbol'], day))
                batch = queue.claim(master['universe_version'], '5min', date.fromisoformat(day),
                                    now=current(), code_revision=revision, limit=1)
                if batch is None or batch['spec']['symbols'] != [selected['symbol']]:
                    raise ValueError('REPAIR_CLAIM_SCOPE_CHANGED')
                manifest = collect_batch(store, batch, calendar, source=trailing_source, workers=1)
                queue.finish(manifest, now=current())
                report['batches'].append({'batch_id': batch['batch_id'],
                    'manifest_sha256': manifest['evidence_sha256'], 'trading_day': day,
                    'outcomes': manifest['outcomes']})
        remaining = _tasks(database, master['universe_version'], days, symbols)
        report.update(state='UNRESOLVED' if remaining else 'REPAIRED',
                      unresolved_count=len(remaining), unresolved=summary(remaining))
    except Exception as error:
        report.update(state='BLOCKED', error_class=type(error).__name__)
        if str(error).startswith('REPAIR_'):
            report['error_code'] = str(error)
    return signed(report)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--max-batches', type=int, default=2)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        report = repair(args.base, args.database, revision=args.source_revision,
                        max_batches=args.max_batches, apply=args.apply)
    except Exception as error:
        parser.exit(1, 'REPAIR_REJECTED:'+type(error).__name__+'\n')
    print(json.dumps(report))
    if report['state'] == 'BLOCKED':
        parser.exit(1)


if __name__ == '__main__':
    main()
