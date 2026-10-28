"""Build, independently reload and atomically admit a new immutable day Provider."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_quality_policy import request_policy
from qtrade.research.data_versions import (
    SCHEMA,
    DataVersionCatalog,
    content_id,
    file_hash,
    verify_version,
)
from qtrade.research.qlib_adapter import positive
from qtrade.research.source_constraints import capture_source
from scripts.atomic_json import write_json_atomic, write_json_immutable
from scripts.pit_collection_queue import read_attempts
from scripts.prepare_qlib_incremental import fetch_fixed, load_signed
from scripts.run_qlib_baseline import make_provider, make_supported_universe
from scripts.unified_store import UnifiedBatchStore

REPO = Path(__file__).resolve().parents[1]
BENCHMARK_URL = ('https://quotes.sina.cn/cn/api/json_v2.php/'
                 'CN_MarketDataService.getKLineData?symbol=sh000300&scale=240&ma=no&datalen=2')


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def move_durable(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)
    sync_directory(source.parent)
    sync_directory(target.parent)


def preserve_incomplete_stage(stage: Path) -> None:
    # dataset.json is the last initialization write. Retain interrupted attempts
    # outside the new version inventory instead of overwriting captured evidence.
    if stage.exists() and not (stage/'dataset.json').exists():
        move_durable(stage, stage.parent/'incomplete'/(stage.name+'-'+uuid4().hex))


def publish_sealed(stage: Path, archive: Path, database: Path, build_id: str,
                   binding: dict[str, Any]) -> dict[str, Any]:
    value = verify_version(stage)
    if value['bindings'] != binding:
        raise ValueError('PROVIDER_SEALED_BUILD_BINDING_CHANGED')
    identity = value['version_id']
    receipt = signed({**AUTHORITY, 'build_id': build_id, 'version_id': identity,
                      'manifest_sha256': value['evidence_sha256']})
    receipt_path = archive/'build-receipts'/(build_id+'.json')
    if receipt_path.exists():
        if load_signed(receipt_path) != receipt:
            raise ValueError('PROVIDER_SEALED_BUILD_RECEIPT_CHANGED')
    else:
        write_json_immutable(receipt_path, receipt)
    target = archive/'versions'/identity
    if target.exists():
        if verify_version(target)['evidence_sha256'] != value['evidence_sha256']:
            raise ValueError('PROVIDER_IMMUTABLE_VERSION_CONFLICT')
    else:
        move_durable(stage, target)
    return publish_head(DataVersionCatalog(database, archive), archive, identity)


def append_benchmark(
    frozen: list[dict[str, Any]], body: bytes, *, end: str,
) -> list[dict[str, Any]]:
    if str(date.fromisoformat(end)) != end:
        raise ValueError('FORWARD_BENCHMARK_DATE_INVALID')
    if not frozen or len(body) > 2_000_000 or str(frozen[-1]['date'])[:10] >= end:
        raise ValueError('FORWARD_BENCHMARK_SCOPE_INVALID')
    values = json.loads(body)
    if not isinstance(values, list) or not 1 <= len(values) <= 2:
        raise ValueError('FORWARD_BENCHMARK_RESPONSE_INVALID')
    mapping = {r['day']: r for r in values}
    if len(mapping) != len(values) or set(mapping) != {str(frozen[-1]['date'])[:10], end}:
        raise ValueError('FORWARD_BENCHMARK_OVERLAP_OR_CURRENT_DAY_MISSING')
    old = mapping[str(frozen[-1]['date'])[:10]]
    for key in ('open', 'high', 'low', 'close'):
        if abs(positive(old[key])-positive(frozen[-1][key])) > Decimal('0.011'):
            raise ValueError('FORWARD_BENCHMARK_PRICE_BASIS_CHANGED')
    if positive(old['volume']) != positive(frozen[-1]['volume']):
        raise ValueError('FORWARD_BENCHMARK_VOLUME_BASIS_CHANGED')
    current = {k: float(positive(mapping[end][k]))
               for k in ('open', 'high', 'low', 'close', 'volume')}
    if (current['high'] < max(current['open'], current['close'])
            or current['low'] > min(current['open'], current['close'])):
        raise ValueError('FORWARD_BENCHMARK_OHLC_INVALID')
    return [dict(r) for r in frozen] + [{'date': end, **current, 'amount': None}]


def link_verified(source: Path, target: Path, digest: str) -> None:
    if not source.is_file() or source.is_symlink() or file_hash(source) != digest:
        raise ValueError('PROVIDER_SOURCE_FILE_HASH_MISMATCH')
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        os.link(source, target)
    if target.is_symlink() or file_hash(target) != digest:
        raise ValueError('PROVIDER_STAGED_FILE_CONFLICT')


def copy_dataset(
    previous: Path, preparation: Path, stage: Path, *, request: dict[str, Any],
    ready_symbols: set[str] | None = None,
) -> dict[str, Any]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    base = load_signed(previous/'dataset.json')
    if base['evidence_sha256'] != request['base_dataset_sha256']:
        raise ValueError('PROVIDER_BASE_DATASET_IDENTITY_CHANGED')
    stocks = []
    ready = set(request['batches']) if ready_symbols is None else ready_symbols
    for original in base['stocks']:
        symbol = original['symbol']
        folder = stage/'stocks'/symbol
        if symbol not in ready:
            row = original
            source = previous/'stocks'/symbol
            for name, digest in row['files'].items():
                if re.fullmatch(r'[A-Za-z0-9._-]+', name) is None:
                    raise ValueError('PROVIDER_SOURCE_FILE_NAME_INVALID')
                link_verified(source/name, folder/name, digest)
        else:
            source = preparation/'stocks'/symbol
            prepared = load_signed(source/'manifest.json')
            if (prepared['base_stock_sha256'] != original['evidence_sha256']
                    or prepared['end'] != request['day']
                    or prepared['normalization']['frozen_prefix_unchanged'] is not True):
                raise ValueError('PREPARED_PARTITION_IDENTITY_OR_END_CHANGED')
            for name, digest in prepared['files'].items():
                if (not re.fullmatch(r'[A-Za-z0-9._-]+', name)
                        or file_hash(source/name) != digest):
                    raise ValueError('PREPARED_PARTITION_FILE_HASH_MISMATCH')
            frozen_file = previous/'stocks'/symbol/'daily.parquet'
            if file_hash(frozen_file) != original['files']['daily.parquet']:
                raise ValueError('PROVIDER_FROZEN_PREFIX_HASH_CHANGED')
            frozen = pq.read_table(frozen_file).to_pylist()
            updated = pq.read_table(source/prepared['daily_file']).to_pylist()
            if updated[:len(frozen)] != frozen or len(updated) != len(frozen)+1:
                raise ValueError('PROVIDER_FROZEN_PREFIX_CONTENT_CHANGED')
            selected = {name: digest for name, digest in original['files'].items()
                        if name != 'daily.parquet'}
            factor_file = prepared['factor_capture_file']
            selected[factor_file] = prepared['files'][factor_file]
            selected['daily.parquet'] = prepared['files'][prepared['daily_file']]
            for name, digest in selected.items():
                path = source/(prepared['daily_file'] if name == 'daily.parquet' else name)
                link_verified(path, folder/name, digest)
            row = signed({
                'symbol': symbol, 'name': original['name'], 'rows': len(updated),
                'start': original['start'], 'end': prepared['end'], 'files': selected,
                'base_stock_sha256': original['evidence_sha256'],
                'preparation_sha256': prepared['evidence_sha256'],
                'batch_sha256': prepared['batch_sha256'],
                'normalization': prepared['normalization'], 'observed_at': prepared['observed_at'],
            })
        if not (folder/'manifest.json').exists():
            write_json_immutable(folder/'manifest.json', row)
        elif load_signed(folder/'manifest.json') != row:
            raise ValueError('PROVIDER_STAGED_STOCK_MANIFEST_CHANGED')
        stocks.append(row)
    current = sorted(ready)
    return signed({
        **{k: v for k, v in base.items() if k not in {'evidence_sha256', 'stocks', 'failures'}},
        'created_at': datetime.now(UTC).isoformat(), 'end': request['day'],
        'stocks': stocks, 'rows': sum(r['rows'] for r in stocks),
        'requested_symbols': len(stocks), 'accepted_symbols': len(stocks),
        'failures': [], 'parent_failures': base.get('failures', []),
        'parent_dataset_sha256': base['evidence_sha256'],
        'ancestor_dataset_sha256': list(dict.fromkeys([
            base['evidence_sha256'], *base.get('ancestor_dataset_sha256', []),
            *([base['parent_dataset_sha256']] if 'parent_dataset_sha256' in base else []),
        ])),
        'incremental': {
            'preparation_request_sha256': request['evidence_sha256'], 'current_symbols': current,
            'blocked_symbols': sorted({r['symbol'] for r in stocks}-set(current)),
            'observation_universe_version': request['universe_version'],
            'scope': 'FROZEN_MODEL_SAMPLE_NOT_FULL_OBSERVATION_UNIVERSE_OR_PIT',
        },
        'limitations': base['limitations'] + [
            'Current unknown daily inputs retain history but have no current price or signal',
            'New same-vendor overlap/HFQ check is not independent corporate-action certification',
            'New benchmark turnover unavailable; price-index volume only, not stock liquidity',
        ],
    })


def publish_head(catalog: DataVersionCatalog, archive: Path, identity: str) -> dict[str, Any]:
    catalog.admit(identity, now=datetime.now(UTC))
    # A replay must not undo an explicit rollback by exporting the replayed ID.
    admitted = catalog.head()
    if admitted is None:
        raise ValueError('PROVIDER_ADMITTED_HEAD_MISSING')
    summary = signed({
        **AUTHORITY, 'version_id': admitted['version_id'], 'actual_end': admitted['actual_end'],
        'state': admitted['state'], 'manifest_sha256': admitted['evidence_sha256'],
        'current_symbols': len(admitted['current_symbols']),
        'historical_symbols': len(admitted['historical_symbols']),
        'blocked_symbols': admitted['blocked_symbols'], 'model_activated': False,
    })
    write_json_atomic(archive/'latest.json', summary)
    return summary


def build(
    base: Path, previous: Path, preparation: Path, database: Path, *, revision: str,
    qlib_python: Path,
) -> dict[str, Any]:
    if re.fullmatch(r'[a-f0-9]{40}', revision) is None:
        raise ValueError('PROVIDER_FULL_SOURCE_REVISION_REQUIRED')
    if shutil.disk_usage(base).free < 100*1024**3:
        raise RuntimeError('PERSISTENT_STORAGE_RESERVE_100_GIB')
    request = load_signed(preparation/'request.json')
    if (preparation.name != request['evidence_sha256'] or not request['batches']
            or any(request.get(k) != v for k, v in AUTHORITY.items())):
        raise ValueError('PROVIDER_PREPARATION_REQUEST_INVALID')
    policy = (request_policy(request, load_signed(previous/'dataset.json'), datetime.now(UTC))
              if {'input_scope_policy', 'input_scope_policy_sha256'} & request.keys() else None)
    ready = {s for s in request['batches']
             if (preparation/'stocks'/s/'manifest.json').is_file()}
    missing = set(request['batches'])-ready
    ledger_input = {'request_sha256': request['evidence_sha256'],
                    'stocks': [{'symbol': s} for s in request['batches']]}
    attempts = read_attempts(preparation, ledger_input, datetime.now(UTC))
    pending = sorted(s for s in missing if attempts.get(s, {}).get('state') != 'FAILED')
    if pending or not ready:
        return {**AUTHORITY, 'state': 'BLOCKED_INCOMPLETE_PREPARATION',
                'missing_symbols': pending, 'provider_published': False}
    quarantined = {s: {k: attempts[s].get(k) for k in (
        'state', 'attempts', 'attempted_at', 'next_attempt_at', 'error_type',
    )} for s in sorted(missing)}
    calendar = Calendar(REPO/'configs/observer_calendar_2026.json')
    if (not calendar.is_open(date.fromisoformat(request['day']))
            or request['day'] > str(calendar.expected(datetime.now(UTC), 'daily').date())):
        raise ValueError('PROVIDER_CURRENT_DAY_NOT_COMPLETE')
    store = UnifiedBatchStore(base/'unified-data/lake')
    batches = {}
    for symbol in sorted(ready):
        batch_id = request['batches'][symbol]
        if batch_id not in batches:
            batches[batch_id] = store.load(batch_id)
        batch = batches[batch_id]
        prepared = load_signed(preparation/'stocks'/symbol/'manifest.json')
        if (prepared['batch_sha256'] != batch['evidence_sha256']
                or batch['spec']['frequency'] != 'day'
                or batch['spec']['universe_version'] != request['universe_version']
                or str(datetime.fromisoformat(batch['spec']['target_end']).date()) != (
                    request['day'])):
            raise ValueError('PROVIDER_PREPARATION_BATCH_LINEAGE_CHANGED')
    snapshot = capture_source(base/'pit-repair')
    benchmark_body = fetch_fixed(BENCHMARK_URL)
    heads = {s: file_hash(preparation/'stocks'/s/'manifest.json') for s in sorted(ready)}
    binding = {
        'base_dataset_sha256': request['base_dataset_sha256'],
        'preparation_sha256': request['evidence_sha256'], 'prepared_heads': heads,
        'quarantined_current_inputs': quarantined,
        'missing_input_policy': 'RETAIN_HISTORY_EXCLUDE_CURRENT_PRICE_AND_SIGNAL',
        'benchmark_body_sha256': sha256(benchmark_body).hexdigest(),
        'source_snapshot_sha256': snapshot.identity,
        'observation_universe_version': request['universe_version'], 'code_revision': revision,
    }
    if policy is not None:
        binding['input_scope_policy_sha256'] = policy['evidence_sha256']
    archive = base/'unified-data/qlib-versions'
    build_id = signed(binding)['evidence_sha256']
    stage = archive/'staging'/build_id
    receipt_path = archive/'build-receipts'/(build_id+'.json')
    if receipt_path.exists():
        receipt = load_signed(receipt_path)
        identity = receipt['version_id']
        target = archive/'versions'/identity
        if not target.exists():
            value = verify_version(stage)
            if value['version_id'] != identity:
                raise ValueError('PROVIDER_SEALED_BUILD_RECEIPT_CHANGED')
            move_durable(stage, target)
        if verify_version(target)['evidence_sha256'] != receipt['manifest_sha256']:
            raise ValueError('PROVIDER_EXISTING_BUILD_RECEIPT_CHANGED')
        return publish_head(DataVersionCatalog(database, archive), archive, identity)
    if (stage/'version.json').exists():
        return publish_sealed(stage, archive, database, build_id, binding)
    preserve_incomplete_stage(stage)
    stage.mkdir(parents=True, exist_ok=True)
    dataset_file = stage/'dataset.json'
    if not dataset_file.exists():
        dataset = copy_dataset(previous, preparation, stage, request=request, ready_symbols=ready)
        old_benchmark = previous/'benchmark/raw.json'
        old = load_signed(previous/'dataset.json')
        if file_hash(old_benchmark) != old['benchmark_sha256']:
            raise ValueError('PROVIDER_OLD_BENCHMARK_HASH_CHANGED')
        rows = append_benchmark(json.loads(old_benchmark.read_bytes())['rows'], benchmark_body,
                                end=request['day'])
        write_json_immutable(stage/'benchmark/raw.json', {'rows': rows})
        write_json_immutable(stage/'benchmark/forward-source.json', signed({
            'source': 'Sina sh000300 bounded daily index', 'url': BENCHMARK_URL,
            'received_at': datetime.now(UTC).isoformat(), 'historical_available_at': None,
            'body_base64': base64.b64encode(benchmark_body).decode(),
            'body_sha256': binding['benchmark_body_sha256'], 'independent_source_reconciled': False,
        }))
        dataset = signed({**{k: v for k, v in dataset.items() if k != 'evidence_sha256'},
                          'benchmark_sha256': file_hash(stage/'benchmark/raw.json')})
        write_json_immutable(stage/'preparation-request.json', request)
        write_json_immutable(stage/'quarantined-current-inputs.json', signed({
            **AUTHORITY, 'preparation_request_sha256': request['evidence_sha256'],
            'quarantined': quarantined, 'policy': binding['missing_input_policy'],
        }))
        snapshot.freeze(stage/'pit-sources')
        # Initialization completion marker, after all immutable input evidence.
        write_json_immutable(dataset_file, dataset)
    else:
        dataset = load_signed(dataset_file)
        if (load_signed(stage/'preparation-request.json') != request
                or capture_source(stage/'pit-sources').identity != snapshot.identity):
            raise ValueError('PROVIDER_STAGING_SOURCE_IDENTITY_CHANGED')
    if not (stage/'provider-reload.json').exists():
        make_provider(stage, stage/'provider', source_snapshot=snapshot)
        make_supported_universe(stage, stage/'provider', dataset)
        with (stage/'provider-reload.log').open('w') as log:
            subprocess.run([str(qlib_python), '-m', 'scripts.reload_qlib_provider',
                            '--root', str(stage)], cwd=REPO, env=os.environ, stdout=log,
                           stderr=subprocess.STDOUT, timeout=300, check=True)
    files = {p.relative_to(stage).as_posix(): file_hash(p)
             for p in sorted(stage.rglob('*')) if p.is_file() and p.name != 'version.json'}
    for name in files:
        with (stage/name).open('rb') as handle:
            os.fsync(handle.fileno())
    for folder in sorted((p for p in stage.rglob('*') if p.is_dir()),
                          key=lambda p: len(p.parts), reverse=True):
        sync_directory(folder)
    sync_directory(stage)
    identity = content_id(files, binding)
    value = signed({
        **AUTHORITY, 'schema_version': SCHEMA, 'version_id': identity, 'frequency': 'day',
        'state': 'RESEARCH_READY_QUALIFIED_SUBSET', 'created_at': datetime.now(UTC).isoformat(),
        'actual_end': request['day'], 'current_symbols': dataset['incremental']['current_symbols'],
        'historical_symbols': sorted(r['symbol'] for r in dataset['stocks']),
        'blocked_symbols': dataset['incremental']['blocked_symbols'],
        'calendar_boundaries': [d for d in (stage/'provider/calendars/day.txt').read_text(
        ).splitlines() if d > request['day']], 'files': files, 'bindings': binding,
        'historical_pit_certified': False, 'external_execution_ready': False,
    })
    if not (stage/'version.json').exists():
        write_json_immutable(stage/'version.json', value)
    else:
        value = load_signed(stage/'version.json')
    return publish_sealed(stage, archive, database, build_id, binding)


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ('base', 'previous', 'preparation', 'database', 'qlib-python'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    args = parser.parse_args()
    if os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
        raise RuntimeError('LIVE_TRADING_MUST_REMAIN_DISABLED')
    import fcntl

    lease_path = Path('/root/qtrade-local/qlib-provider-build.lock')
    lease_path.parent.mkdir(parents=True, exist_ok=True)
    with lease_path.open('a') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(build(args.base, args.previous, args.preparation, args.database,
                               revision=args.source_revision, qlib_python=args.qlib_python)))


if __name__ == '__main__':
    main()
