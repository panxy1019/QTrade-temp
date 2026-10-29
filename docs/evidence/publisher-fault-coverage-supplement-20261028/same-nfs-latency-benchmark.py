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

from scripts import unified_market_checkpoint as checkpoint
from datetime import timedelta
pointer=base/'unified-data/market-public/unified-market.json'
before=pointer.read_bytes()
original=checkpoint.prepare
injected=[]
def slow_prepare(*a,**kw):
 value=original(*a,**kw)
 t=time.monotonic();time.sleep(16);injected.append(time.monotonic()-t)
 return value
checkpoint.prepare=slow_prepare
started=time.monotonic()
try:
 m.export_market(base,args.freeze/'frozen.sqlite',now=clock+timedelta(seconds=1),seconds=45,receipt_database=root/'validation.sqlite',checkpoint_root=base/'unified-data/market-checkpoints')
 raise AssertionError('SLOW_NFS_CANDIDATE_MUST_NOT_PUBLISH')
except ValueError as error:
 assert str(error)=='UNIFIED_MARKET_TIME_BUDGET', str(error)
 failure={'error_class':type(error).__name__,'error_code':str(error),'wall_seconds':time.monotonic()-started}
finally:checkpoint.prepare=original
assert injected and pointer.read_bytes()==before
recovery=m.export_market(base,args.freeze/'frozen.sqlite',now=clock+timedelta(seconds=1),seconds=45,receipt_database=root/'validation.sqlite',checkpoint_root=base/'unified-data/market-checkpoints')
assert pointer.read_bytes()!=before
for name,digest in manifest['files'].items():assert sha256((source_root/name).read_bytes()).hexdigest()==digest
value=signed({**AUTHORITY,'schema_version':'qtrade.same-nfs-latency-injection.v1','state':'EXPECTED_FAILURE_AND_RECOVERY_VERIFIED','checked_at':datetime.now(UTC).isoformat(),'source_revision':manifest['source_revision'],'source_manifest_sha256':sha256((args.source_evidence/'manifest.json').read_bytes()).hexdigest(),'freeze_evidence_sha256':freeze['evidence_sha256'],'injection_scope':'ACTUAL_ISOLATED_NFS_GENERATION_WITH_CONTROLLED_POST_FSYNC_LATENCY_NOT_REAL_OUTAGE','injected_elapsed_seconds':injected,'negative_export':failure,'negative_head_preserved':True,'recovery_statistics':recovery['statistics'],'new_recovery_head':sha256(pointer.read_bytes()).hexdigest(),'original_head':sha256(before).hexdigest(),'mount_before':before_mount,'mount_after':mount_snapshot('after'),'normal_seconds_limit_unchanged':45,'production_output_modified':False,'logical_clock_scope':'FROZEN_DIAGNOSTIC_ONLY'})
write_json_immutable(root/'benchmark-report.json',value)
print(json.dumps({k:v for k,v in value.items() if k not in {'mount_before','mount_after'}}),flush=True)
