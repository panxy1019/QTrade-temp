import functools,json,os,time,threading,resource
from collections import defaultdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from scripts import publish_unified_market as m
r=Path('/root/qtrade-local/operator-evidence/market-persistent-delta-profile-20261028T101709Z'); b=Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/operator-checks/market-persistent-continuity-nfs-20261028T101117Z'); f=Path('/root/qtrade-local/operator-evidence/market-current-freeze-20261028T100718Z')
assert b.exists()
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
from qtrade.research.market_validation_receipts import MarketValidationReceiptStore
for name in ['get','put_success']:
 wrap(MarketValidationReceiptStore,name,'receipt.'+name+'.')
wall=time.monotonic();cpu=time.thread_time()
try:
 result=m.export_market(b,f/'frozen.sqlite',now=clock,seconds=45,receipt_database=Path('/root/qtrade-local/operator-evidence/market-persistent-continuity-nfs-20261028T101117Z/validation.sqlite'),checkpoint_root=b/'unified-data/market-checkpoints')
 result={'state':'SUCCEEDED','statistics':result['statistics']}
except BaseException as error:
 result={'state':'FAILED','error_class':type(error).__name__,'error_code':str(error)}
 tb=error.__traceback__
 while tb is not None:
  if tb.tb_frame.f_code.co_name=='_export_market':result['failed_statistics']=dict(tb.tb_frame.f_locals['stats'])
  tb=tb.tb_next
result.update(wall_seconds=time.monotonic()-wall,cpu_seconds=time.thread_time()-cpu,metrics=dict(metrics),pid=os.getpid(),source_sha256=sha256(Path(m.__file__).read_bytes()).hexdigest(),peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
with (r/'profile.json').open('x') as handle:json.dump(result,handle,sort_keys=True,indent=2)
print(json.dumps(result),flush=True)
