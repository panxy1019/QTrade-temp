"""Low-priority daily ETF close diagnostics; one durable source attempt per exchange day."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import signal
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.etf_trend import etf_targets, verify_etf_archive
from qtrade.research.model_registry import checked_document, checked_identity
from scripts.atomic_json import write_json_atomic, write_json_immutable
from scripts.collect_unified_data import fetch
from scripts.run_etf_trend import run
from scripts.unified_store import UnifiedBatchStore

REPO = Path(__file__).resolve().parents[1]
RESERVE = 100*1024**3
HEADROOM = 128*1024**2
PERIOD_SECONDS = 300
HARD_DEADLINE_SECONDS = 900
SOURCE_ERRORS = {'SOURCE_HTTP_ERROR', 'SOURCE_TRANSPORT_ERROR'}


def utc_now() -> datetime:
    return datetime.now(UTC)


def load(path: Path, maximum: int = 300_000, *, signed_document: bool = True) -> dict[str, Any]:
    if path.is_symlink() or path.stat().st_size > maximum:
        raise ValueError('ETF_SERVICE_INPUT_BOUND_EXCEEDED')
    value: dict[str, Any] = json.loads(path.read_bytes())
    if signed_document:
        checked_document(value)
    return value


@contextlib.contextmanager
def exclusive(path: Path) -> Iterator[None]:
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def source_gate(base: Path, local_root: Path, calendar: Calendar, day: str,
                now: datetime) -> str | None:
    """Recheck current collection, real source failures and storage before every request."""
    if calendar.expected(now, 'daily').date().isoformat() != day:
        return 'ETF_SERVICE_SESSION_CHANGED'
    if shutil.disk_usage(base).free < RESERVE+HEADROOM:
        return 'ETF_SERVICE_STORAGE_RESERVE'
    backoff = local_root/'source-backoff.json'
    if backoff.exists():
        value = load(backoff)
        deadline = datetime.fromisoformat(value['not_before'])
        if deadline.tzinfo is None:
            return 'ETF_SERVICE_SOURCE_BACKOFF_INVALID'
        if now < deadline:
            return 'ETF_SERVICE_SOURCE_BACKOFF_ACTIVE'
    marker = load(base/'unified-data/service-state.json', signed_document=False)
    heartbeat = datetime.fromisoformat(marker['heartbeat_at'])
    if (heartbeat.tzinfo is None or not 0 <= (now-heartbeat).total_seconds() <= 300
            or marker['state'] != 'WAITING' or marker['live_trading_enabled'] is not False):
        return 'ETF_SERVICE_WAITING_CURRENT_COLLECTION'
    progress = load(base/'unified-data/collection-progress.json', 32_000_000)
    clock = datetime.fromisoformat(progress['updated_at'])
    if (clock.tzinfo is None or not 0 <= (now-clock).total_seconds() <= 300
            or progress['state'] != 'PASS_FINISHED'
            or datetime.fromisoformat(progress['required_through']['day']).date().isoformat()
            != day or not isinstance(progress['tasks'], list) or len(progress['tasks']) > 12_000):
        return 'ETF_SERVICE_CURRENT_COLLECTION_EVIDENCE_INVALID'
    for task in progress['tasks']:
        if task['state'] == 'RUNNING':
            return 'ETF_SERVICE_WAITING_CURRENT_COLLECTION'
        if task['state'] == 'FAILED' and task.get('last_error') in SOURCE_ERRORS:
            retry = datetime.fromisoformat(task['next_attempt_at'])
            if retry.tzinfo is None:
                return 'ETF_SERVICE_CURRENT_SOURCE_BACKOFF_INVALID'
            return ('ETF_SERVICE_CURRENT_SOURCE_BACKOFF_ACTIVE' if now < retry
                    else 'ETF_SERVICE_WAITING_CURRENT_SOURCE_RETRY')
    return None


def cycle(base: Path, local_root: Path, master_path: Path, revision: str, *,
          now: datetime | None = None, stop: Event | None = None) -> dict[str, Any]:
    if (os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false'
            or os.environ.get('QTRADE_AKSHARE_NONCOMMERCIAL_ACK', '').lower() != 'true'):
        raise ValueError('ETF_SERVICE_RESEARCH_CONFIGURATION_REQUIRED')
    now, stop = now or utc_now(), stop or Event()
    if now.tzinfo is None or re.fullmatch('[a-f0-9]{40}', revision) is None:
        raise ValueError('ETF_SERVICE_REVISION_AND_CLOCK_REQUIRED')
    calendar = Calendar(REPO/'configs/observer_calendar_2026.json')
    day = calendar.expected(now, 'daily').date().isoformat()
    output = base/'online-research/etf-trend'
    job = local_root/'days'/day
    local_root.mkdir(parents=True, exist_ok=True)
    detail: dict[str, Any] = {'required_day': day, 'diagnostic_id': None,
                              'published': False, 'source_revision': revision}

    def outcome(state: str) -> dict[str, Any]:
        value = signed({**AUTHORITY, **detail, 'schema_version': 'qtrade.etf-trend-service.v1',
                        'state': state, 'heartbeat_at': utc_now().isoformat()})
        write_json_atomic(local_root/'cycle.json', value)
        return value

    def publish(identity: str, value: dict[str, Any]) -> dict[str, Any]:
        if value['as_of'] != day or signed(value['bindings'])['evidence_sha256'] != identity:
            raise ValueError('ETF_SERVICE_VERIFIED_DAY_OR_IDENTITY_CHANGED')
        detail.update(diagnostic_id=identity,
                      qualified_close_symbols=value['qualified_close_symbols'],
                      required_symbols=value['required_symbols'])
        completed = signed({**AUTHORITY, 'as_of': day, 'diagnostic_id': identity,
                            'diagnostic_sha256': value['evidence_sha256']})
        completed_path = job/'completed.json'
        if completed_path.exists():
            if load(completed_path) != completed:
                raise ValueError('ETF_SERVICE_COMPLETED_DAY_CONFLICT')
        else:
            write_json_immutable(completed_path, completed)
        pointer = signed({**AUTHORITY, 'diagnostic_id': identity})
        latest = output/'latest.json'
        already_ready = latest.exists() and load(latest) == pointer
        if not already_ready:
            if latest.exists():
                previous_id = checked_identity(load(latest)['diagnostic_id'])
                previous = verify_etf_archive(output/previous_id, calendar)
                if previous['as_of'] >= day:
                    raise ValueError('ETF_SERVICE_LATEST_REPLACEMENT_REQUIRES_REVIEW')
            write_json_atomic(latest, pointer)
        detail['published'] = True
        return outcome('ETF_DAILY_DIAGNOSTIC_ALREADY_READY' if already_ready
                       else 'ETF_DAILY_DIAGNOSTIC_PUBLISHED')

    with exclusive(local_root/'cycle.lock'):
        # Durable completion and already-published archives need no fresh source or master.
        completed = job/'completed.json'
        if completed.exists():
            identity = checked_identity(load(completed)['diagnostic_id'])
            return publish(identity, verify_etf_archive(output/identity, calendar))
        latest = output/'latest.json'
        if latest.exists():
            identity = checked_identity(load(latest)['diagnostic_id'])
            value = verify_etf_archive(output/identity, calendar)
            if value['as_of'] >= day:
                if value['as_of'] != day:
                    return outcome('ETF_SERVICE_NEWER_ARCHIVE_REQUIRES_REVIEW')
                return publish(identity, value)
        if stop.is_set():
            return outcome('STOPPED')
        if reason := source_gate(base, local_root, calendar, day, now):
            return outcome(reason)
        request_path = job/'request.json'
        frozen_master = job/'master.json'
        if request_path.exists():
            request = load(request_path)
            master = load(frozen_master, 16_000_000)
            if (request['source_revision'] != revision or request['required_day'] != day
                    or request['master_sha256'] != master['evidence_sha256']):
                return outcome('ETF_DAILY_FROZEN_INPUT_CHANGED_REVIEW_REQUIRED')
        else:
            master = load(master_path, 16_000_000)
            symbols = etf_targets(master, now)
            bindings = {'master_sha256': master['evidence_sha256'], 'as_of': day,
                        'code_revision': revision, 'calendar_sha256': signed(calendar.document)[
                            'evidence_sha256'], 'contract': 'ETF_CLOSE_DIAGNOSTIC_V1'}
            request = signed({**AUTHORITY, 'required_day': day, 'source_revision': revision,
                'master_sha256': master['evidence_sha256'], 'symbols': symbols,
                'diagnostic_id': signed(bindings)['evidence_sha256'], 'bindings': bindings})
            if frozen_master.exists():
                if load(frozen_master, 16_000_000) != master:
                    return outcome('ETF_DAILY_FROZEN_MASTER_REQUIRES_REVIEW')
            else:
                write_json_immutable(frozen_master, master)
            write_json_immutable(request_path, request)
        identity = checked_identity(request['diagnostic_id'])
        detail['diagnostic_id'] = identity
        archive = output/identity
        if (archive/'diagnostic.json').exists():
            return publish(identity, verify_etf_archive(archive, calendar))
        # Resume only from durable bytes. A lost or interrupted request never reissues a burst.
        if (archive/'receipt.json').exists():
            run(frozen_master, output, revision, collect=False)
            return publish(identity, verify_etf_archive(archive, calendar))
        attempt = job/'attempt.json'
        if attempt.exists() or (archive/'collection-attempt.json').exists():
            return outcome('ETF_DAILY_ATTEMPT_REVIEW_REQUIRED')
        write_json_immutable(attempt, signed({**AUTHORITY, 'diagnostic_id': identity,
            'started_at': now.isoformat(), 'maximum_queries': len(request['symbols']),
            'automatic_retry': False}))
        captures = 0
        outcome('ETF_CAPTURING')

        def guarded_fetch(source: str, params: dict[str, str]) -> dict[str, Any]:
            nonlocal captures
            stamp = utc_now()
            if stop.is_set():
                raise ValueError('ETF_SERVICE_STOP_REQUESTED')
            if reason := source_gate(base, local_root, calendar, day, stamp):
                raise ValueError(reason)
            if captures >= len(request['symbols']):
                raise ValueError('ETF_SERVICE_REQUEST_BUDGET_EXCEEDED')
            response = fetch(source, params)
            captures += 1
            # Keep each response durable even if the next priority check aborts the burst.
            raw = UnifiedBatchStore(archive/'capture-store').capture([response])
            write_json_immutable(archive/'source-receipts'/f'{captures:02d}.json', signed({
                **AUTHORITY, 'diagnostic_id': identity, 'raw': raw}))
            detail['queries_completed'] = captures
            outcome('ETF_CAPTURING')
            if (response['status_code'] != 200 or response['transport_error'] is not None
                    or response['body_complete'] is not True):
                write_json_atomic(local_root/'source-backoff.json', signed({**AUTHORITY,
                    'source': 'sina_klines', 'observed_at': utc_now().isoformat(),
                    'not_before': (utc_now()+timedelta(hours=6)).isoformat(),
                    'reason': 'ETF_SOURCE_RESPONSE_FAILED', 'diagnostic_id': identity}))
            return response

        try:
            result = run(frozen_master, output, revision, collect=True, source=guarded_fetch)
            if result['bindings'] != request['bindings']:
                raise ValueError('ETF_SERVICE_CAPTURE_BINDINGS_CHANGED')
            return publish(identity, verify_etf_archive(archive, calendar))
        except Exception as exc:
            detail['error_code'] = (str(exc) if re.fullmatch('[A-Z][A-Z0-9_]{1,120}', str(exc))
                                    else type(exc).__name__)
            write_json_immutable(job/'failed.json', signed({**AUTHORITY,
                'diagnostic_id': identity, 'error_code': detail['error_code'],
                'queries_completed': captures, 'at': utc_now().isoformat()}))
            return outcome('ETF_DAILY_ATTEMPT_FAILED_REVIEW_REQUIRED')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--local-root', type=Path, required=True)
    parser.add_argument('--master', type=Path)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if os.name != 'posix':
        parser.error('The supervised service requires Linux local filesystem leases and alarms')
    stop = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    master = args.master or args.base/'unified-data/catalog/latest.json'
    with exclusive(args.local_root/'service.lock'):
        while not stop.is_set():
            try:
                signal.alarm(HARD_DEADLINE_SECONDS)
                value = cycle(args.base, args.local_root, master, args.source_revision, stop=stop)
                print(json.dumps(value), flush=True)
            except Exception as exc:
                write_json_atomic(args.local_root/'cycle.json', signed({**AUTHORITY,
                    'schema_version': 'qtrade.etf-trend-service.v1', 'state': 'FAILED',
                    'heartbeat_at': utc_now().isoformat(), 'error_class': type(exc).__name__,
                    'source_revision': args.source_revision}))
            finally:
                signal.alarm(0)
            if args.once:
                return
            stop.wait(PERIOD_SECONDS)


if __name__ == '__main__':
    main()
