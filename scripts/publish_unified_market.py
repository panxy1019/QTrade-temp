"""Export recent accepted collection chunks without claiming the collector's writer lease."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from datetime import UTC, datetime
from decimal import Context, localcontext
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal, cast
from uuid import uuid4

from qtrade.api.unified_data import validate_publication
from qtrade.data.storage import _fsync_directory
from qtrade.market_data.akshare import SHANGHAI
from qtrade.market_data.sina_incremental import (
    PRICE_BASIS,
    completed_klines,
    completed_quotes,
    reconcile_day,
)
from qtrade.market_data.sina_minute import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_versions import file_hash
from qtrade.research.unified_market import (
    BAR_FIELDS,
    LIMITATIONS,
    MAX_TARGETS,
    TOTAL_LIMIT,
    encoded,
    sessions,
    validate_bar,
    validate_index,
    validate_shard,
)
from scripts.atomic_json import write_json_atomic
from scripts.unified_store import UnifiedBatchStore, _BatchMaterial

REPO = Path(__file__).resolve().parents[1]
MAX_BATCHES = 12_000
# Each immutable batch admits at most 50 symbols, and the ledger key is
# (batch_id, symbol). Bound metadata independently of source/cache/time budgets.
MAX_CHUNKS = MAX_BATCHES*50
SOURCE_BYTE_BUDGET = 512_000_000
MAX_CACHE_ROWS = 100_000
MAX_CACHE_ESTIMATED_BYTES = 128_000_000
_CACHE: dict[tuple[str, ...], tuple[dict[str, Any], list[dict[str, Any]]]] = {}
_CACHE_SIZES: dict[tuple[str, ...], int] = {}


def _write_object(path: Path, raw: bytes) -> None:
    """Persist already encoded bytes with the existing immutable writer's durability contract."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix='.'+path.name+'.', suffix='.tmp',
                                delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        temporary.unlink()
        _fsync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _replay(material: _BatchMaterial, calendar: Calendar, kinds: dict[str, str]) -> None:
    """Reproduce collector admission from actual receipts before caching normalized rows."""
    manifest, captures, rows = material.validate()
    spec = manifest['spec']
    target = datetime.fromisoformat(spec['target_end']).astimezone(SHANGHAI)
    completed = datetime.fromisoformat(manifest['completed_at'])
    frequency = cast(Literal['day', '5min'], spec['frequency'])
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_symbol[row['symbol']].append(row)
    for symbol, actual in by_symbol.items():
        code = ('sh' if symbol.startswith(('5', '6')) else 'sz')+symbol
        klines = [row for row in captures if row['source_id'] == 'sina_klines'
                  and row['request']['symbol'] == code]
        quotes = [row for row in captures if row['source_id'] == 'sina_quote'
                  and code in row['request']['list'].split(',')]
        relevant = klines+quotes
        if (len(klines) != 1 or len(quotes) != (1 if frequency == 'day' else 0)
                or any(row['status_code'] != 200 or row['body_complete'] is not True
                       or row['transport_error'] is not None for row in relevant)
                or any(datetime.fromisoformat(row['received_at']).tzinfo is None
                       or datetime.fromisoformat(row['received_at']) > completed
                       for row in relevant)
                or klines[0]['request']['scale'] != ('240' if frequency == 'day' else '5')
                or klines[0]['request']['ma'] != 'no'):
            raise ValueError('UNIFIED_MARKET_RAW_SOURCE_OR_CLOCK_INVALID')
        kline = klines[0]
        parsed = completed_klines(kline['body'], symbol=symbol, security_type=kinds[symbol],
            frequency=frequency, requested_day=target.date(),
            observed_at=datetime.fromisoformat(kline['received_at']), calendar=calendar)
        after = spec['after_by_symbol'][symbol]
        selected = [row for row in parsed if row.bar.timestamp <= target and
                    (after is None or row.bar.timestamp > datetime.fromisoformat(after))]
        if frequency == 'day':
            quote = quotes[0]
            lines = [line for line in quote['body'].splitlines()
                     if line.startswith(f'var hq_str_{code}='.encode())]
            if len(lines) != 1 or len(selected) != 1:
                raise ValueError('UNIFIED_MARKET_RAW_DAY_AMBIGUOUS')
            candidate = completed_quotes(lines[0], requested={symbol: kinds[symbol]},
                requested_day=target.date(), observed_at=datetime.fromisoformat(
                    quote['received_at']), calendar=calendar)[symbol]
            selected = [reconcile_day(selected[0], candidate)]
        else:
            expected = {datetime.combine(target.date(), clock, SHANGHAI)
                        for clock in SINA_FIVE_MINUTE_SESSION_TIMES
                        if datetime.combine(target.date(), clock, SHANGHAI) <= target
                        and (after is None or datetime.combine(target.date(), clock, SHANGHAI)
                             > datetime.fromisoformat(after))}
            if {row.bar.timestamp for row in selected} != expected:
                raise ValueError('UNIFIED_MARKET_RAW_MINUTE_WINDOW_INCOMPLETE')
        expected_rows = [{**row.row(), 'raw_pack_sha256': manifest['raw']['sha256'],
                          'source_hashes': [source['body_sha256'] for source in captures
                            if source in relevant]} for row in selected]
        if (not selected or selected[-1].bar.timestamp != target
                or sorted(actual, key=lambda row: row['timestamp']) != expected_rows):
            raise ValueError('UNIFIED_MARKET_RAW_NORMALIZED_REPLAY_MISMATCH')


def _chunks(database: Path, universe: str, windows: dict[str, list[str]]) -> list[dict[str, Any]]:
    if database.is_symlink():
        raise ValueError('UNIFIED_MARKET_DATABASE_SYMLINK')
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True,
                                timeout=5)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        rows: list[dict[str, Any]] = []
        for freq, days in windows.items():
            rows.extend(dict(row) for row in connection.execute('''
                SELECT c.*, b.manifest_sha256 FROM collection_chunks c
                JOIN collection_batches b ON b.batch_id=c.batch_id
                WHERE c.universe_version=? AND c.frequency=? AND b.state='COMPLETE'
                AND substr(c.end_time,1,10)>=? AND substr(c.start_time,1,10)<=?
                AND NOT EXISTS (SELECT 1 FROM excluded_collection_chunks e
                                WHERE e.batch_id=c.batch_id AND e.symbol=c.symbol)
                ORDER BY c.end_time,c.batch_id,c.symbol LIMIT ?
                ''', (universe, freq, days[0], days[-1], MAX_CHUNKS-len(rows)+1)))
            if len(rows) > MAX_CHUNKS:
                raise ValueError('UNIFIED_MARKET_CHUNK_BUDGET')
        return rows


def _load(store: UnifiedBatchStore, batch_id: str, stats: dict[str, Any],
          calendar: Calendar, kinds: dict[str, str], context: tuple[str, str]
          ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = store.batch_path(batch_id)
    if path.is_symlink() or path.stat().st_size > 2_000_000:
        raise ValueError('UNIFIED_MARKET_SOURCE_MANIFEST_BOUND')
    material = store._batch_material(batch_id)
    manifest = material.manifest
    # Hash actual immutable object bytes every pass. Cache skips decompression and
    # Parquet decoding only; neither timestamps nor a previous schema PASS suffice.
    stats['source_bytes_read'] += material.source_bytes_read
    if stats['source_bytes_read'] > SOURCE_BYTE_BUDGET:
        raise ValueError('UNIFIED_MARKET_SOURCE_BYTE_BUDGET')
    if manifest['normalized'] is None:
        raise ValueError('UNIFIED_MARKET_NORMALIZED_SOURCE_MISSING')

    def recheck_manifest() -> None:
        read_before = material.source_bytes_read
        try:
            material.check_manifest_unchanged()
        finally:
            stats['source_bytes_read'] += material.source_bytes_read-read_before
        if stats['source_bytes_read'] > SOURCE_BYTE_BUDGET:
            raise ValueError('UNIFIED_MARKET_SOURCE_BYTE_BUDGET')

    key = (*material.identities(), *context)
    if key in _CACHE:
        recheck_manifest()
        stats['cached_batches'] += 1
        return _CACHE[key]
    material.check_export_bounds()
    checked, _, rows = material.validate()
    with localcontext(Context(prec=28)):
        _replay(material, calendar, kinds)
    recheck_manifest()
    estimate = len(encoded({'rows': rows}))+len(rows)*1024
    if (len(_CACHE) < MAX_BATCHES
            and sum(len(value[1]) for value in _CACHE.values())+len(rows) <= MAX_CACHE_ROWS
            and sum(_CACHE_SIZES.get(old, 0) for old in _CACHE)+estimate
            <= MAX_CACHE_ESTIMATED_BYTES):
        _CACHE[key] = (checked, rows)
        _CACHE_SIZES[key] = estimate
    stats['replayed_batches'] += 1
    return checked, rows


def export_market(base: Path, database: Path, *, now: datetime | None = None,
                  seconds: float = 45) -> dict[str, Any]:
    if os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
        raise ValueError('UNIFIED_MARKET_LIVE_DISABLED_REQUIRED')
    now = now or datetime.now(UTC)
    if now.tzinfo is None or not 1 <= seconds <= 120:
        raise ValueError('UNIFIED_MARKET_CLOCK_OR_BUDGET_INVALID')
    started = time.monotonic()
    deadline = started+seconds
    publication = base/'unified-data/public/unified.json'
    catalog = validate_publication(json.loads(publication.read_bytes()))
    master = catalog['master']
    targets = [row for row in master['securities'] if row['in_target']]
    if not 1 <= len(targets) <= MAX_TARGETS:
        raise ValueError('UNIFIED_MARKET_TARGET_BOUND')
    calendar = Calendar(master['collection']['calendar'])
    windows = {freq: sessions(calendar, calendar.expected(now,
        'daily' if freq == 'day' else 'minute').date(), 20 if freq == 'day' else 3)
        for freq in ('day', '5min')}
    chunks = _chunks(database, master['universe_version'], windows)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        grouped[chunk['batch_id']].append(chunk)
    if len(grouped) > MAX_BATCHES:
        raise ValueError('UNIFIED_MARKET_BATCH_BUDGET')
    # Keep a stable bounded working set instead of evicting it on every sequential
    # scan. Expired batch entries are evicted; over-budget new entries are not cached.
    for key in list(_CACHE):
        if _CACHE[key][0]['batch_id'] not in grouped:
            del _CACHE[key]
            _CACHE_SIZES.pop(key, None)
    stats: dict[str, Any] = {'source_bytes_read': 0, 'cached_batches': 0, 'replayed_batches': 0,
             'selected_chunks': len(chunks), 'selected_batches': len(grouped)}
    identities = {row['symbol']: row for row in targets}
    kinds = {symbol: row['security_type'] for symbol, row in identities.items()}
    context = (signed(calendar.document)['evidence_sha256'], signed(kinds)['evidence_sha256'])
    collected: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    sources: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    problems: dict[tuple[str, str], set[str]] = defaultdict(set)
    store = UnifiedBatchStore(base/'unified-data/lake')
    for batch_id, selected in grouped.items():
        if time.monotonic() > deadline:
            raise ValueError('UNIFIED_MARKET_TIME_BUDGET')
        try:
            manifest, rows = _load(store, batch_id, stats, calendar, kinds, context)
            by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                by_symbol[row['symbol']].append(row)
            spec = manifest['spec']
            for chunk in selected:
                symbol, freq = chunk['symbol'], chunk['frequency']
                if symbol not in identities:
                    raise ValueError('UNIFIED_MARKET_NON_TARGET_CHUNK')
                outcome = manifest['outcomes'][symbol]
                selected_rows = by_symbol[symbol]
                if (manifest['evidence_sha256'] != chunk['manifest_sha256']
                        or spec['universe_version'] != master['universe_version']
                        or spec['frequency'] != freq or outcome['state'] != 'SUCCEEDED'
                        or outcome['bar_count'] != chunk['bar_count']
                        or len(selected_rows) != chunk['bar_count']
                        or min(datetime.fromisoformat(row['timestamp']) for row in selected_rows)
                        != datetime.fromisoformat(chunk['start_time'])
                        or max(datetime.fromisoformat(row['timestamp']) for row in selected_rows)
                        != datetime.fromisoformat(chunk['end_time'])
                        or datetime.fromisoformat(outcome['start']) != datetime.fromisoformat(
                            chunk['start_time'])
                        or datetime.fromisoformat(outcome['end']) != datetime.fromisoformat(
                            chunk['end_time'])
                        or manifest['price_basis'] != PRICE_BASIS
                        or datetime.fromisoformat(manifest['completed_at']) > now):
                    raise ValueError('UNIFIED_MARKET_CHUNK_SOURCE_MISMATCH')
                for original in selected_rows:
                    if (original['completion_status'] != 'CLOSED'
                            or datetime.fromisoformat(original['timestamp'])
                            > datetime.fromisoformat(spec['target_end'])):
                        raise ValueError('UNIFIED_MARKET_UNCLOSED_SOURCE')
                    # Byte identities must survive a new process/hash seed.
                    row = {key: original[key] for key in sorted(BAR_FIELDS)
                           if key != 'amount_basis'}
                    row['amount_basis'] = original['amount_basis']
                    validate_bar(row, symbol, identities[symbol]['security_type'], freq,
                                 calendar, now)
                    day = str(datetime.fromisoformat(row['timestamp']).astimezone(SHANGHAI).date())
                    if day not in windows[freq]:
                        continue
                    key = (symbol, freq)
                    old = collected[key].get(row['timestamp'])
                    if old is not None and old != row:
                        problems[key].add('CONFLICTING_ACCEPTED_BARS')
                    collected[key][row['timestamp']] = row
                    sources[(symbol, freq, day if freq == '5min' else 'window')][batch_id] = {
                        'batch_id': batch_id,
                        'manifest_sha256': manifest['evidence_sha256'],
                        'raw_sha256': manifest['raw']['sha256'],
                        'normalized_sha256': manifest['normalized']['sha256']}
        except OSError:
            raise  # Transient storage faults cannot become sealed permanent exclusions.
        except (ValueError, KeyError, TypeError) as error:
            if isinstance(error, ValueError) and 'BUDGET' in str(error):
                raise
            for chunk in selected:
                problems[(chunk['symbol'], chunk['frequency'])].add('SOURCE_REPLAY_FAILED')
    objects = {}
    entries = []
    for target in sorted(targets, key=lambda row: row['symbol']):
        for freq in ('day', '5min'):
            key = (target['symbol'], freq)
            entry = {field: target[field] for field in ('symbol', 'instrument_id',
                                                       'security_type', 'name')}
            entry.update(frequency=freq, state='NO_DATA', issues=sorted(problems[key]), shards=[])
            if problems[key]:
                entry['state'] = 'QUARANTINED'
            else:
                groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for row in collected[key].values():
                    groups[row['trading_day'] if freq == '5min' else 'window'].append(row)
                for group, rows in groups.items():
                    rows.sort(key=lambda row: datetime.fromisoformat(row['timestamp']))
                    refs = list(sources[(target['symbol'], freq, group)].values())
                    shard = signed({**AUTHORITY, 'schema_version': 'qtrade.unified-market-shard.v1',
                        'universe_version': master['universe_version'], **{field: target[field]
                        for field in ('symbol', 'instrument_id', 'security_type')},
                        'frequency': freq, 'price_basis': PRICE_BASIS, 'bars': rows,
                        'source_batches': sorted(refs, key=lambda row: row['batch_id'])})
                    validate_shard(shard, calendar, now)
                    raw = encoded(shard)
                    digest = sha256(raw).hexdigest()
                    objects[digest] = raw
                    entry['shards'].append({'sha256': digest,
                        'evidence_sha256': shard['evidence_sha256'], 'bytes': len(raw),
                        'start': rows[0]['timestamp'], 'end': rows[-1]['timestamp'],
                        'count': len(rows)})
                if entry['shards']:
                    entry['state'] = 'AVAILABLE'
            entries.append(entry)
    total = sum(len(raw) for raw in objects.values())
    if total > TOTAL_LIMIT or time.monotonic() > deadline:
        raise ValueError('UNIFIED_MARKET_EXPORT_BUDGET')
    # New chunks may arrive while replay runs; excluded/changed selected chunks may not.
    final = {tuple(sorted(row.items())) for row in _chunks(database, master['universe_version'],
                                                          windows)}
    if (any(tuple(sorted(row.items())) not in final for row in chunks)
            or json.loads(publication.read_bytes())['master']['universe_version'] != master[
                'universe_version']):
        raise ValueError('UNIFIED_MARKET_LEDGER_OR_MASTER_CHANGED')
    index = signed({**AUTHORITY, 'schema_version': 'qtrade.unified-market-index.v1',
        'universe_version': master['universe_version'], 'master_sha256': master['evidence_sha256'],
        'published_at': now.isoformat(), 'calendar': calendar.document, 'windows': windows,
        'entries': entries, 'total_bytes': total, 'total_files': len(objects),
        'limitations': LIMITATIONS, 'implementation_sources': {name: file_hash(REPO/name)
            for name in ('scripts/publish_unified_market.py',
                         'src/qtrade/research/unified_market.py')}})
    validate_index(index, now)
    destination = base/'unified-data/market-public'
    for digest, raw in objects.items():
        if time.monotonic() > deadline:
            raise ValueError('UNIFIED_MARKET_TIME_BUDGET')
        path = destination/'unified-market/objects'/(digest+'.json')
        if path.exists():
            if path.is_symlink() or file_hash(path) != digest:
                raise ValueError('UNIFIED_MARKET_IMMUTABLE_OBJECT_CHANGED')
        else:
            _write_object(path, raw)
    if time.monotonic() > deadline:
        raise ValueError('UNIFIED_MARKET_TIME_BUDGET')
    write_json_atomic(destination/'unified-market.json', index)
    stats.update(export_seconds=round(time.monotonic()-started, 6), total_bytes=total,
                 total_files=len(objects), index_id=index['evidence_sha256'],
                 cached_rows=sum(len(value[1]) for value in _CACHE.values()),
                 cache_estimated_bytes=sum(_CACHE_SIZES.get(key, 0) for key in _CACHE),
                 quarantined_entries=sum(row['state'] == 'QUARANTINED' for row in entries))
    write_json_atomic(base/'unified-data/market-export-state.json', signed({**AUTHORITY, **stats}))
    return {'index': index, 'statistics': stats}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export_market(args.base, args.database)['statistics']))


def publish_market_files(base: Path, publication: Path,
                         database: Path | None = None, *,
                         seed_existing: bool = False) -> list[str]:
    """Use the existing public Git queue; a failed export retains its dated last success."""
    if seed_existing and database is not None:
        raise ValueError('UNIFIED_MARKET_DELTA_SEED_DATABASE_FORBIDDEN')

    def checked(root: Path, name: str) -> Path:
        path = root/name
        if root.is_symlink():
            raise ValueError('UNIFIED_MARKET_PUBLIC_PATH_INVALID')
        current = root
        for part in Path(name).parts:
            current = current/part
            if current.is_symlink():
                raise ValueError('UNIFIED_MARKET_PUBLIC_PATH_INVALID')
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('UNIFIED_MARKET_PUBLIC_PATH_INVALID')
        return path

    if database is not None:
        started_at = datetime.now(UTC)
        started = time.monotonic()
        attempt: dict[str, Any] = {**AUTHORITY,
            'schema_version': 'qtrade.unified-market-export-attempt.v1',
            'started_at': started_at.isoformat(), 'state': 'FAILED',
            'index_id': None, 'error_class': None, 'error_code': None}
        try:
            result = export_market(base, database)
            attempt.update(state='SUCCEEDED', index_id=result['index']['evidence_sha256'])
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            attempt['error_class'] = type(error).__name__
            if str(error) in {'UNIFIED_MARKET_TIME_BUDGET', 'UNIFIED_MARKET_EXPORT_BUDGET',
                    'UNIFIED_MARKET_SOURCE_BYTE_BUDGET', 'UNIFIED_MARKET_CHUNK_BUDGET',
                    'UNIFIED_MARKET_BATCH_BUDGET', 'UNIFIED_MARKET_LEDGER_OR_MASTER_CHANGED',
                    'UNIFIED_MARKET_IMMUTABLE_OBJECT_CHANGED'}:
                attempt['error_code'] = str(error)
            print('UNIFIED_MARKET_EXPORT_FAILED_RETAINING_LAST_SUCCESS', flush=True)
        finally:
            attempt.update(completed_at=datetime.now(UTC).isoformat(),
                           elapsed_seconds=round(time.monotonic()-started, 6))
            write_json_atomic(base/'unified-data/market-export-attempt.json', signed(attempt))
    root = publication if seed_existing else base/'unified-data/market-public'
    path = checked(root, 'unified-market.json')
    pointer = checked(publication, 'unified-market.json')
    if not path.is_file():
        if seed_existing or path.exists():
            raise ValueError('UNIFIED_MARKET_PUBLIC_INDEX_INVALID')
        write_json_atomic(pointer, signed({**AUTHORITY,
            'state': 'NO_VERIFIED_UNIFIED_MARKET_PUBLICATION'}))
        return ['unified-market.json']
    if path.is_symlink() or path.stat().st_size > 4_000_000:
        raise ValueError('UNIFIED_MARKET_PUBLIC_INDEX_INVALID')
    index = validate_index(json.loads(path.read_bytes()))
    names = []
    for entry in index['entries']:
        for ref in entry['shards']:
            name = 'unified-market/objects/'+ref['sha256']+'.json'
            source, target = checked(root, name), checked(publication, name)
            if (source.is_symlink() or not source.resolve().is_relative_to(root.resolve())
                    or source.stat().st_size != ref['bytes'] or file_hash(source) != ref['sha256']):
                raise ValueError('UNIFIED_MARKET_PUBLIC_OBJECT_INVALID')
            if not target.resolve().is_relative_to(publication.resolve()):
                raise ValueError('UNIFIED_MARKET_PUBLIC_DESTINATION_ESCAPE')
            if target.exists():
                if target.is_symlink() or file_hash(target) != ref['sha256']:
                    raise ValueError('UNIFIED_MARKET_PUBLIC_OBJECT_CONFLICT')
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            names.append(name)
            # Stable identity names let Git select the previous shard as a delta
            # base. They are transport hints only; consumers still use hash paths.
            group = 'day'
            if entry['frequency'] == '5min':
                start = datetime.fromisoformat(ref['start']).astimezone(SHANGHAI).date()
                end = datetime.fromisoformat(ref['end']).astimezone(SHANGHAI).date()
                if start != end:
                    raise ValueError('UNIFIED_MARKET_DELTA_SESSION_INVALID')
                group = start.strftime('%Y%m%d')
            identity = f"{entry['symbol']}|{entry['frequency']}|{group}"
            suffix = sha256(identity.encode()).hexdigest()[:16]
            prefix = 'M' if entry['frequency'] == '5min' else 'D'
            alias_name = f"unified-market/.delta/{group}/{prefix}{entry['symbol']}-{suffix}.json"
            alias = checked(publication, alias_name)
            if alias.exists() and not alias.is_file():
                raise ValueError('UNIFIED_MARKET_DELTA_ALIAS_INVALID')
            if not alias.exists() or not alias.samefile(target):
                alias.parent.mkdir(parents=True, exist_ok=True)
                temporary = alias.with_name('.'+alias.name+'.'+uuid4().hex+'.tmp')
                try:
                    os.link(target, temporary)
                    os.replace(temporary, alias)
                finally:
                    temporary.unlink(missing_ok=True)
            names.append(alias_name)
    if not seed_existing:
        write_json_atomic(pointer, index)
    return ['unified-market.json', *sorted(set(names))]


if __name__ == '__main__':
    main()
