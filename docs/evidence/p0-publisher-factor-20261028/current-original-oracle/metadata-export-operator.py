
from pathlib import Path
from hashlib import sha256
from datetime import UTC,datetime
import sqlite3,json,gzip
from contextlib import closing
r=Path('/root/qtrade-local/operator-evidence/market-current-independent-oracle-20261028T051529Z');p=Path('/root/qtrade-local/operator-evidence/market-current-freeze-20261028T042337Z')
f=json.loads((p/'freeze.json').read_bytes());craw=(p/'snapshot/unified-data/public/unified.json').read_bytes()
assert sha256(craw).hexdigest()==f['catalog_raw_sha256']
u=json.loads(craw)['master']['universe_version'];rows=[]
with closing(sqlite3.connect((p/'frozen.sqlite').resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as c:
 c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
 for freq,days in f['windows'].items():
  rows.extend(c.execute("SELECT c.batch_id,c.symbol,c.frequency FROM collection_chunks c JOIN collection_batches b ON b.batch_id=c.batch_id WHERE c.universe_version=? AND c.frequency=? AND b.state='COMPLETE' AND substr(c.end_time,1,10)>=? AND substr(c.start_time,1,10)<=? AND NOT EXISTS (SELECT 1 FROM excluded_collection_chunks e WHERE e.batch_id=c.batch_id AND e.symbol=c.symbol) ORDER BY c.end_time,c.batch_id,c.symbol LIMIT ?",(u,freq,days[0],days[-1],600000-len(rows)+1)).fetchall())
  assert len(rows)<=600000
assert len(rows)==f['selected_chunks'] and len({row[0] for row in rows})==f['selected_batches']
raw=json.dumps({'scope':'READONLY_METADATA_ONLY_NO_RAW_LAKE_READ','checked_at':datetime.now(UTC).isoformat(),'freeze_evidence_sha256':f['evidence_sha256'],'columns':['batch_id','symbol','frequency'],'rows':rows},separators=(',',':')).encode()
out=r/'selected-chunk-metadata.json.gz'
with out.open('xb') as h:h.write(gzip.compress(raw,mtime=0))
print(json.dumps({'path':str(out),'rows':len(rows),'selected_batches':len({row[0] for row in rows}),'sha256':sha256(out.read_bytes()).hexdigest(),'uncompressed_sha256':sha256(raw).hexdigest(),'bytes':out.stat().st_size,'uncompressed_bytes':len(raw),'raw_lake_read':False}))
