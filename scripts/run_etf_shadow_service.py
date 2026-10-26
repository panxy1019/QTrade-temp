"""Supervise a separate ETF-only prospective allocation/SHADOW archive channel."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import signal
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from itertools import islice
from pathlib import Path
from threading import Event
from typing import Any

from qtrade.market_data.akshare import SHANGHAI
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.etf_research_contract import (
    BLOCKERS,
    EtfAllocationPolicy,
    etf_contract,
    freeze_etf_target,
    observe_etf_shadow,
    validate_etf_shadow_observation,
    validate_etf_target,
    verify_etf_shadow_observation,
    verify_etf_target,
)
from qtrade.research.etf_shadow_publication import (
    LIMITATIONS,
    MAXIMUM_BYTES,
    validate_etf_shadow_publication,
)
from qtrade.research.etf_trend import verify_etf_archive
from qtrade.research.forward_prediction import next_execution
from qtrade.research.model_registry import checked_document, checked_identity
from scripts.atomic_json import write_json_atomic, write_json_immutable
from scripts.unified_store import UnifiedBatchStore

REPO = Path(__file__).resolve().parents[1]
STRATEGY = 'etf_positive_trend20_native_close_rotation_v1'
RESERVE = 100*1024**3
HEADROOM = 16*1024**2
LOCAL_HEADROOM = 1024**3
PERIOD_SECONDS = 60
HARD_DEADLINE_SECONDS = 900
MAX_ARCHIVES = 4096
PUBLIC_ENTRIES = 30


def utc_now() -> datetime:
    return datetime.now(UTC)


def load(path: Path, maximum: int = 300_000) -> dict[str, Any]:
    if path.is_symlink() or path.stat().st_size > maximum:
        raise ValueError('ETF_SHADOW_DOCUMENT_BOUND_INVALID')
    value: dict[str, Any] = json.loads(path.read_bytes())
    checked_document(value)
    if any(type(value.get(key)) is not type(expected) for key, expected in AUTHORITY.items()):
        raise ValueError('ETF_SHADOW_AUTHORITY_INVALID')
    return value


@contextlib.contextmanager
def exclusive(path: Path) -> Iterator[None]:
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def record(path: Path, schema: str, fields: dict[str, Any]) -> dict[str, Any]:
    expected = signed({**AUTHORITY, 'schema_version': schema, **fields})
    if path.exists():
        if load(path) != expected:
            raise ValueError('ETF_SHADOW_IMMUTABLE_BINDING_CHANGED')
    else:
        write_json_immutable(path, expected)
    return expected


def source(root: Path, calendar: Calendar, now: datetime) -> dict[str, Any]:
    if root.is_symlink():
        raise ValueError('ETF_SHADOW_SOURCE_SYMLINK')
    checked_identity(root.name)
    value = verify_etf_archive(root, calendar)
    receipt = load(root/'receipt.json')
    captures = UnifiedBatchStore(root/'capture-store').read_captures(receipt['raw'])
    observed = max(datetime.fromisoformat(row['received_at']) for row in captures)
    if (observed.tzinfo is None or observed > now
            or calendar.expected(observed, 'daily').date().isoformat() != value['as_of']):
        raise ValueError('ETF_SHADOW_SOURCE_CLOCK_INVALID')
    return value


def discover(root: Path, errors: set[str]) -> tuple[str | None, dict[str, list[str]]]:
    """Headers only select candidates. Every used source undergoes raw replay."""
    latest = None
    try:
        pointer = load(root/'latest.json', 16_000)
        if set(pointer) != {*AUTHORITY, 'diagnostic_id', 'evidence_sha256'}:
            raise ValueError('ETF_SHADOW_POINTER_INVALID')
        latest = checked_identity(pointer['diagnostic_id'])
    except Exception:
        errors.add('ETF_SHADOW_LATEST_SOURCE_INVALID')
    candidates: dict[str, list[str]] = {}
    if not root.is_dir() or root.is_symlink():
        errors.add('ETF_SHADOW_SOURCE_DIRECTORY_INVALID')
        return latest, candidates
    paths = sorted(islice(root.iterdir(), MAX_ARCHIVES+2))
    if len(paths) > MAX_ARCHIVES+1:
        raise ValueError('ETF_SHADOW_ARCHIVE_SCAN_BOUND_EXCEEDED')
    for path in paths:
        if path.name == 'latest.json':
            continue
        try:
            checked_identity(path.name)
            if path.is_symlink() or not path.is_dir():
                raise ValueError('ETF_SHADOW_SOURCE_DIRECTORY_INVALID')
            header = load(path/'diagnostic.json')
            day = date.fromisoformat(header['as_of']).isoformat()
            if day != header['as_of']:
                raise ValueError('ETF_SHADOW_SOURCE_DAY_INVALID')
            candidates.setdefault(day, []).append(path.name)
        except Exception:
            errors.add('ETF_SHADOW_ARCHIVED_SOURCE_INVALID')
    return latest, candidates


def intent(path: Path, contract_id: str, calendar: Calendar,
           now: datetime) -> dict[str, Any]:
    value = load(path, 16_000)
    keys = {*AUTHORITY, 'schema_version', 'contract_id', 'diagnostic_id',
            'implementation_revision', 'as_of', 'expected_execution_at', 'issued_at',
            'evidence_sha256'}
    stamp = datetime.fromisoformat(value['issued_at'])
    execution = next_execution(value['as_of'], calendar)
    if (set(value) != keys or value['schema_version'] != 'qtrade.etf-shadow-intent.v1'
            or value['contract_id'] != contract_id
            or re.fullmatch('[a-f0-9]{40}', value['implementation_revision']) is None
            or stamp.tzinfo is None or not stamp < execution or stamp > now
            or value['expected_execution_at'] != execution.isoformat()
            or path.parent.name != execution.astimezone(SHANGHAI).date().isoformat()):
        raise ValueError('ETF_SHADOW_INTENT_INVALID')
    checked_identity(value['diagnostic_id'])
    return value


def completed(job: Path, request: dict[str, Any], targets: Path,
              calendar: Calendar, now: datetime) -> bool:
    """Old completed entries leave the public window, but never erase private evidence."""
    path = job/'completed.json'
    if not path.exists():
        return False
    value = load(path, 16_000)
    target_id = checked_identity(value['target_id'])
    observation_id = checked_identity(value['observation_id'])
    expected = signed({**AUTHORITY, 'schema_version': 'qtrade.etf-shadow-completed.v1',
                       'intent_sha256': request['evidence_sha256'], 'target_id': target_id,
                       'observation_id': observation_id})
    if value != expected:
        raise ValueError('ETF_SHADOW_COMPLETION_INVALID')
    target = validate_etf_target(load(targets/target_id/'target.json'), calendar=calendar, now=now)
    observation = validate_etf_shadow_observation(
        load(targets/target_id/'observations'/(observation_id+'.json')),
        target, calendar=calendar, now=now)
    target_context(target, request, target_id)
    if observation['observation_id'] != observation_id:
        raise ValueError('ETF_SHADOW_COMPLETION_CONTEXT_INVALID')
    return True


def target_context(target: dict[str, Any], request: dict[str, Any], target_id: str) -> None:
    if (target['target_id'] != target_id or any(target['bindings'][key] != request[key] for key in (
            'diagnostic_id', 'implementation_revision', 'contract_id', 'as_of',
            'expected_execution_at'))):
        raise ValueError('ETF_SHADOW_TARGET_CONTEXT_CHANGED')


def process(job: Path, request: dict[str, Any], archives: Path, targets: Path,
            candidates: dict[str, list[str]], calendar: Calendar, clock: Callable[[], datetime],
            writable: bool, stop: Event, errors: set[str],
            controlled_now: datetime | None) -> dict[str, Any]:
    binding = job/'target-binding.json'
    target_source = archives/request['diagnostic_id']
    if binding.exists():
        reference = load(binding, 16_000)
        target_id = checked_identity(reference['target_id'])
        record(binding, 'qtrade.etf-shadow-target-binding.v1', {
            'intent_sha256': request['evidence_sha256'], 'target_id': target_id})
        target_path = targets/target_id/'target.json'
        target = verify_etf_target(target_path, target_source, calendar=calendar, now=clock())
    else:
        if not writable or stop.is_set():
            raise ValueError('ETF_SHADOW_TARGET_WRITE_BLOCKED')
        # Existing core targets are replayed after a crash; a missing target cannot
        # be backdated even when its intent was durably written before the open.
        # Production passes None: the core samples the actual clock AFTER replay.
        target = freeze_etf_target(target_source, targets, request['implementation_revision'],
                                   calendar=calendar, now=controlled_now)
        target_id = target['target_id']
        target_path = targets/target_id/'target.json'
        record(binding, 'qtrade.etf-shadow-target-binding.v1', {
            'intent_sha256': request['evidence_sha256'], 'target_id': target_id})
    target_context(target, request, target_id)
    observation = None
    observation_source = job/'observation-source.json'
    execution = datetime.fromisoformat(request['expected_execution_at'])
    day = execution.astimezone(SHANGHAI).date().isoformat()
    if (not observation_source.exists()
            and calendar.expected(clock(), 'daily').date().isoformat() >= day):
        valid = []
        for identity in candidates.get(day, []):
            try:
                source(archives/identity, calendar, clock())
                valid.append(identity)
            except Exception:
                errors.add('ETF_SHADOW_OBSERVATION_SOURCE_INVALID')
        if len(valid) > 1:
            errors.add('ETF_SHADOW_OBSERVATION_SOURCE_AMBIGUOUS')
        elif valid and writable and not stop.is_set():
            record(observation_source, 'qtrade.etf-shadow-observation-source.v1', {
                'intent_sha256': request['evidence_sha256'], 'target_id': target_id,
                'diagnostic_id': valid[0]})
        else:
            errors.add('ETF_SHADOW_WAITING_EXACT_NEXT_SESSION_SOURCE')
    if observation_source.exists():
        reference = load(observation_source, 16_000)
        identity = checked_identity(reference['diagnostic_id'])
        record(observation_source, 'qtrade.etf-shadow-observation-source.v1', {
            'intent_sha256': request['evidence_sha256'], 'target_id': target_id,
            'diagnostic_id': identity})
        paths = sorted((target_path.parent/'observations').glob('*.json'))
        if len(paths) > 1:
            raise ValueError('ETF_SHADOW_MULTIPLE_OBSERVATIONS')
        if paths:
            checked_identity(paths[0].stem)
            observation = verify_etf_shadow_observation(paths[0], target_path, target_source,
                archives/identity, calendar=calendar, now=clock())
            if observation['observation_id'] != paths[0].stem:
                raise ValueError('ETF_SHADOW_OBSERVATION_PATH_CHANGED')
        elif writable and not stop.is_set():
            observation = observe_etf_shadow(target_path, target_source, archives/identity,
                                            calendar=calendar, now=controlled_now)
        if observation is not None and writable and not stop.is_set():
            record(job/'completed.json', 'qtrade.etf-shadow-completed.v1', {
                'intent_sha256': request['evidence_sha256'], 'target_id': target_id,
                'observation_id': observation['observation_id']})
    return {'target': target, 'observation': observation}


def publish(base: Path, local_root: Path, revision: str, entries: list[dict[str, Any]],
            errors: set[str], stop: Event, clock: Callable[[], datetime]) -> dict[str, Any]:
    contract = etf_contract(EtfAllocationPolicy())
    entries = entries[:PUBLIC_ENTRIES]
    while True:
        stamp = clock().isoformat()
        value = signed({**AUTHORITY, 'schema_version': 'qtrade.etf-shadow-service.v1',
            'security_type': 'ETF', 'mode': 'SHADOW', 'strategy_id': STRATEGY,
            'contract_id': signed(contract)['evidence_sha256'], 'contract': contract,
            'source_revision': revision, 'generated_at': stamp, 'heartbeat_at': stamp,
            'state': 'STOPPED' if stop.is_set() else 'DEGRADED' if errors else 'RUNNING',
            'entries': entries, 'errors': sorted(errors)[:32],
            'execution_admitted': False,
            'paper_admission': {'state': 'BLOCKED', 'reasons': BLOCKERS},
            'orders': [], 'fills': [], 'holdings': [], 'cash': None,
            'net_asset_value': None, 'forward_return': None, 'limitations': LIMITATIONS})
        if len(json.dumps(value, ensure_ascii=False,
                          separators=(',', ':')).encode()) <= MAXIMUM_BYTES:
            break
        entries.pop()
        errors.add('ETF_SHADOW_PUBLICATION_WINDOW_TRUNCATED')
    validate_etf_shadow_publication(value, now=clock())
    write_json_atomic(base/'workflow/public/etf-forward.json', value, compact=True)
    write_json_atomic(local_root/'cycle.json', value, compact=True)
    return value


def cycle(base: Path, local_root: Path, revision: str, *, now: datetime | None = None,
          stop: Event | None = None) -> dict[str, Any]:
    if (os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false'
            or re.fullmatch('[a-f0-9]{40}', revision) is None):
        raise ValueError('ETF_SHADOW_CONFIGURATION_INVALID')
    def clock() -> datetime:
        return now or utc_now()

    if clock().tzinfo is None:
        raise ValueError('ETF_SHADOW_AWARE_CLOCK_REQUIRED')
    stop = stop or Event()
    calendar = Calendar(REPO/'configs/observer_calendar_2026.json')
    contract = etf_contract(EtfAllocationPolicy())
    contract_id = signed(contract)['evidence_sha256']
    if local_root.is_symlink():
        raise ValueError('ETF_SHADOW_LOCAL_ROOT_SYMLINK')
    local_root.mkdir(parents=True, exist_ok=True)
    jobs = local_root/'contracts'/contract_id/'sessions'
    targets = local_root/'targets'
    archives = base/'online-research/etf-trend'
    errors: set[str] = set()
    data_space = shutil.disk_usage(base).free >= RESERVE+HEADROOM
    local_space = shutil.disk_usage(local_root).free >= LOCAL_HEADROOM
    writable = data_space and local_space and not stop.is_set()
    if not stop.is_set():
        if not data_space:
            errors.add('ETF_SHADOW_STORAGE_RESERVE')
        if not local_space:
            errors.add('ETF_SHADOW_LOCAL_STORAGE_HEADROOM')
    latest, candidates = discover(archives, errors)
    if latest is not None and writable and not stop.is_set():
        try:
            diagnostic = source(archives/latest, calendar, clock())
            day = calendar.expected(clock(), 'daily').date().isoformat()
            if diagnostic['as_of'] != day:
                raise ValueError('ETF_SHADOW_CURRENT_SOURCE_STALE')
            if diagnostic['state'] != 'CLOSE_DIAGNOSTIC_READY':
                raise ValueError('ETF_SHADOW_CURRENT_SOURCE_UNAVAILABLE')
            execution = next_execution(day, calendar)
            job = jobs/execution.astimezone(SHANGHAI).date().isoformat()
            if not (job/'intent.json').exists():
                stamp = clock()
                if stamp >= execution or stop.is_set():
                    raise ValueError('ETF_SHADOW_PROSPECTIVE_FREEZE_MISSED')
                record(job/'intent.json', 'qtrade.etf-shadow-intent.v1', {
                    'contract_id': contract_id, 'diagnostic_id': latest,
                    'implementation_revision': revision, 'as_of': day,
                    'expected_execution_at': execution.isoformat(), 'issued_at': stamp.isoformat()})
        except Exception as exc:
            code = str(exc)
            errors.add(code if code in {'ETF_SHADOW_CURRENT_SOURCE_STALE',
                'ETF_SHADOW_CURRENT_SOURCE_UNAVAILABLE', 'ETF_SHADOW_PROSPECTIVE_FREEZE_MISSED'}
                       else 'ETF_SHADOW_CURRENT_SOURCE_INVALID')
    paths = sorted(islice(jobs.glob('*/intent.json'), MAX_ARCHIVES+1))
    if len(paths) > MAX_ARCHIVES:
        raise ValueError('ETF_SHADOW_INTENT_SCAN_BOUND_EXCEEDED')
    entries = []
    for index, path in enumerate(paths):
        try:
            request = intent(path, contract_id, calendar, clock())
            if index < len(paths)-PUBLIC_ENTRIES and completed(
                    path.parent, request, targets, calendar, clock()):
                continue
            entry = process(path.parent, request, archives, targets, candidates, calendar,
                            clock, writable, stop, errors, now)
            if index >= len(paths)-PUBLIC_ENTRIES:
                entries.append(entry)
        except Exception:
            errors.add('ETF_SHADOW_TARGET_OR_OBSERVATION_REPLAY_FAILED')
    return publish(base, local_root, revision, list(reversed(entries)), errors, stop, clock)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--local-root', type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if os.name != 'posix':
        parser.error('The supervised service requires Linux filesystem leases and alarms')
    if (os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false'
            or re.fullmatch('[a-f0-9]{40}', args.source_revision) is None):
        parser.error('A source revision and LIVE_TRADING_ENABLED=false are required')
    stop = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    with exclusive(args.local_root/'service.lock'):
        while not stop.is_set():
            try:
                signal.alarm(HARD_DEADLINE_SECONDS)
                value = cycle(args.base, args.local_root, args.source_revision, stop=stop)
                print(json.dumps({'state': value['state'], 'errors': value['errors'],
                                  'heartbeat_at': value['heartbeat_at']}), flush=True)
            except Exception:
                # Never republish unverified prior entries after a failed cycle.
                publish(args.base, args.local_root, args.source_revision, [],
                        {'ETF_SHADOW_CYCLE_FAILED'}, stop, utc_now)
            finally:
                signal.alarm(0)
            if args.once:
                stop.set()
                break
            stop.wait(PERIOD_SECONDS)
        # Replayed read-only evidence and an explicit STOPPED heartbeat on a clean exit.
        cycle(args.base, args.local_root, args.source_revision, stop=stop)


if __name__ == '__main__':
    main()
