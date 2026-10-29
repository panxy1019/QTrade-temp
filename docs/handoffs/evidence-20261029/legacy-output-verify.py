import json,signal,time
from pathlib import Path
from datetime import datetime,timezone
from qtrade.research.strategy_comparison import verify_comparison
r=Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/online-research/strategy-comparisons/f6b9e52b1c30c4f46e0733d02bb4e800450a9acbb67a8954d63a2bb857dde943')
def deadline(*args):raise TimeoutError('READONLY_ARCHIVE_DIAGNOSTIC_DEADLINE')
signal.signal(signal.SIGALRM,deadline);signal.alarm(30);t=time.monotonic()
try:
 d=verify_comparison(r);manifest=json.loads((r/'manifest.json').read_bytes());result={'at':datetime.now(timezone.utc).isoformat(),'state':'VERIFIED_EXISTING_OUTPUT_ARCHIVE','comparison_id':d['comparison_id'],'files_verified':len(manifest['files']),'bytes_verified':sum((r/n).stat().st_size for n in manifest['files']),'seconds':time.monotonic()-t,'read_only':True,'recomputed_backtest':False,'training_input_archives_reverified':False,'new_registry_lineage_proven':False}
except Exception as e:result={'at':datetime.now(timezone.utc).isoformat(),'state':'NOT_VERIFIED','error_class':type(e).__name__,'seconds':time.monotonic()-t,'read_only':True}
finally:signal.alarm(0)
print(json.dumps(result,sort_keys=True))
