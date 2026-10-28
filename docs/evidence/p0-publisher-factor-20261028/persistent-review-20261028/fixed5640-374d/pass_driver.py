import json,resource,time,sys,os
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
