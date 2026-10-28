import json,time,sqlite3,io
from pathlib import Path
from hashlib import sha256
import pyarrow.parquet as pq
from scripts.unified_store import UnifiedBatchStore,_export_rows,FIELDS
r=Path('/root/qtrade-local/operator-evidence/market-parquet-sample-20261028T074654Z');f=Path('/root/qtrade-local/operator-evidence/market-current-freeze-20261028T070943Z')
c=sqlite3.connect((f/'frozen.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
ids=[row[0] for row in c.execute("SELECT DISTINCT batch_id FROM collection_batches WHERE state='COMPLETE' ORDER BY batch_id LIMIT 200")];c.close()
st=UnifiedBatchStore(f/'snapshot/unified-data/lake')
materials=[st._batch_material(x) for x in ids]
raw=[x._normalized for x in materials if x._normalized is not None]
def decode_names(table):
 table.validate(full=True)
 names=FIELDS.names
 columns=[col.to_pylist() if name=='source_hashes' else col.to_numpy(zero_copy_only=False) for name,col in zip(names,table.columns,strict=True)]
 return [dict(zip(names,row,strict=True)) for row in zip(*columns,strict=True)]
results=[]
for repetition in range(3):
 for threaded in ([True,False] if repetition%2==0 else [False,True]):
  t=time.monotonic();tables=[pq.ParquetFile(io.BytesIO(x)).read(use_threads=threaded) for x in raw]
  elapsed=time.monotonic()-t
  t=time.monotonic();original=[_export_rows(x) for x in tables];decode=time.monotonic()-t
  t=time.monotonic();candidate=[decode_names(x) for x in tables];names=time.monotonic()-t
  assert original==candidate
  results.append(dict(repetition=repetition,threaded=threaded,parquet_seconds=elapsed,decode_original_seconds=decode,decode_names_seconds=names,rows=sum(t.num_rows for t in tables)))
value={'sample_batches':len(raw),'results':results,'scope':'ISOLATED_SAMPLE_ONLY_NOT_FULL_EXPORT_OR_PRODUCTION'}
(r/'profile.json').write_text(json.dumps(value,indent=2));print(json.dumps(value),flush=True)
