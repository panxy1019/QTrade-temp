import functools,json,os,time,threading,resource
from collections import defaultdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from scripts import publish_unified_market as m
r=Path('/root/qtrade-local/operator-evidence/market-arrow-detail-profile-20261028T074459Z'); b=Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/operator-checks/market-arrow-detail-profile-20261028T074459Z'); f=Path('/root/qtrade-local/operator-evidence/market-current-freeze-20261028T070943Z')
assert not b.exists() and os.statvfs(b.parent).f_bavail*os.statvfs(b.parent).f_frsize>100*1024**3
(b/'unified-data/public').mkdir(parents=True)
(b/'unified-data/public/unified.json').write_bytes((f/'snapshot/unified-data/public/unified.json').read_bytes())
(b/'unified-data/lake').symlink_to(f/'snapshot/unified-data/lake',target_is_directory=True)
freeze=json.loads((f/'freeze.json').read_bytes());clock=datetime.fromisoformat(freeze['captured_at'])
metrics=defaultdict(lambda:{'count':0,'wall':0.,'cpu':0.})
lock=threading.Lock()
main=threading.get_ident()
def wrap(obj,name,key):
 fn=getattr(obj,name)
 @functools.wraps(fn)
 def call(*a,**k):
  wall=time.monotonic();cpu=time.thread_time()
  try:return fn(*a,**k)
  finally:
   row=key+('main' if threading.get_ident()==main else 'other')
   with lock:
    metrics[row]['count']+=1;metrics[row]['wall']+=time.monotonic()-wall;metrics[row]['cpu']+=time.thread_time()-cpu
 setattr(obj,name,call)
for name in ['_load','_replay','_worker_compact','_worker_inventory','signed','encoded','validate_index','_chunks','_persist_objects']:
 wrap(m,name,name+'.')
for name in ['response','pump','send','close']:
 wrap(m._ShardEncoders,name,'pool.'+name+'.')
from scripts import unified_store as st
for name in ['_export_rows', '_decode_captures', 'canonical']:
 wrap(st,name,'store.'+name+'.')
for name in ['validate','check_export_bounds','_parquet','check_manifest_unchanged']:
 wrap(st._BatchMaterial,name,'material.'+name+'.')
wall=time.monotonic();cpu=time.thread_time()
try:
 result=m.export_market(b,f/'frozen.sqlite',now=clock,seconds=45)
 result={'state':'SUCCEEDED','statistics':result['statistics']}
except BaseException as error:result={'state':'FAILED','error_class':type(error).__name__,'error_code':str(error)}
result.update(wall_seconds=time.monotonic()-wall,cpu_seconds=time.thread_time()-cpu,metrics=dict(metrics),pid=os.getpid(),source_sha256=sha256(Path(m.__file__).read_bytes()).hexdigest(),peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
with (r/'profile.json').open('x') as handle:json.dump(result,handle,sort_keys=True,indent=2)
print(json.dumps(result),flush=True)
