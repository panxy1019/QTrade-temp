"""Original-458 read-only oracle; never calls an exporter or writes a market pointer."""
from __future__ import annotations

import argparse
import importlib
import json
import os
import resource
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from contextlib import closing
from datetime import UTC, datetime
from decimal import Context, localcontext
from hashlib import sha256
from pathlib import Path

BASE_REVISION = '458552de1fea1faf77d02dcd9ec69251a0c364b7'
MAX_BATCHES, MAX_CHUNKS = 12_000, 600_000
SOURCE_LIMIT, OUTPUT_LIMIT = 512_000_000, 192_000_000


def require(condition, code):
    if not condition:
        raise ValueError('ORACLE_'+code)


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--baseline-source', type=Path, required=True)
    ap.add_argument('--source-manifest', type=Path, required=True)
    ap.add_argument('--freeze', type=Path, required=True)
    ap.add_argument('--candidate', type=Path, required=True)
    ap.add_argument('--report', type=Path, required=True)
    args = ap.parse_args()
    started = time.monotonic()
    require(os.environ.get('LIVE_TRADING_ENABLED') == 'false', 'LIVE_FALSE_REQUIRED')
    baseline = args.baseline_source.resolve()
    require(not args.report.exists(), 'REPORT_ALREADY_EXISTS')
    source_manifest_raw = args.source_manifest.read_bytes()
    source_manifest = json.loads(source_manifest_raw)
    require(source_manifest['source_revision'] == BASE_REVISION, 'BASELINE_REVISION')
    for name, expected in source_manifest['files'].items():
        path = baseline/name
        require(not path.is_symlink() and path.resolve().is_relative_to(baseline), 'BASELINE_PATH')
        require(digest(path) == expected, 'BASELINE_SOURCE_HASH')
    sys.path[:0] = [str(baseline/'src'), str(baseline)]
    from qtrade.api.unified_data import validate_publication
    from qtrade.market_data.akshare import SHANGHAI
    from qtrade.market_data.sina_incremental import PRICE_BASIS, completed_klines, completed_quotes, reconcile_day
    from qtrade.market_data.sina_minute import SINA_FIVE_MINUTE_SESSION_TIMES
    from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
    from qtrade.research.unified_market import BAR_FIELDS, INDEX_LIMIT, MAX_TARGETS, encoded, sessions, validate_bar, validate_index, validate_shard
    from scripts.unified_store import UnifiedBatchStore
    imported = {}
    for name in ['qtrade.api.unified_data', 'qtrade.market_data.sina_incremental',
                 'qtrade.monitoring.data_freshness', 'qtrade.research.unified_market',
                 'qtrade.data.quality', 'scripts.unified_store']:
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        require(path.is_relative_to(baseline), 'IMPORT_OUTSIDE_BASELINE')
        relative = path.relative_to(baseline).as_posix()
        require(digest(path) == source_manifest['files'][relative], 'IMPORT_SOURCE_HASH')
        imported[relative] = digest(path)
    require('scripts.publish_unified_market' not in sys.modules, 'EXPORTER_IMPORTED')
    freeze_raw = (args.freeze/'freeze.json').read_bytes()
    freeze = json.loads(freeze_raw)
    require(signed({k:v for k,v in freeze.items() if k != 'evidence_sha256'}) == freeze, 'FREEZE_SIGNATURE')
    clock = datetime.fromisoformat(freeze['captured_at'])
    database = args.freeze/'frozen.sqlite'
    require(not database.is_symlink() and digest(database) == freeze['ledger_sha256'], 'FROZEN_LEDGER_HASH')
    catalog_raw = (args.freeze/'snapshot/unified-data/public/unified.json').read_bytes()
    require(sha256(catalog_raw).hexdigest() == freeze['catalog_raw_sha256'], 'FROZEN_CATALOG_HASH')
    catalog = validate_publication(json.loads(catalog_raw))
    master = catalog['master']
    targets = sorted([t for t in master['securities'] if t['in_target']], key=lambda t:t['symbol'])
    require(1 <= len(targets) <= MAX_TARGETS, 'TARGET_COUNT')
    identities = {t['symbol']:t for t in targets}
    kinds = {s:t['security_type'] for s,t in identities.items()}
    calendar = Calendar(master['collection']['calendar'])
    windows = {f:sessions(calendar, calendar.expected(clock, 'daily' if f == 'day' else 'minute').date(),
                          20 if f == 'day' else 3) for f in ['day','5min']}
    require(windows == freeze['windows'], 'WINDOW_BINDING')
    candidate_manifest_raw = (args.candidate/'candidate-manifest.json').read_bytes()
    candidate_manifest = json.loads(candidate_manifest_raw)
    for name, expected in candidate_manifest['candidate_files'].items():
        path = args.candidate/'source'/name
        require(not path.is_symlink() and digest(path) == expected, 'CANDIDATE_SOURCE_HASH')
    base = args.candidate/'current-frozen-export'
    require((base/'unified-data/public/unified.json').read_bytes() == catalog_raw, 'CANDIDATE_CATALOG_BINDING')
    index_path = base/'unified-data/market-public/unified-market.json'
    require(not index_path.is_symlink() and index_path.stat().st_size <= INDEX_LIMIT, 'INDEX_FILE_BOUND')
    index_raw = index_path.read_bytes()
    index = validate_index(json.loads(index_raw), clock)
    require(index['published_at'] == clock.isoformat(), 'INDEX_CLOCK_BINDING')
    require(index['universe_version'] == master['universe_version'] and
            index['master_sha256'] == master['evidence_sha256'] and
            index['windows'] == windows and index['calendar'] == calendar.document, 'INDEX_MASTER_BINDING')
    require(index['implementation_sources'] == {name:candidate_manifest['candidate_files'][name]
        for name in ['scripts/publish_unified_market.py', 'src/qtrade/research/unified_market.py']}, 'INDEX_IMPLEMENTATION_BINDING')

    def chunks():
        with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
            connection.execute('BEGIN')
            rows = []
            for frequency, days in windows.items():
                rows.extend(dict(row) for row in connection.execute('''
                    SELECT c.*, b.manifest_sha256 FROM collection_chunks c
                    JOIN collection_batches b ON b.batch_id=c.batch_id
                    WHERE c.universe_version=? AND c.frequency=? AND b.state='COMPLETE'
                    AND substr(c.end_time,1,10)>=? AND substr(c.start_time,1,10)<=?
                    AND NOT EXISTS (SELECT 1 FROM excluded_collection_chunks e
                                    WHERE e.batch_id=c.batch_id AND e.symbol=c.symbol)
                    ORDER BY c.end_time,c.batch_id,c.symbol LIMIT ?
                    ''', (master['universe_version'], frequency, days[0], days[-1], MAX_CHUNKS-len(rows)+1)))
                require(len(rows) <= MAX_CHUNKS, 'CHUNK_BUDGET')
            return rows

    selected_chunks = chunks()
    grouped = defaultdict(list)
    for row in selected_chunks:
        grouped[row['batch_id']].append(row)
    require(len(grouped) <= MAX_BATCHES and len(grouped) == freeze['selected_batches'] and
            len(selected_chunks) == freeze['selected_chunks'], 'SELECTED_METADATA_COUNTS')
    store = UnifiedBatchStore(args.freeze/'snapshot/unified-data/lake')
    stats = {'source_bytes_read':0, 'batches_validated':0, 'captures_replayed':0,
             'normalized_rows_replayed':0, 'expected_bars':0, 'actual_shards_read':0,
             'actual_output_bytes_read':0, 'source_failures':0}
    physical_read_bytes = Path.read_bytes
    def accounted_read_bytes(path):
        raw = physical_read_bytes(path)
        if path.parent in {store.root/'batches', store.root/'objects'}:
            stats['source_bytes_read'] += len(raw)
            require(stats['source_bytes_read'] <= SOURCE_LIMIT, 'SOURCE_BYTE_BUDGET')
        return raw
    # Count every actual source read, including a blob whose subsequent hash
    # check fails. Validation remains entirely in the original store/parser.
    Path.read_bytes = accounted_read_bytes
    collected = defaultdict(dict)
    sources = defaultdict(dict)
    problems = defaultdict(set)
    manifest_bindings = []

    def replay(manifest, captures, rows):
        spec = manifest['spec']
        target = datetime.fromisoformat(spec['target_end']).astimezone(SHANGHAI)
        completed = datetime.fromisoformat(manifest['completed_at'])
        frequency = spec['frequency']
        by_symbol = defaultdict(list)
        for row in rows:
            by_symbol[row['symbol']].append(row)
        for symbol, actual in by_symbol.items():
            code = ('sh' if symbol.startswith(('5','6')) else 'sz')+symbol
            klines = [c for c in captures if c['source_id'] == 'sina_klines' and c['request']['symbol'] == code]
            quotes = [c for c in captures if c['source_id'] == 'sina_quote' and code in c['request']['list'].split(',')]
            relevant = klines+quotes
            require(len(klines) == 1 and len(quotes) == (1 if frequency == 'day' else 0), 'RAW_ENDPOINT_COUNTS')
            require(all(c['status_code'] == 200 and c['body_complete'] is True and c['transport_error'] is None
                        and datetime.fromisoformat(c['received_at']).tzinfo is not None
                        and datetime.fromisoformat(c['received_at']) <= completed for c in relevant), 'RAW_RECEIPTS')
            require(klines[0]['request']['scale'] == ('240' if frequency == 'day' else '5')
                    and klines[0]['request']['ma'] == 'no', 'RAW_REQUEST')
            parsed = completed_klines(klines[0]['body'], symbol=symbol, security_type=kinds[symbol],
                frequency=frequency, requested_day=target.date(),
                observed_at=datetime.fromisoformat(klines[0]['received_at']), calendar=calendar)
            after = spec['after_by_symbol'][symbol]
            selected = [bar for bar in parsed if bar.bar.timestamp <= target and
                        (after is None or bar.bar.timestamp > datetime.fromisoformat(after))]
            if frequency == 'day':
                quote = quotes[0]
                lines = [line for line in quote['body'].splitlines() if line.startswith(f'var hq_str_{code}='.encode())]
                require(len(lines) == 1 and len(selected) == 1, 'RAW_DAY_AMBIGUITY')
                daily_quote = completed_quotes(lines[0], requested={symbol:kinds[symbol]},
                    requested_day=target.date(), observed_at=datetime.fromisoformat(quote['received_at']), calendar=calendar)[symbol]
                selected = [reconcile_day(selected[0], daily_quote)]
            else:
                expected = {datetime.combine(target.date(), t, SHANGHAI) for t in SINA_FIVE_MINUTE_SESSION_TIMES
                    if datetime.combine(target.date(), t, SHANGHAI) <= target and
                    (after is None or datetime.combine(target.date(), t, SHANGHAI) > datetime.fromisoformat(after))}
                require({bar.bar.timestamp for bar in selected} == expected, 'RAW_MINUTE_WINDOW')
            expected_rows = [{**bar.row(), 'raw_pack_sha256':manifest['raw']['sha256'],
                'source_hashes':[c['body_sha256'] for c in captures if c in relevant]} for bar in selected]
            require(bool(selected) and selected[-1].bar.timestamp == target and
                    sorted(actual, key=lambda row:row['timestamp']) == expected_rows, 'RAW_NORMALIZED_LINEAGE_REPLAY')

    for batch_id, selected in grouped.items():
        try:
            path = store.batch_path(batch_id)
            require(not path.is_symlink() and path.stat().st_size <= 2_000_000, 'MANIFEST_BOUND')
            material = store._batch_material(batch_id)
            require(material.manifest['normalized'] is not None, 'NORMALIZED_SOURCE_MISSING')
            material.check_export_bounds()
            manifest, captures, rows = material.validate()
            with localcontext(Context(prec=28)):
                replay(manifest, captures, rows)
            material.check_manifest_unchanged()
            stats['batches_validated'] += 1
            stats['captures_replayed'] += len(captures)
            stats['normalized_rows_replayed'] += len(rows)
            manifest_bindings.append({'batch_id':batch_id, 'manifest_sha256':manifest['evidence_sha256'],
                'raw_sha256':manifest['raw']['sha256'], 'normalized_sha256':manifest['normalized']['sha256']})
            by_symbol = defaultdict(list)
            for row in rows:
                by_symbol[row['symbol']].append(row)
            spec = manifest['spec']
            for chunk in selected:
                symbol, frequency = chunk['symbol'], chunk['frequency']
                require(symbol in identities, 'NON_TARGET_CHUNK')
                outcome = manifest['outcomes'][symbol]
                selected_rows = by_symbol[symbol]
                require(manifest['evidence_sha256'] == chunk['manifest_sha256'] and
                    spec['universe_version'] == master['universe_version'] and spec['frequency'] == frequency and
                    outcome['state'] == 'SUCCEEDED' and outcome['bar_count'] == chunk['bar_count'] == len(selected_rows) and
                    min(datetime.fromisoformat(row['timestamp']) for row in selected_rows) == datetime.fromisoformat(chunk['start_time']) and
                    max(datetime.fromisoformat(row['timestamp']) for row in selected_rows) == datetime.fromisoformat(chunk['end_time']) and
                    datetime.fromisoformat(outcome['start']) == datetime.fromisoformat(chunk['start_time']) and
                    datetime.fromisoformat(outcome['end']) == datetime.fromisoformat(chunk['end_time']) and
                    manifest['price_basis'] == PRICE_BASIS and datetime.fromisoformat(manifest['completed_at']) <= clock,
                    'CHUNK_SOURCE_BINDING')
                for original in selected_rows:
                    require(original['completion_status'] == 'CLOSED' and
                        datetime.fromisoformat(original['timestamp']) <= datetime.fromisoformat(spec['target_end']), 'UNCLOSED_SOURCE')
                    row = {key:original[key] for key in sorted(BAR_FIELDS) if key != 'amount_basis'}
                    row['amount_basis'] = original['amount_basis']
                    day = str(datetime.fromisoformat(row['timestamp']).astimezone(SHANGHAI).date())
                    if day not in windows[frequency]:
                        validate_bar(row, symbol, kinds[symbol], frequency, calendar, clock)
                        continue
                    key = (symbol, frequency)
                    old = collected[key].get(row['timestamp'])
                    if old is not None and old != row:
                        problems[key].add('CONFLICTING_ACCEPTED_BARS')
                    collected[key][row['timestamp']] = row
                    sources[(symbol, frequency, day if frequency == '5min' else 'window')][batch_id] = {
                        'batch_id':batch_id, 'manifest_sha256':manifest['evidence_sha256'],
                        'raw_sha256':manifest['raw']['sha256'], 'normalized_sha256':manifest['normalized']['sha256']}
        except OSError:
            raise
        except (ValueError, KeyError, TypeError) as error:
            if 'BUDGET' in str(error):
                raise
            stats['source_failures'] += 1
            for chunk in selected:
                problems[(chunk['symbol'],chunk['frequency'])].add('SOURCE_REPLAY_FAILED')
        if stats['batches_validated'] and stats['batches_validated'] % 500 == 0:
            print(json.dumps({'progress_batches':stats['batches_validated'], 'source_bytes_read':stats['source_bytes_read']}), flush=True)

    expected_entries, unique_objects = [], {}
    object_root = base/'unified-data/market-public/unified-market/objects'
    for target in targets:
        for frequency in ['day','5min']:
            symbol = target['symbol']
            key = (symbol, frequency)
            entry = {field:target[field] for field in ['symbol','instrument_id','security_type','name']}
            entry.update(frequency=frequency, state='NO_DATA', issues=sorted(problems[key]), shards=[])
            if problems[key]:
                entry['state'] = 'QUARANTINED'
            else:
                groups = defaultdict(list)
                for row in collected[key].values():
                    groups[row['trading_day'] if frequency == '5min' else 'window'].append(row)
                for group, rows in groups.items():
                    rows.sort(key=lambda row:datetime.fromisoformat(row['timestamp']))
                    refs = sorted(sources[(symbol,frequency,group)].values(), key=lambda row:row['batch_id'])
                    shard = signed({**AUTHORITY, 'schema_version':'qtrade.unified-market-shard.v1',
                        'universe_version':master['universe_version'], **{f:target[f] for f in ['symbol','instrument_id','security_type']},
                        'frequency':frequency, 'price_basis':PRICE_BASIS, 'bars':rows, 'source_batches':refs})
                    validate_shard(shard, calendar, clock)
                    raw = encoded(shard)
                    identity = sha256(raw).hexdigest()
                    entry['shards'].append({'sha256':identity, 'evidence_sha256':shard['evidence_sha256'],
                        'bytes':len(raw), 'start':rows[0]['timestamp'], 'end':rows[-1]['timestamp'], 'count':len(rows)})
                    stats['expected_bars'] += len(rows)
                    if identity not in unique_objects:
                        unique_objects[identity] = len(raw)
                        require(sum(unique_objects.values()) <= OUTPUT_LIMIT, 'OUTPUT_BYTE_BUDGET')
                        path = object_root/(identity+'.json')
                        require(not path.is_symlink() and path.stat().st_size == len(raw), 'ACTUAL_OBJECT_SIZE')
                        actual = path.read_bytes()
                        require(actual == raw and sha256(actual).hexdigest() == identity, 'EXACT_SHARD_BYTES')
                        stats['actual_shards_read'] += 1
                        stats['actual_output_bytes_read'] += len(actual)
                if entry['shards']:
                    entry['state'] = 'AVAILABLE'
            expected_entries.append(entry)
    require(expected_entries == index['entries'], 'ALL_ENTRIES_REFERENCES_OR_STATES_DIFFER')
    require(len(unique_objects) == index['total_files'] and sum(unique_objects.values()) == index['total_bytes'], 'INDEX_OUTPUT_COUNTS')
    require(chunks() == selected_chunks and digest(database) == freeze['ledger_sha256'], 'FINAL_FROZEN_LEDGER_CHANGED')
    require((args.freeze/'freeze.json').read_bytes() == freeze_raw and
            (args.freeze/'snapshot/unified-data/public/unified.json').read_bytes() == catalog_raw and
            index_path.read_bytes() == index_raw and
            (args.candidate/'candidate-manifest.json').read_bytes() == candidate_manifest_raw, 'FINAL_INPUT_OR_OUTPUT_CHANGED')
    require('scripts.publish_unified_market' not in sys.modules, 'EXPORTER_IMPORTED')
    Path.read_bytes = physical_read_bytes
    report = signed({**AUTHORITY, 'schema_version':'qtrade.independent-current-market-oracle.v1',
        'state':'PASS', 'checked_at':datetime.now(UTC).isoformat(),
        'scope':'ORIGINAL_458_READONLY_ORACLE_NOT_EXPORT_PERFORMANCE_OR_PRODUCTION_ACCEPTANCE',
        'baseline_revision':BASE_REVISION, 'baseline_source_manifest_sha256':sha256(source_manifest_raw).hexdigest(),
        'baseline_imports':imported, 'candidate_manifest_sha256':sha256(candidate_manifest_raw).hexdigest(),
        'candidate_files':candidate_manifest['candidate_files'], 'freeze_evidence_sha256':freeze['evidence_sha256'],
        'ledger_sha256':freeze['ledger_sha256'], 'catalog_raw_sha256':freeze['catalog_raw_sha256'],
        'controlled_snapshot_clock':clock.isoformat(), 'selected_chunks':len(selected_chunks), 'selected_batches':len(grouped),
        'all_entries_equal':True, 'entry_count':len(expected_entries),
        'entry_states':dict(Counter(entry['state'] for entry in expected_entries)),
        'all_shards_byte_equal':True, 'unique_shards':len(unique_objects), 'output_bytes':sum(unique_objects.values()),
        'index_raw_sha256':sha256(index_raw).hexdigest(), 'index_evidence_sha256':index['evidence_sha256'],
        'source_manifest_bindings_sha256':sha256(json.dumps(manifest_bindings,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'oracle_wall_seconds':round(time.monotonic()-started,6),
        'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'source_byte_limit':SOURCE_LIMIT, 'metadata_batch_limit':MAX_BATCHES, 'metadata_chunk_limit':MAX_CHUNKS,
        'output_byte_limit':OUTPUT_LIMIT, 'decoded_batch_cache_used':False, 'exporter_called':False,
        'candidate_validator_called':False, 'market_pointer_written':False, 'production_output_modified':False,
        'statistics':stats})
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open('xb') as handle:
        handle.write(encoded(report))
        handle.flush()
        os.fsync(handle.fileno())
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
