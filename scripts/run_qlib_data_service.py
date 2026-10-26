"""Supervised bounded daily preparation and immutable Provider publication."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_epoch import current_epoch
from qtrade.research.data_versions import file_hash
from qtrade.research.forward_prediction import next_execution
from scripts.atomic_json import write_json_atomic, write_json_immutable
from scripts.build_qlib_daily_version import build
from scripts.prepare_qlib_incremental import daily_tasks, load_signed
from scripts.prepare_qlib_incremental import run as prepare

REPO = Path(__file__).resolve().parents[1]


def head(base: Path, database: Path) -> tuple[Path, dict[str, Any]]:
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True) as c:
        row = c.execute("SELECT h.version_id,v.manifest_sha256 FROM data_heads h "
                        "JOIN data_versions v USING(version_id) WHERE frequency='day'").fetchone()
    if row is None or re.fullmatch('[a-f0-9]{64}', row[0]) is None:
        raise ValueError('DATA_SERVICE_QUALIFIED_HEAD_REQUIRED')
    root = base/'unified-data/qlib-versions/versions'/row[0]
    value = load_signed(root/'version.json')
    if value['version_id'] != row[0] or value['evidence_sha256'] != row[1]:
        raise ValueError('DATA_SERVICE_HEAD_IDENTITY_CHANGED')
    return root, value


def collection_idle(base: Path, now: datetime) -> bool:
    try:
        value = json.loads((base/'unified-data/service-state.json').read_bytes())
        stamp = datetime.fromisoformat(value['heartbeat_at'])
        return bool(stamp.tzinfo and 0 <= (now-stamp).total_seconds() <= 300
                    and value['state'] == 'WAITING')
    except (ValueError, KeyError, TypeError, OSError):
        return False


def ready_identity(folder: Path) -> str:
    request = load_signed(folder/'request.json')
    return str(signed({'request': request['evidence_sha256'], 'ready': {
        s: file_hash(folder/'stocks'/s/'manifest.json') for s in request['batches']
        if (folder/'stocks'/s/'manifest.json').exists()}})['evidence_sha256'])


@contextlib.contextmanager
def exclusive(path: Path) -> Iterator[None]:
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def cycle(base: Path, database: Path, python: Path, revision: str, *, now: datetime,
          collection_database: Path | None = None) -> dict[str, Any]:
    if re.fullmatch('[a-f0-9]{40}', revision) is None or now.tzinfo is None:
        raise ValueError('DATA_SERVICE_REVISION_AND_CLOCK_REQUIRED')
    root = base/'unified-data/qlib-schedule'
    root.mkdir(parents=True, exist_ok=True)
    calendar = Calendar(REPO/'configs/observer_calendar_2026.json')
    day = calendar.expected(now, 'daily').date().isoformat()
    previous, current = head(base, database)
    epoch = current_epoch(database, current['version_id'])
    detail: dict[str, Any] = {'required_day': day, 'data_version_id': current['version_id'],
                              'data_end': current['actual_end'], 'provider_published': False}

    def outcome(state: str) -> dict[str, Any]:
        value = signed({**AUTHORITY, **detail, 'state': state, 'updated_at': now.isoformat(),
                        'source_revision': revision})
        write_json_atomic(root/'cycle.json', value)
        return value

    if current['actual_end'] >= day:
        return outcome('CURRENT_DAY_ALREADY_READY')
    if next_execution(current['actual_end'], calendar).date().isoformat() != day:
        return outcome('BLOCKED_MISSED_SESSION_REQUIRES_BACKFILL')
    if next_execution(day, calendar) <= now:
        return outcome('BLOCKED_EXECUTION_WINDOW_PASSED')
    if not collection_idle(base, now):
        return outcome('WAITING_FOR_CURRENT_COLLECTION')
    if shutil.disk_usage(base).free < 100*1024**3:
        return outcome('BLOCKED_STORAGE_RESERVE')
    if collection_database is None:
        try:
            progress = load_signed(base/'unified-data/collection-progress.json')
        except FileNotFoundError:
            return outcome('WAITING_FOR_CURRENT_DAILY_INPUTS')
        daily_end = progress.get('required_through', {}).get('day')
        if daily_end is None or datetime.fromisoformat(daily_end).date().isoformat() != day:
            return outcome('WAITING_FOR_CURRENT_DAILY_INPUTS')
    elif current.get('bindings', {}).get('observation_universe_version') is None:
        return outcome('WAITING_FOR_CURRENT_DAILY_INPUTS')
    job_root = root/'days'/day
    job_file = job_root/'request.json'
    if (collection_database is not None and not job_file.exists()
            and daily_tasks(base, load_signed(previous/'dataset.json'), day,
                current['bindings']['observation_universe_version'], collection_database) is None):
        return outcome('WAITING_FOR_CURRENT_DAILY_INPUTS')
    request = signed({**AUTHORITY, 'day': day, 'previous': previous.relative_to(base).as_posix(),
                      'previous_version': current['version_id'], 'previous_event_id': epoch,
                      'source_revision': revision})
    if job_file.exists():
        saved = load_signed(job_file)
        if saved != request:
            return outcome('BLOCKED_DAILY_INPUT_CHANGED_REVIEW_REQUIRED')
    else:
        write_json_immutable(job_file, request)
    attempt_file = job_root/'attempt.json'
    if attempt_file.exists():
        attempt = load_signed(attempt_file)
        if datetime.fromisoformat(attempt['not_before']) > now:
            detail['not_before'] = attempt['not_before']
            return outcome('WAITING_FOR_RETRY')
    # Persist the retry deadline before work; interrupted passes cannot spin.
    write_json_atomic(attempt_file, signed({**AUTHORITY, 'started_at': now.isoformat(),
        'not_before': (now+timedelta(minutes=5)).isoformat(), 'state': 'RUNNING'}))
    with exclusive(database.parent/'qlib-data-preparation.lock'):
        prepared = prepare(base, previous, revision=revision, maximum=25,
            collection_database=collection_database, day=day,
            universe_version=current.get('bindings', {}).get('observation_universe_version'))
    if prepared.get('state') == 'WAITING_FOR_CURRENT_DAILY_INPUTS':
        return outcome('WAITING_FOR_CURRENT_DAILY_INPUTS')
    detail['preparation'] = {k: prepared[k] for k in (
        'request_sha256', 'prepared_symbols', 'qualified_current_daily_inputs', 'retry_queue')}
    if prepared['retry_queue']['unattempted_symbols'] or not prepared['prepared_symbols']:
        return outcome('PREPARING_DAILY_INPUTS')
    folder = base/'unified-data/qlib-preparation'/prepared['request_sha256']
    identity = ready_identity(folder)
    build_file = job_root/'build-attempt.json'
    if build_file.exists():
        last = load_signed(build_file)
        if (last['prepared_identity'] == identity
                and datetime.fromisoformat(last['not_before']) > now):
            return outcome('WAITING_FOR_BUILD_RETRY')
    # Retry metadata does not define a new prepared snapshot. Only ready bytes do.
    write_json_atomic(build_file, signed({**AUTHORITY, 'prepared_identity': identity,
        'not_before': (now+timedelta(minutes=30)).isoformat(), 'state': 'BUILDING'}))
    if not collection_idle(base, datetime.now(UTC)):
        return outcome('WAITING_FOR_CURRENT_COLLECTION')
    # Exclude manual builders while the automatic one publishes the same ledger.
    with exclusive(database.parent/'qlib-provider-build.lock'):
        _, final_head = head(base, database)
        if (final_head['version_id'] != current['version_id']
                or current_epoch(database, final_head['version_id']) != epoch):
            return outcome('BLOCKED_DAILY_HEAD_CHANGED_REVIEW_REQUIRED')
        value = build(base, previous, folder, database, revision=revision, qlib_python=python)
    if value['state'] != 'RESEARCH_READY_QUALIFIED_SUBSET':
        return outcome('BLOCKED_PROVIDER_VALIDATION')
    write_json_immutable(job_root/'completed.json', signed({**AUTHORITY,
        'prepared_identity': identity, 'result': value,
        'completed_at': datetime.now(UTC).isoformat()}))
    detail.update(data_version_id=value['version_id'], data_end=value['actual_end'],
                  provider_published=True)
    return outcome('DAILY_VERSION_PUBLISHED')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('base', 'database', 'qlib-python'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--collection-database', type=Path)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--cycle', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
        raise RuntimeError('LIVE_TRADING_MUST_REMAIN_DISABLED')
    if os.environ.get('QTRADE_AKSHARE_NONCOMMERCIAL_ACK', '').lower() != 'true':
        raise RuntimeError('NONCOMMERCIAL_RESEARCH_ACK_REQUIRED')
    if args.cycle:
        # Hard independent deadline also applies after supervisor-parent SIGKILL.
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.alarm(900)
        print(json.dumps(cycle(args.base, args.database, args.qlib_python,
                               args.source_revision, now=datetime.now(UTC),
                               collection_database=args.collection_database)))
        return
    import fcntl

    args.database.parent.mkdir(parents=True, exist_ok=True)
    with (args.database.parent/'qlib-data-service.lock').open('a') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stop = Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        root = args.base/'unified-data/qlib-schedule'
        root.mkdir(parents=True, exist_ok=True)
        while not stop.is_set():
            started = time.monotonic()
            with (root/'service.log').open('ab') as log:
                collection_args = (['--collection-database', str(args.collection_database)]
                                   if args.collection_database is not None else [])
                child = subprocess.Popen([sys.executable, '-m', 'scripts.run_qlib_data_service',
                    '--base', str(args.base), '--database', str(args.database), '--qlib-python',
                    str(args.qlib_python), '--source-revision', args.source_revision, '--cycle',
                    *collection_args],
                    cwd=REPO, env={**os.environ, 'PYTHONPATH': str(REPO/'src')},
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                    pass_fds=(lease.fileno(),))
            while child.poll() is None and not stop.is_set():
                write_json_atomic(root/'service-state.json', signed({**AUTHORITY,
                    'state': 'PREPARING_OR_VERIFYING',
                    'heartbeat_at': datetime.now(UTC).isoformat(),
                    'source_revision': args.source_revision, 'child_pid': child.pid}))
                stop.wait(10)
            if child.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(10)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
            state = ('STOPPED' if stop.is_set()
                     else ('WAITING' if child.returncode == 0 else 'FAILED'))
            while True:
                write_json_atomic(root/'service-state.json', signed({**AUTHORITY,
                    'state': state, 'heartbeat_at': datetime.now(UTC).isoformat(),
                    'source_revision': args.source_revision, 'last_returncode': child.returncode}))
                if args.once or stop.is_set() or time.monotonic()-started >= 300:
                    break
                stop.wait(10)
            if args.once:
                break


if __name__ == '__main__':
    main()
