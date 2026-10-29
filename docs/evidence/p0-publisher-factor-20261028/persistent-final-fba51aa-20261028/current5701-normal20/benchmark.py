"""Current frozen source benchmark on same NFS mount; no production output writes."""
from __future__ import annotations
import argparse
import json
import os
import resource
import sys
import shutil
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

old=Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/operator-checks/market-persistent-final-modes-nfs-20261028T105454Z')
old_operator=Path('/root/qtrade-local/operator-evidence/market-persistent-final-modes-nfs-20261028T105454Z')
setup_started=time.monotonic()
for name in ['market-public','market-checkpoints']:
 shutil.copytree(old/'unified-data'/name,base/'unified-data'/name,copy_function=os.link)
 for parent,dirs,files in os.walk(base/'unified-data'/name,topdown=False):
  fd=os.open(parent,os.O_RDONLY)
  try:os.fsync(fd)
  finally:os.close(fd)
shutil.copy2(old_operator/'bootstrap1-validation.sqlite',root/'validation.sqlite')
with (root/'validation.sqlite').open('rb') as handle:os.fsync(handle.fileno())
seed_seconds=time.monotonic()-setup_started
before_mount = mount_snapshot('before')
parent_stat = Path('/proc/self/stat').read_text()
parent_start_ticks = parent_stat[parent_stat.rfind(')')+2:].split()[19]
results = []
driver = r"""import json,resource,time,sys,os
from pathlib import Path
from datetime import datetime
from scripts import publish_unified_market as m
b,f,db,clock,limit=sys.argv[1:]
t=time.monotonic()
try:
 result=m.export_market(Path(b),Path(f)/'frozen.sqlite',now=datetime.fromisoformat(clock),seconds=float(limit),receipt_database=Path(db),checkpoint_root=Path(b)/'unified-data/market-checkpoints')
 item={'state':'SUCCEEDED','statistics':result['statistics']}
except BaseException as error:item={'state':'FAILED','error_class':type(error).__name__,'error_code':str(error)}
item.update(wall_seconds=time.monotonic()-t,pid=os.getpid(),peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,cache_empty_at_start=True)
print(json.dumps(item),flush=True)
"""
(root/'pass_driver.py').write_text(driver)
for label,limit in [('incremental',45)]+[(f'restart{i:02}',45) for i in range(1,20)]:
 child=subprocess.run([sys.executable,'-B',str(root/'pass_driver.py'),str(base),str(args.freeze),str(root/'validation.sqlite'),clock.isoformat(),str(limit)],capture_output=True,text=True,timeout=limit+15)
 (root/(label+'-stdout.log')).write_text(child.stdout)
 (root/(label+'-stderr.log')).write_text(child.stderr)
 assert child.returncode==0, 'PASS_SUBPROCESS_FAILED'
 item=json.loads(child.stdout)
 item.update(pass_name=label,seconds_limit=limit,within_budget=item['wall_seconds']<=limit)
 if item['state']=='SUCCEEDED':
  index_raw=(base/'unified-data/market-public/unified-market.json').read_bytes()
  index=json.loads(index_raw)
  assert item['statistics']['selected_chunks']==freeze['selected_chunks']
  assert item['statistics']['selected_batches']==freeze['selected_batches']
  assert item['statistics']['total_files']>0 and item['statistics']['quarantined_entries']==0
  refs=sorted([{'symbol':entry['symbol'],'frequency':entry['frequency'],**ref} for entry in index['entries'] for ref in entry['shards']],key=lambda row:(row['symbol'],row['frequency'],row['start'],row['sha256']))
  raw_refs=json.dumps(refs,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
  (root/(label+'-output-refs.json')).write_bytes(raw_refs)
  item.update(index_raw_sha256=sha256(index_raw).hexdigest(),output_refs_manifest_sha256=sha256(raw_refs).hexdigest(),output_refs_count=len(refs),previous_success_used_as_oracle=False,independent_full_current_audit='PENDING')
 results.append(item)
 print(json.dumps(item),flush=True)
 if item["state"] != "SUCCEEDED" or not item["within_budget"]: break
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
    'production_output_modified':False, 'profiling_enabled':False, 'concurrent_operator_workload':'FINAL_FULL_COLD_DIAGNOSTICS_ON_SAME_SHARED_NFS_REPORTED_NOT_EXCLUSIVE',
    'controlled_seconds_limit':45, 'seed_setup_seconds_excluded_and_reported':seed_seconds, 'initial_state':'CLONE_OF_PRIOR_REAL_DURABLE_5640_CHECKPOINT_NOT_NO_CHECKPOINT_COLD', 'production_seconds_limit_unchanged':45,
    'source_byte_budget':m.SOURCE_BYTE_BUDGET, 'cache_estimated_byte_budget':m.MAX_CACHE_ESTIMATED_BYTES,
    'parent_pid':os.getpid(), 'parent_start_ticks':parent_start_ticks,
    'mount_before':before_mount, 'mount_after':after_mount,
    'children_at_completion':Path('/proc/self/task/'+str(os.getpid())+'/children').read_text().strip(),
    'results':results})
write_json_immutable(root/'benchmark-report.json', report)
assert len(results)==20 and all(item['state']=='SUCCEEDED' and item['within_budget'] for item in results), 'CURRENT_FROZEN_EXPORT_NOT_ACCEPTED'
