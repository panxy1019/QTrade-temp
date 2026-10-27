"""Explicit bounded repair of a proven collection-preempted ETF attempt.

Planning and resolution verification only read files. Applying never resumes an
interrupted repair: a durable request reservation without a receipt is uncertain.
"""
from __future__ import annotations

import json
import os
import re
import signal
from datetime import UTC, date, datetime, timedelta
from hashlib import sha1
from itertools import islice
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_versions import file_hash
from qtrade.research.etf_trend import admit, etf_targets, verify_etf_archive
from qtrade.research.model_registry import checked_document, checked_identity
from qtrade.research.qlib_publication import verify_signed
from scripts.atomic_json import write_json_atomic, write_json_immutable
from scripts.collect_unified_data import fetch
from scripts.run_etf_trend_service import REPO, exclusive, source_gate
from scripts.unified_store import UnifiedBatchStore

SCHEMA = 'qtrade.etf-attempt-repair-plan.v1'
FAILURE = 'ETF_SERVICE_WAITING_CURRENT_COLLECTION_RETRY'
MAX_SECONDS = 900
CAPTURE_FIELDS = {'source_id', 'request', 'received_at', 'status_code', 'body',
                  'transport_error', 'body_complete'}


def utc_now() -> datetime:
    return datetime.now(UTC)


def read(path: Path, maximum: int = 300_000, *, authority: bool = True) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= maximum:
        raise ValueError('ETF_REPAIR_INPUT_BOUND_INVALID')
    value: dict[str, Any] = json.loads(path.read_bytes())
    verify_signed(value)
    if authority:
        checked_document(value)
        if any(type(value.get(k)) is not type(v) for k, v in AUTHORITY.items()):
            raise ValueError('ETF_REPAIR_AUTHORITY_INVALID')
    return value


def stamp(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result > utc_now():
        raise ValueError('ETF_REPAIR_CLOCK_INVALID')
    return result


def calendar() -> Calendar:
    return Calendar(REPO/'configs/observer_calendar_2026.json')


def request_for(symbol: str) -> dict[str, str]:
    return {'symbol': ('sh' if symbol.startswith('5') else 'sz')+symbol,
            'scale': '240', 'ma': 'no', 'datalen': '63'}


def receipts(root: Path) -> list[Path]:
    if root.is_symlink():
        raise ValueError('ETF_REPAIR_RECEIPTS_SYMLINK')
    paths = sorted(islice(root.iterdir(), 51)) if root.exists() else []
    if len(paths) > 50 or [p.name for p in paths] != [
            f'{i:02d}.json' for i in range(1, len(paths)+1)]:
        raise ValueError('ETF_REPAIR_RECEIPT_INVENTORY_INVALID')
    return paths


def healthy(capture: dict[str, Any], symbol: str, day: str, cal: Calendar) -> None:
    if (capture['source_id'] != 'sina_klines' or capture['request'] != request_for(symbol)
            or capture['status_code'] != 200 or capture['transport_error'] is not None
            or capture['body_complete'] is not True):
        raise ValueError('ETF_REPAIR_SOURCE_UNHEALTHY')
    observed = stamp(capture['received_at'])
    result = admit([capture], [symbol], cal, date.fromisoformat(day), observed)
    if result['qualified_close_symbols'] != 1:
        raise ValueError('ETF_REPAIR_SOURCE_CLOSES_INVALID')


def origin(archive: Path, documents: dict[str, dict[str, Any]], master: dict[str, Any],
           cal: Calendar) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Prove the old loop stopped at a gate before any next HTTP request."""
    request, attempt, failed = (documents[k] for k in ('request', 'attempt', 'failed'))
    for document in documents.values():
        checked_document(document)
    day = request['required_day']
    bindings = request['bindings']
    symbols = etf_targets(master, utc_now())
    identity = checked_identity(request['diagnostic_id'])
    count = failed['queries_completed']
    if (archive.is_symlink() or archive.name != identity
            or signed(bindings)['evidence_sha256'] != identity
            or bindings != {'master_sha256': master['evidence_sha256'], 'as_of': day,
                'code_revision': request['source_revision'],
                'calendar_sha256': signed(cal.document)['evidence_sha256'],
                'contract': 'ETF_CLOSE_DIAGNOSTIC_V1'}
            or re.fullmatch('[a-f0-9]{40}', request['source_revision']) is None
            or request['master_sha256'] != master['evidence_sha256']
            or request['symbols'] != symbols or attempt['diagnostic_id'] != identity
            or attempt['maximum_queries'] != len(symbols) or attempt['automatic_retry'] is not False
            or failed['diagnostic_id'] != identity or failed['error_code'] != FAILURE
            or type(count) is not int or not 1 <= count < len(symbols)
            or (archive/'receipt.json').exists() or (archive/'diagnostic.json').exists()):
        raise ValueError('ETF_REPAIR_ORIGINAL_ATTEMPT_NOT_PROVEN')
    started, ended = stamp(attempt['started_at']), stamp(failed['at'])
    collection = read(archive/'collection-attempt.json', authority=False)
    if (set(collection) != {*bindings, 'started_at', 'maximum_queries', 'evidence_sha256'}
            or any(collection[k] != v for k, v in bindings.items())
            or collection['maximum_queries'] != len(symbols)
            or not started <= stamp(collection['started_at']) <= ended):
        raise ValueError('ETF_REPAIR_ORIGINAL_COLLECTION_INVALID')
    paths = receipts(archive/'source-receipts')
    if len(paths) != count:
        raise ValueError('ETF_REPAIR_ORIGINAL_QUERY_COUNT_UNCERTAIN')
    captures, hashes = [], {'collection-attempt.json': file_hash(archive/'collection-attempt.json')}
    store = UnifiedBatchStore(archive/'capture-store')
    for index, path in enumerate(paths):
        receipt = read(path)
        rows = store.read_captures(receipt['raw'])
        if receipt['diagnostic_id'] != identity or len(rows) != 1:
            raise ValueError('ETF_REPAIR_ORIGINAL_RECEIPT_INVALID')
        healthy(rows[0], symbols[index], day, cal)
        if not started <= stamp(rows[0]['received_at']) <= ended:
            raise ValueError('ETF_REPAIR_ORIGINAL_SOURCE_CLOCK_INVALID')
        captures.extend(rows)
        hashes['source-receipts/'+path.name] = file_hash(path)
    return captures, hashes


def validate_manifest(value: dict[str, Any], revision: str) -> None:
    checked_document(value)
    if (set(value) != {*AUTHORITY, 'schema_version', 'revision', 'files', 'evidence_sha256'}
            or value['schema_version'] != 'qtrade.etf-repair-source-manifest.v1'
            or value['revision'] != revision or not isinstance(value['files'], dict)
            or not 1 <= len(value['files']) <= 20_000
            or 'configs/observer_calendar_2026.json' not in value['files']):
        raise ValueError('ETF_REPAIR_SOURCE_MANIFEST_INVALID')
    for name, digest in value['files'].items():
        if (not isinstance(name, str) or not isinstance(digest, str)
                or re.fullmatch('[a-f0-9]{40}', digest) is None
                or name != 'configs/observer_calendar_2026.json' and re.fullmatch(
                    r'(?:src|scripts)/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+\.py', name) is None):
            raise ValueError('ETF_REPAIR_SOURCE_MANIFEST_PATH_INVALID')


def _verify_source_files(root: Path, files: dict[str, str]) -> None:
    actual = {str(path.relative_to(root)).replace('\\', '/')
        for directory in ('src', 'scripts') for path in (root/directory).rglob('*.py')}
    expected = set(files)-{'configs/observer_calendar_2026.json'}
    if actual != expected:
        raise ValueError('ETF_REPAIR_SOURCE_INVENTORY_CHANGED')
    for name, digest in files.items():
        path = root/name
        if (not path.resolve().is_relative_to(root) or not path.is_file()
                or any(parent.is_symlink() for parent in (path, *path.parents)
                       if parent != root and parent.is_relative_to(root))
                or path.stat().st_size > 4_000_000):
            raise ValueError('ETF_REPAIR_SOURCE_PATH_INVALID')
        payload = path.read_bytes()
        actual_digest = sha1(b'blob '+str(len(payload)).encode()+b'\0'+payload).hexdigest()
        if actual_digest != digest:
            raise ValueError('ETF_REPAIR_SOURCE_BLOB_MISMATCH')


def source_provenance(revision: str, manifest_path: Path | None) -> dict[str, Any]:
    if manifest_path is None:
        return {'mode': 'git', 'revision': revision}
    value = read(manifest_path, 4_000_000)
    validate_manifest(value, revision)
    root = REPO.resolve()
    if root.name != revision:
        raise ValueError('ETF_REPAIR_RELEASE_DIRECTORY_REVISION_INVALID')
    _verify_source_files(root, value['files'])
    return {'mode': 'manifest', 'revision': revision,
            'manifest_file_sha256': file_hash(manifest_path), 'manifest': value}


def plan(base: Path, local: Path, day: str, revision: str, *,
         source_manifest: Path | None = None) -> dict[str, Any]:
    """Read-only: no directories, leases, source requests or mutable state writes."""
    if os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
        raise ValueError('ETF_REPAIR_LIVE_MUST_REMAIN_DISABLED')
    if re.fullmatch('[a-f0-9]{40}', revision) is None:
        raise ValueError('ETF_REPAIR_REVISION_INVALID')
    provenance = source_provenance(revision, source_manifest)
    cal = calendar()
    if day != cal.expected(utc_now(), 'daily').date().isoformat():
        raise ValueError('ETF_REPAIR_CURRENT_COMPLETED_DAY_REQUIRED')
    job = local/'days'/day
    documents = {key: read(job/(key+'.json')) for key in ('request', 'attempt', 'failed')}
    master = read(job/'master.json', 16_000_000)
    request = documents['request']
    old = base/'online-research/etf-trend'/checked_identity(request['diagnostic_id'])
    captures, hashes = origin(old, documents, master, cal)
    if request['required_day'] != day or request['source_revision'] == revision:
        raise ValueError('ETF_REPAIR_NEW_REVISION_REQUIRED')
    bindings = {**request['bindings'], 'code_revision': revision}
    identity = signed(bindings)['evidence_sha256']
    new = old.parent/identity
    if (new.exists() or (old/'repair-resolution.json').exists()
            or (old/'repair-claim.json').exists()):
        raise ValueError('ETF_REPAIR_ALREADY_ATTEMPTED_REVIEW_REQUIRED')
    pointer = read(old.parent/'latest.json', 16_000)
    previous = verify_etf_archive(old.parent/checked_identity(pointer['diagnostic_id']), cal)
    if previous['as_of'] >= day:
        raise ValueError('ETF_REPAIR_POINTER_NOT_PREVIOUS_SESSION')
    return signed({**AUTHORITY, 'schema_version': SCHEMA, 'day': day,
        'original_diagnostic_id': old.name, 'repair_diagnostic_id': identity,
        'original_revision': request['source_revision'], 'repair_revision': revision,
        'source_verification': provenance,
        'bindings': bindings, 'documents': documents,
        'original_file_hashes': {key+'.json': file_hash(job/(key+'.json'))
            for key in ('request', 'attempt', 'failed', 'master')},
        'archive_file_hashes': hashes, 'expected_latest': pointer,
        'expected_latest_file_sha256': file_hash(old.parent/'latest.json'),
        'symbols': request['symbols'], 'reused_symbols': request['symbols'][:len(captures)],
        'remaining_symbols': request['symbols'][len(captures):],
        'maximum_total_queries': len(request['symbols']),
        'maximum_additional_queries': len(request['symbols'])-len(captures)})


def _check_revision(revision: str) -> None:
    # A caller cannot label arbitrary working bytes as the reviewed repair revision.
    import subprocess

    actual = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=REPO, check=True,
                            capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'],
                           cwd=REPO, check=True, capture_output=True, text=True).stdout
    if actual != revision or dirty.strip():
        raise ValueError('ETF_REPAIR_RUNNING_REVISION_NOT_CLEAN')
    inventory = subprocess.run(['git', 'ls-tree', '-r', '-z', 'HEAD', '--', 'src', 'scripts',
        'configs/observer_calendar_2026.json'], cwd=REPO, check=True, capture_output=True)
    files = {}
    for row in inventory.stdout.decode('utf-8').split('\0'):
        if not row:
            continue
        metadata, name = row.split('\t', 1)
        if name.endswith('.py') or name == 'configs/observer_calendar_2026.json':
            files[name] = metadata.split()[2]
    required = {'src/qtrade/research/etf_attempt_repair.py',
        'scripts/repair_etf_daily_attempt.py', 'scripts/run_etf_shadow_service.py',
        'configs/observer_calendar_2026.json'}
    if not required <= set(files):
        raise ValueError('ETF_REPAIR_REQUIRED_CODE_NOT_TRACKED')
    _verify_source_files(REPO.resolve(), files)


def _require_linux() -> None:
    if os.name != 'posix':
        raise ValueError('ETF_REPAIR_LINUX_DURABILITY_REQUIRED')


def _copy_origin(source: Path, destination: Path) -> None:
    original_bytes = source.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb') as handle:
        handle.write(original_bytes)
        handle.flush()
        os.fsync(handle.fileno())
    directory_fd = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _durable_directory(path: Path) -> None:
    """Persist every newly created directory entry up to an existing ancestor."""
    if path.is_symlink():
        raise ValueError('ETF_REPAIR_DIRECTORY_SYMLINK')
    if path.exists():
        if not path.is_dir():
            raise ValueError('ETF_REPAIR_DIRECTORY_INVALID')
        return
    _durable_directory(path.parent)
    path.mkdir()
    for directory in (path, path.parent):
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def apply(base: Path, local: Path, reviewed: dict[str, Any], approved_sha256: str,
          *, deadline_seconds: int = MAX_SECONDS,
          source_manifest: Path | None = None) -> dict[str, Any]:
    """One explicit burst. Any interruption leaves durable, non-retryable evidence."""
    checked_document(reviewed)
    if (reviewed['schema_version'] != SCHEMA
            or reviewed['evidence_sha256'] != checked_identity(approved_sha256)
            or type(deadline_seconds) is not int or not 1 <= deadline_seconds <= MAX_SECONDS
            or os.environ.get('QTRADE_AKSHARE_NONCOMMERCIAL_ACK', '').lower() != 'true'):
        raise ValueError('ETF_REPAIR_REVIEW_OR_CONFIGURATION_INVALID')
    if source_manifest is None:
        _check_revision(reviewed['repair_revision'])
    if source_provenance(reviewed['repair_revision'], source_manifest) != reviewed[
            'source_verification']:
        raise ValueError('ETF_REPAIR_SOURCE_PROVENANCE_CHANGED')
    _require_linux()
    with exclusive(local/'cycle.lock'):
        if plan(base, local, reviewed['day'], reviewed['repair_revision'],
                source_manifest=source_manifest) != reviewed:
            raise ValueError('ETF_REPAIR_PLAN_CHANGED')
        cal = calendar()
        if reason := source_gate(base, local, cal, reviewed['day'], utc_now(),
                                 collection_database=local.parent/'unified-data.sqlite'):
            raise ValueError(reason)
        archives = base/'online-research/etf-trend'
        old, new = (archives/reviewed[key] for key in (
            'original_diagnostic_id', 'repair_diagnostic_id'))
        repair = local/'repairs'/approved_sha256
        if repair.exists():
            raise ValueError('ETF_REPAIR_ALREADY_ATTEMPTED_REVIEW_REQUIRED')
        expires = utc_now()+timedelta(seconds=deadline_seconds)
        for directory in (repair, new/'origin', new/'capture-store/objects',
                          new/'repair-requests', new/'repair-receipts'):
            _durable_directory(directory)
        # One claim across ALL repair revisions prevents another explicit job from
        # spending the original request budget again after an uncertain interruption.
        write_json_immutable(old/'repair-claim.json', signed({**AUTHORITY,
            'plan_sha256': approved_sha256, 'repair_diagnostic_id': new.name}))
        write_json_immutable(repair/'started.json', signed({**AUTHORITY,
            'plan_sha256': approved_sha256, 'started_at': utc_now().isoformat(),
            'deadline_at': expires.isoformat(), 'automatic_retry': False}))
        write_json_immutable(new/'repair-plan.json', reviewed)
        if source_manifest is not None:
            _copy_origin(source_manifest, new/'source-manifest.json')
            if file_hash(new/'source-manifest.json') != reviewed['source_verification'][
                    'manifest_file_sha256']:
                raise ValueError('ETF_REPAIR_SOURCE_MANIFEST_CHANGED_DURING_COPY')
        job = local/'days'/reviewed['day']
        # Preserve byte-identical source document hashes, including the frozen master.
        for key in ('request', 'attempt', 'failed', 'master'):
            _copy_origin(job/(key+'.json'), new/'origin'/(key+'.json'))
        if reviewed['original_file_hashes'] != {
                key+'.json': file_hash(new/'origin'/(key+'.json'))
                for key in ('request', 'attempt', 'failed', 'master')}:
            raise ValueError('ETF_REPAIR_ORIGIN_CHANGED_DURING_COPY')
        master = read(new/'origin/master.json', 16_000_000)
        captures, _ = origin(old, reviewed['documents'], master, cal)
        store = UnifiedBatchStore(new/'capture-store')

        def alarm_handler(signum: int, frame: Any) -> None:
            raise TimeoutError('ETF_REPAIR_DEADLINE')

        previous_handler = signal.signal(signal.SIGALRM, alarm_handler)
        signal.alarm(deadline_seconds)
        try:
            for index, symbol in enumerate(reviewed['remaining_symbols'], 1):
                if utc_now() >= expires:
                    raise TimeoutError('ETF_REPAIR_DEADLINE')
                if reason := source_gate(base, local, cal, reviewed['day'], utc_now(),
                        collection_database=local.parent/'unified-data.sqlite'):
                    raise ValueError(reason)
                if utc_now() >= expires:
                    raise TimeoutError('ETF_REPAIR_DEADLINE')
                reservation = signed({**AUTHORITY, 'plan_sha256': approved_sha256,
                    'sequence': index, 'symbol': symbol, 'request': request_for(symbol),
                    'reserved_at': utc_now().isoformat()})
                write_json_immutable(new/'repair-requests'/f'{index:02d}.json', reservation)
                response = fetch('sina_klines', request_for(symbol))
                raw = store.capture([response])
                write_json_immutable(new/'repair-receipts'/f'{index:02d}.json', signed({
                    **AUTHORITY, 'plan_sha256': approved_sha256,
                    'reservation_sha256': reservation['evidence_sha256'], 'raw': raw}))
                if (response['status_code'] != 200 or response['transport_error'] is not None
                        or response['body_complete'] is not True):
                    write_json_atomic(local/'source-backoff.json', signed({**AUTHORITY,
                        'source': 'sina_klines', 'observed_at': utc_now().isoformat(),
                        'not_before': (utc_now()+timedelta(hours=6)).isoformat(),
                        'reason': 'ETF_SOURCE_RESPONSE_FAILED', 'diagnostic_id': new.name}))
                healthy(response, symbol, reviewed['day'], cal)
                if stamp(response['received_at']) < stamp(reservation['reserved_at']):
                    raise ValueError('ETF_REPAIR_RESPONSE_PRECEDES_RESERVATION')
                captures.append(response)
            if utc_now() >= expires:
                raise TimeoutError('ETF_REPAIR_DEADLINE')
            if reason := source_gate(base, local, cal, reviewed['day'], utc_now(),
                    collection_database=local.parent/'unified-data.sqlite'):
                raise ValueError(reason)
            raw = store.capture([{k: row[k] for k in CAPTURE_FIELDS} for row in captures])
            write_json_immutable(new/'receipt.json', signed({**AUTHORITY,
                'bindings': reviewed['bindings'], 'raw': raw,
                'repair_plan_sha256': approved_sha256}))
            diagnostic = admit(captures, reviewed['symbols'], cal,
                               date.fromisoformat(reviewed['day']), utc_now())
            write_json_immutable(new/'diagnostic.json', signed({**{
                k: v for k, v in diagnostic.items() if k != 'evidence_sha256'},
                'bindings': reviewed['bindings'], 'receipt_sha256': file_hash(new/'receipt.json')}))
            verified = verify_etf_archive(new, cal)
            completion = signed({**AUTHORITY, 'plan_sha256': approved_sha256,
                'original_diagnostic_id': old.name, 'repair_diagnostic_id': new.name,
                'original_revision': reviewed['original_revision'],
                'repair_revision': reviewed['repair_revision'],
                'receipt_sha256': file_hash(new/'receipt.json'),
                'diagnostic_sha256': verified['evidence_sha256'],
                'request_hashes': {p.name: file_hash(p) for p in receipts(new/'repair-requests')},
                'response_hashes': {p.name: file_hash(p) for p in receipts(new/'repair-receipts')},
                'completed_at': utc_now().isoformat()})
            write_json_immutable(new/'repair-completed.json', completion)
            resolution = signed({**AUTHORITY, 'schema_version': 'qtrade.etf-repair-resolution.v1',
                'original_diagnostic_id': old.name, 'repair_diagnostic_id': new.name,
                'plan_sha256': approved_sha256, 'completion_sha256': completion['evidence_sha256']})
            # Verify the prospective resolution before making it discoverable.
            verify_resolution(old, cal, resolution=resolution)
            latest = archives/'latest.json'
            if (read(latest, 16_000) != reviewed['expected_latest']
                    or file_hash(latest) != reviewed['expected_latest_file_sha256']):
                raise ValueError('ETF_REPAIR_PUBLICATION_CAS_CHANGED')
            write_json_atomic(latest, signed({**AUTHORITY, 'diagnostic_id': new.name}))
            write_json_immutable(repair/'completed.json', completion)
            # This is the final source-admission marker: incomplete publication,
            # including a successful diagnostic with failed completion/CAS, cannot
            # be consumed by the independent SHADOW service.
            write_json_immutable(old/'repair-resolution.json', resolution)
            return completion
        except BaseException as exc:
            code = str(exc)
            write_json_immutable(repair/'stopped.json', signed({**AUTHORITY,
                'plan_sha256': approved_sha256, 'stopped_at': utc_now().isoformat(),
                'reason': code if re.fullmatch('[A-Z][A-Z0-9_]{1,120}', code)
                else type(exc).__name__, 'automatic_retry': False}))
            raise
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous_handler)


def verify_resolution(old: Path, cal: Calendar, *,
                      resolution: dict[str, Any] | None = None) -> dict[str, Any]:
    """Only a complete, replayed, exact linked repair resolves an old partial."""
    resolution = resolution or read(old/'repair-resolution.json', 16_000)
    checked_document(resolution)
    new_id = checked_identity(resolution['repair_diagnostic_id'])
    if (set(resolution) != {*AUTHORITY, 'schema_version', 'original_diagnostic_id',
            'repair_diagnostic_id', 'plan_sha256', 'completion_sha256', 'evidence_sha256'}
            or resolution['schema_version'] != 'qtrade.etf-repair-resolution.v1'
            or resolution['original_diagnostic_id'] != old.name or new_id == old.name):
        raise ValueError('ETF_REPAIR_RESOLUTION_INVALID')
    new = old.parent/new_id
    reviewed = read(new/'repair-plan.json')
    completion = read(new/'repair-completed.json')
    provenance = reviewed['source_verification']
    if provenance['mode'] == 'manifest':
        validate_manifest(provenance['manifest'], reviewed['repair_revision'])
        if (read(new/'source-manifest.json', 4_000_000) != provenance['manifest']
                or file_hash(new/'source-manifest.json') != provenance['manifest_file_sha256']):
            raise ValueError('ETF_REPAIR_ARCHIVED_MANIFEST_CHANGED')
    elif provenance != {'mode': 'git', 'revision': reviewed['repair_revision']}:
        raise ValueError('ETF_REPAIR_SOURCE_PROVENANCE_INVALID')
    if provenance['revision'] != reviewed['repair_revision']:
        raise ValueError('ETF_REPAIR_SOURCE_PROVENANCE_REVISION_INVALID')
    if (read(old/'repair-claim.json') != signed({**AUTHORITY,
            'plan_sha256': reviewed['evidence_sha256'], 'repair_diagnostic_id': new_id})
            or new.is_symlink() or reviewed['schema_version'] != SCHEMA
            or reviewed['original_diagnostic_id'] != old.name
            or reviewed['repair_diagnostic_id'] != new_id
            or reviewed['evidence_sha256'] != resolution['plan_sha256']
            or completion['evidence_sha256'] != resolution['completion_sha256']
            or completion['plan_sha256'] != reviewed['evidence_sha256']
            or completion['original_diagnostic_id'] != old.name
            or completion['repair_diagnostic_id'] != new_id
            or completion['original_revision'] != reviewed['original_revision']
            or completion['repair_revision'] != reviewed['repair_revision']
            or reviewed['repair_revision'] == reviewed['original_revision']):
        raise ValueError('ETF_REPAIR_COMPLETION_CONTEXT_INVALID')
    documents = {key: read(new/'origin'/(key+'.json'))
                 for key in ('request', 'attempt', 'failed')}
    master = read(new/'origin/master.json', 16_000_000)
    if (documents != reviewed['documents'] or reviewed['original_file_hashes'] != {
            key+'.json': file_hash(new/'origin'/(key+'.json'))
            for key in ('request', 'attempt', 'failed', 'master')}):
        raise ValueError('ETF_REPAIR_ORIGIN_DOCUMENTS_CHANGED')
    captures, hashes = origin(old, documents, master, cal)
    symbols = documents['request']['symbols']
    remaining = symbols[len(captures):]
    if (hashes != reviewed['archive_file_hashes'] or reviewed['symbols'] != symbols
            or reviewed['reused_symbols'] != symbols[:len(captures)]
            or reviewed['remaining_symbols'] != remaining
            or reviewed['maximum_total_queries'] != len(symbols)
            or reviewed['maximum_additional_queries'] != len(remaining)
            or reviewed['bindings'] != {**documents['request']['bindings'],
                                        'code_revision': reviewed['repair_revision']}
            or signed(reviewed['bindings'])['evidence_sha256'] != new_id):
        raise ValueError('ETF_REPAIR_BUDGET_OR_BINDINGS_CHANGED')
    requests, responses = receipts(new/'repair-requests'), receipts(new/'repair-receipts')
    if (len(requests) != len(remaining) or len(responses) != len(remaining)
            or completion['request_hashes'] != {p.name: file_hash(p) for p in requests}
            or completion['response_hashes'] != {p.name: file_hash(p) for p in responses}):
        raise ValueError('ETF_REPAIR_REQUEST_BUDGET_OR_COMPLETION_INVALID')
    completed = stamp(completion['completed_at'])
    store = UnifiedBatchStore(new/'capture-store')
    for index, (request_path, response_path, symbol) in enumerate(
            zip(requests, responses, remaining, strict=True), 1):
        request, response = read(request_path), read(response_path)
        rows = store.read_captures(response['raw'])
        if (request['plan_sha256'] != reviewed['evidence_sha256']
                or request['sequence'] != index or request['symbol'] != symbol
                or request['request'] != request_for(symbol)
                or response['plan_sha256'] != reviewed['evidence_sha256']
                or response['reservation_sha256'] != request['evidence_sha256']
                or len(rows) != 1):
            raise ValueError('ETF_REPAIR_REQUEST_BINDING_INVALID')
        healthy(rows[0], symbol, reviewed['day'], cal)
        if not stamp(request['reserved_at']) <= stamp(rows[0]['received_at']) <= completed:
            raise ValueError('ETF_REPAIR_REQUEST_CLOCK_INVALID')
        captures.extend(rows)
    diagnostic = verify_etf_archive(new, cal)
    receipt = read(new/'receipt.json')
    if (completion['receipt_sha256'] != file_hash(new/'receipt.json')
            or completion['diagnostic_sha256'] != diagnostic['evidence_sha256']
            or receipt['repair_plan_sha256'] != reviewed['evidence_sha256']
            or receipt['bindings'] != reviewed['bindings']
            or store.read_captures(receipt['raw']) != captures):
        raise ValueError('ETF_REPAIR_AGGREGATE_LINEAGE_INVALID')
    return diagnostic


def require_resolved_archive(root: Path, cal: Calendar) -> dict[str, Any] | None:
    """A repaired diagnostic is never a source before its final resolution."""
    receipt_path = root/'receipt.json'
    is_repair = (root/'repair-plan.json').exists()
    if not is_repair and receipt_path.exists():
        # Ordinary source corruption is diagnosed by its existing raw verifier;
        # this probe only detects repair provenance when the plan was removed.
        try:
            if not receipt_path.is_symlink() and receipt_path.stat().st_size <= 300_000:
                receipt = json.loads(receipt_path.read_bytes())
                is_repair = isinstance(receipt, dict) and 'repair_plan_sha256' in receipt
        except (OSError, ValueError):
            pass
    if not is_repair:
        return None
    reviewed = read(root/'repair-plan.json')
    original_id = checked_identity(reviewed['original_diagnostic_id'])
    if (reviewed['repair_diagnostic_id'] != root.name or original_id == root.name
            or reviewed['schema_version'] != SCHEMA):
        raise ValueError('ETF_REPAIR_SOURCE_CONTEXT_INVALID')
    original = root.parent/original_id
    resolution = read(original/'repair-resolution.json', 16_000)
    if resolution['repair_diagnostic_id'] != root.name:
        raise ValueError('ETF_REPAIR_SOURCE_RESOLUTION_CHANGED')
    value = verify_resolution(original, cal)
    if signed(value['bindings'])['evidence_sha256'] != root.name:
        raise ValueError('ETF_REPAIR_SOURCE_IDENTITY_CHANGED')
    return value
