"""Version recent closed-session vendor observations separately from PIT backfills."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import re
import shutil
import signal
import socket
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from threading import Event
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.pit_quality import status_quality
from qtrade.research.workflow import digest
from scripts.atomic_json import write_json_atomic, write_json_immutable
from scripts.collect_pit_evidence import FIELDS, rows
from scripts.pit_collection_queue import (
    begin_attempt,
    end_attempt,
    queue_progress,
    read_attempts,
    save_attempts,
    select_batch,
)
from scripts.vendor_session import baostock_session

REPO = Path(__file__).resolve().parents[1]


def next_pass_delay(base: Path, now: datetime) -> float:
    """A watcher restart must not spend another batch before the durable cadence is due."""
    path = base / 'recent-status/current.json'
    if not path.exists():
        return 0
    value = json.loads(path.read_bytes())
    if (signed({k: v for k, v in value.items() if k != 'evidence_sha256'}) != value
            or value.get('schema_version') != 'qtrade.recent-status-coverage.v1'
            or any(value.get(k) != v for k, v in AUTHORITY.items())):
        raise ValueError('RECENT_STATUS_SCHEDULE_IDENTITY_INVALID')
    at = datetime.fromisoformat(value['observed_at'])
    if now.tzinfo is None or at.tzinfo is None or at > now:
        raise ValueError('RECENT_STATUS_SCHEDULE_CLOCK_INVALID')
    return max(0, 300 - (now - at).total_seconds())


def window_scope(
    dataset: dict[str, Any], calendar: Calendar, now: datetime,
) -> dict[str, Any]:
    symbols = [item['symbol'] for item in dataset['stocks']]
    if (not 1 <= len(symbols) <= 1500 or len(set(symbols)) != len(symbols)
            or any(re.fullmatch(r'[036][0-9]{5}', s) is None for s in symbols)):
        raise ValueError('RECENT_STATUS_SCOPE_INVALID')
    end = calendar.expected(now, 'daily').date()
    days = [end]
    for _ in range(2):
        days.append(calendar.previous(days[-1] - timedelta(days=1)))
    return {'stocks': [{'symbol': symbol} for symbol in symbols],
            'source_dataset_sha256': digest(dataset), 'calendar_sha256': digest(calendar.document),
            'start': days[-1].isoformat(), 'end': end.isoformat(),
            'expected_days': sorted(day.isoformat() for day in days)}


def verified_recent(path: Path, scope: dict[str, Any], now: datetime) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_bytes())
    if (signed({k: v for k, v in value.items() if k != 'evidence_sha256'}) != value
            or value.get('schema_version') != 'qtrade.recent-status-observation.v1'
            or value.get('window_sha256') != digest(scope)
            or any(value.get(k) != v for k, v in AUTHORITY.items())
            or value.get('raw_transport_bytes_captured') is not False
            or value.get('historical_pit_certified') is not False):
        raise ValueError('RECENT_STATUS_BINDING_INVALID')
    symbol = value['symbol']
    expected_request = {'code': ('sh.' if symbol.startswith('6') else 'sz.') + symbol,
                        'fields': FIELDS, 'start_date': scope['start'],
                        'end_date': scope['end'], 'frequency': 'd', 'adjustflag': '3'}
    started = datetime.fromisoformat(value['query_started_at'])
    observed = datetime.fromisoformat(value['observed_at'])
    if (value.get('request') != expected_request or value.get('sdk_error_code') != '0'
            or re.fullmatch(r'[a-f0-9]{40}', value.get('source_revision', '')) is None
            or started.tzinfo is None or observed.tzinfo is None or started > observed):
        raise ValueError('RECENT_STATUS_REQUEST_OR_CLOCK_INVALID')
    quality = status_quality(value, symbol, now, start=scope['start'], end=scope['end'])
    if (value.get('quality') != quality
            or value.get('observed_at') != value.get('system_available_at')
            or value.get('requested_days_complete') is not True
            or [row['date'] for row in value['rows']] != scope['expected_days']):
        raise ValueError('RECENT_STATUS_WINDOW_INCOMPLETE')
    return value


def collect_pass(
    base: Path, dataset: dict[str, Any], calendar: Calendar, *,
    batch_size: int = 20, revision: str, vendor: Any,
) -> dict[str, Any]:
    if (os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false'
            or re.fullmatch(r'[0-9a-f]{40}', revision) is None
            or type(batch_size) is not int or not 1 <= batch_size <= 50):
        raise ValueError('RECENT_STATUS_BOUNDS_OR_AUTHORITY_INVALID')
    now = datetime.now(UTC)
    scope = window_scope(dataset, calendar, now)
    root = base / 'recent-status' / 'windows' / digest(scope)
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(base).free < 100 * 1024**3:
        raise ValueError('DISK_RESERVE_BELOW_100_GIB')
    symbols = [s['symbol'] for s in scope['stocks']]
    entries = read_attempts(root, scope, now)
    completed: set[str] = set()
    checksums: dict[str, str] = {}
    for path in sorted((root / 'status').glob('*.json')):
        if path.stem not in symbols:
            raise ValueError('RECENT_STATUS_OUTSIDE_SCOPE')
        value = verified_recent(path, scope, now)
        if value['symbol'] != path.stem:
            raise ValueError('RECENT_STATUS_SYMBOL_MISMATCH')
        completed.add(path.stem)
        checksums[path.stem] = value['evidence_sha256']
    selected = select_batch(symbols, completed, entries, now, batch_size)
    outcome = 'NO_QUERY_NEEDED'
    observations: list[dict[str, Any]] = []
    if selected:
        try:
            with baostock_session(blocking=False):
                if vendor.login().error_code != '0':
                    raise ValueError('ANONYMOUS_VENDOR_LOGIN_FAILED')
                try:
                    for symbol in selected:
                        begin_attempt(entries, symbol, datetime.now(UTC))
                        save_attempts(root, scope, entries)
                        request = {'code': ('sh.' if symbol.startswith('6') else 'sz.') + symbol,
                                   'fields': FIELDS, 'start_date': scope['start'],
                                   'end_date': scope['end'], 'frequency': 'd', 'adjustflag': '3'}
                        try:
                            query_started = datetime.now(UTC)
                            result = vendor.query_history_k_data_plus(**request)
                            values = rows(result)
                            observed = datetime.now(UTC)
                            record = {**AUTHORITY,
                                'schema_version': 'qtrade.recent-status-observation.v1',
                                'source': 'BaoStock raw daily status backfill', 'symbol': symbol,
                                'window_sha256': digest(scope), 'request': request,
                                'query_started_at': query_started.isoformat(),
                                'sdk_fields': result.fields,
                                'sdk_error_code': str(result.error_code),
                                'observed_at': observed.isoformat(),
                                'system_available_at': observed.isoformat(),
                                'historical_available_at': None, 'certified_at_decision': False,
                                'historical_pit_certified': False,
                                'raw_transport_bytes_captured': False, 'rows': values,
                                'requested_days_complete': [r.get('date') for r in values]
                                == scope['expected_days'],
                                'quality': None, 'source_revision': revision,
                                'vendor_sdk_version': importlib.metadata.version('baostock')}
                            # Keep decoded content even when validation or coverage fails.
                            original = signed(record)
                            write_json_immutable(root / 'responses' /
                                                 (original['evidence_sha256'] + '.json'), original)
                            record['quality'] = status_quality(
                                record, symbol, observed, start=scope['start'], end=scope['end'])
                            if not record['requested_days_complete']:
                                raise ValueError('RECENT_STATUS_REQUESTED_DAYS_MISSING')
                            accepted = signed(record)
                            write_json_immutable(root / 'status' / (symbol + '.json'), accepted)
                            completed.add(symbol)
                            checksums[symbol] = accepted['evidence_sha256']
                            observations.append({'symbol': symbol,
                                                 'evidence_sha256': accepted['evidence_sha256']})
                            end_attempt(entries, symbol, None)
                        except Exception as error:
                            end_attempt(entries, symbol, error)
                            # A failed query can poison the SDK's global socket. End this pass.
                            save_attempts(root, scope, entries)
                            outcome = 'QUERY_FAILED_RETRY_RETAINED'
                            break
                        save_attempts(root, scope, entries)
                    else:
                        outcome = 'BOUNDED_PASS_FINISHED'
                finally:
                    vendor.logout()
        except BlockingIOError:
            outcome = 'VENDOR_LEASE_BUSY_NO_QUERY_SENT'
    progress = queue_progress(symbols, completed, entries, datetime.now(UTC))
    summary = signed({**AUTHORITY, 'schema_version': 'qtrade.recent-status-coverage.v1',
        'observed_at': datetime.now(UTC).isoformat(), 'scope': scope,
        'window_sha256': digest(scope), 'source_revision': revision,
        'outcome': outcome,
        'state': 'COMPLETE' if not progress['unresolved_symbols'] else 'PARTIAL',
        'collection_progress': progress, 'new_observations': observations,
        'status_checksums': checksums,
        'historical_pit_certified': False, 'execution_enforcement': 'NOT_USED',
        'missing': ['HISTORICAL_INFORMATION_AVAILABILITY', 'EXACT_PRICE_LIMITS',
                    'INDEPENDENT_EXCHANGE_STATE_RECONCILIATION']})
    version = root / 'coverage-versions' / (summary['evidence_sha256'] + '.json')
    write_json_immutable(version, summary)
    write_json_atomic(base / 'recent-status/current.json', summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    fcntl = import_module('fcntl')
    bs = import_module('baostock')

    stop = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    socket.setdefaulttimeout(20)
    # Separate owner from fixed historical PIT and main ingestion. Shared vendor lease remains.
    lease_path = Path('/root/qtrade-local/recent-status-worker.lock')
    with lease_path.open('a') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while not stop.is_set():
            try:
                if delay := next_pass_delay(args.base, datetime.now(UTC)):
                    if not args.watch:
                        print('RECENT_STATUS_PASS_NOT_DUE', flush=True)
                        return
                    stop.wait(min(60, delay))
                    continue
                result = collect_pass(
                    args.base, json.loads(args.dataset.read_bytes()),
                    Calendar(REPO / 'configs/observer_calendar_2026.json'),
                    revision=args.source_revision, vendor=bs)
                print(json.dumps({'state': result['state'], 'outcome': result['outcome'],
                                  'progress': result['collection_progress']}), flush=True)
            except Exception as error:
                # Never log vendor payloads, private config or arbitrary exception text.
                print('RECENT_STATUS_PASS_FAILED:' + type(error).__name__, flush=True)
                if not args.watch:
                    raise SystemExit(1) from None
            if not args.watch:
                break
            stop.wait(300)


if __name__ == '__main__':
    main()
