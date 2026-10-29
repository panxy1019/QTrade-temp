"""Current frozen source benchmark on same NFS mount; no production output writes."""
from __future__ import annotations
import argparse
import json
import os
import resource
import subprocess
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument('--evidence', type=Path, required=True)
ap.add_argument('--source-evidence', type=Path, required=True)
ap.add_argument('--data-base', type=Path, required=True)
ap.add_argument('--freeze', type=Path, required=True)
args = ap.parse_args()
root = args.evidence
source_root = args.source_evidence/'source'
manifest = json.loads((args.source_evidence/'manifest.json').read_bytes())
assert not (root/'benchmark-report.json').exists()
for name, digest in manifest['files'].items():
    assert sha256((source_root/name).read_bytes()).hexdigest() == digest
from scripts import publish_unified_market as m
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts.atomic_json import write_json_immutable
assert m.REPO.resolve() == source_root.resolve()
assert os.environ.get('LIVE_TRADING_ENABLED') == 'false'
freeze_raw = (args.freeze/'freeze.json').read_bytes()
freeze = json.loads(freeze_raw)
clock = datetime.fromisoformat(freeze['captured_at'])
base = args.data_base/'operator-checks'/root.name
assert base.is_absolute() and not base.exists()
assert base.parent.resolve() == (args.data_base/'operator-checks').resolve()
assert os.statvfs(base.parent).f_bavail*os.statvfs(base.parent).f_frsize > 100*1024**3
assert os.statvfs(root).f_bavail*os.statvfs(root).f_frsize > 1024**3
catalog = (args.freeze/'snapshot/unified-data/public/unified.json').read_bytes()
assert sha256(catalog).hexdigest() == freeze['catalog_raw_sha256']
assert sha256((args.freeze/'frozen.sqlite').read_bytes()).hexdigest() == freeze['ledger_sha256']
(base/'unified-data/public').mkdir(parents=True)
(base/'unified-data/public/unified.json').write_bytes(catalog)
(base/'unified-data/lake').symlink_to(args.freeze/'snapshot/unified-data/lake', target_is_directory=True)

def mount_snapshot(label):
    raw = Path('/proc/self/mountinfo').read_bytes()
    write_json = root/(label+'-mountinfo.raw')
    with write_json.open('xb') as handle:
        handle.write(raw)
    rows = []
    for line in raw.decode().splitlines():
        fields = line.split()
        split = fields.index('-')
        point = fields[4].replace('\\040', ' ')
        if base.resolve().is_relative_to(Path(point)):
            rows.append((len(point), {'mount_id':fields[0], 'parent_id':fields[1],
                'major_minor':fields[2], 'root':fields[3], 'mount_point':point,
                'fs_type':fields[split+1], 'source':fields[split+2], 'raw_line':line}))
    selected = max(rows, key=lambda row: row[0])[1]
    output_dev = base.stat().st_dev
    production_dev = (args.data_base/'unified-data/market-public').stat().st_dev
    assert output_dev == production_dev and selected['fs_type'] in {'nfs', 'nfs4'}
    assert selected['major_minor'] == f'{os.major(output_dev)}:{os.minor(output_dev)}'
    return {'raw_sha256':sha256(raw).hexdigest(), 'selected_mount':selected,
        'output_st_dev':output_dev, 'production_st_dev':production_dev,
        'output_resolved':str(base.resolve()),
        'production_resolved':str((args.data_base/'unified-data/market-public').resolve()),
        'available_bytes':os.statvfs(base).f_bavail*os.statvfs(base).f_frsize}

before_mount = mount_snapshot('before')
parent_stat = Path('/proc/self/stat').read_text()
parent_start_ticks = parent_stat[parent_stat.rfind(')')+2:].split()[19]
results = []
for label in ['cold', 'warm']:
    empty_cache = not m._CACHE
    destination_count = len(list((base/'unified-data/market-public/unified-market/objects').glob('*.json')))
    if label == 'cold':
        assert empty_cache and destination_count == 0
    started = time.monotonic()
    try:
        result = m.export_market(base, args.freeze/'frozen.sqlite', now=clock, seconds=45)
        wall = time.monotonic()-started
        assert result['statistics']['selected_chunks'] == freeze['selected_chunks']
        assert result['statistics']['selected_batches'] == freeze['selected_batches']
        index_raw = (base/'unified-data/market-public/unified-market.json').read_bytes()
        index = json.loads(index_raw)
        assert index == result['index']
        refs = sorted([{'symbol':entry['symbol'], 'frequency':entry['frequency'], **ref}
            for entry in index['entries'] for ref in entry['shards']],
            key=lambda row:(row['symbol'],row['frequency'],row['start'],row['sha256']))
        raw_refs = json.dumps(refs, sort_keys=True, separators=(',',':'), ensure_ascii=False).encode()
        with (root/(label+'-output-refs.json')).open('xb') as handle:handle.write(raw_refs)
        item = {'state':'SUCCEEDED', 'statistics':result['statistics'],
            'wall_seconds':round(wall, 6), 'within_45_seconds':wall <= 45,
            'index_raw_sha256':sha256(index_raw).hexdigest(),
            'output_refs_manifest_sha256':sha256(raw_refs).hexdigest(),
            'output_refs_count':len(refs),
            'previous_success_used_as_oracle':False,
            'independent_full_current_audit':'PENDING'}
    except BaseException as error:
        item = {'state':'FAILED', 'error_class':type(error).__name__, 'error_code':str(error),
            'wall_seconds':round(time.monotonic()-started, 6), 'within_45_seconds':False}
    item.update(pass_name=label, pid=os.getpid(), parent_start_ticks=parent_start_ticks,
        decoded_cache_empty_at_start=empty_cache, destination_objects_at_start=destination_count,
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        max_source_cache_note='SOURCE_BYTES_ARE_CUMULATIVE_ACCOUNTED_READS_AND_CACHE_END_TOTAL_IS_MONOTONIC_DERIVATION_NOT_A_SAMPLED_PEAK')
    results.append(item)
    print(json.dumps(item), flush=True)
after_mount = mount_snapshot('after')
assert before_mount['selected_mount'] == after_mount['selected_mount']
for name, digest in manifest['files'].items():
    assert sha256((source_root/name).read_bytes()).hexdigest() == digest
assert sha256((args.freeze/'frozen.sqlite').read_bytes()).hexdigest() == freeze['ledger_sha256']
report = signed({**AUTHORITY, 'schema_version':'qtrade.market-export-worker-benchmark.v1',
    'checked_at':datetime.now(UTC).isoformat(), 'source_base_revision':manifest['base_revision'],
    'source_scope':manifest['source_scope'], 'source_manifest_sha256':sha256((args.source_evidence/'manifest.json').read_bytes()).hexdigest(),
    'candidate_files':{name:manifest['files'][name] for name in manifest['candidate_paths']},
    'worker_sources':m._worker_inventory(), 'source_files_verified':len(manifest['files']),
    'freeze':str(args.freeze), 'freeze_evidence_sha256':freeze['evidence_sha256'],
    'controlled_snapshot_clock':clock.isoformat(), 'output_base':str(base),
    'clock_scope':'FROZEN_BENCHMARK_ONLY_NOT_PRODUCTION_ACCEPTANCE',
    'production_output_modified':False, 'profiling_enabled':False,
    'controlled_seconds_limit':45, 'production_seconds_limit_unchanged':45,
    'source_byte_budget':m.SOURCE_BYTE_BUDGET, 'cache_estimated_byte_budget':m.MAX_CACHE_ESTIMATED_BYTES,
    'parent_pid':os.getpid(), 'parent_start_ticks':parent_start_ticks,
    'mount_before':before_mount, 'mount_after':after_mount,
    'children_at_completion':Path('/proc/self/task/'+str(os.getpid())+'/children').read_text().strip(),
    'results':results})
write_json_immutable(root/'benchmark-report.json', report)
assert all(item['state']=='SUCCEEDED' and item['within_45_seconds'] for item in results), 'CURRENT_FROZEN_EXPORT_NOT_ACCEPTED'
