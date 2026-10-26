import os,json,sqlite3,contextlib,uuid
from pathlib import Path
from datetime import datetime,UTC
from qtrade.research.data_versions import DataVersionCatalog
from qtrade.research.data_version_control import apply,read_catalog
from qtrade.monitoring.data_freshness import AUTHORITY,signed
from scripts.atomic_json import write_json_immutable
b=Path(os.environ['B']);source=Path('/root/qtrade-local/qlib-versions.sqlite');archives=b/'unified-data/qlib-versions'
root=Path('/root/qtrade-local/acceptance')/('rollback-'+uuid.uuid4().hex);root.mkdir(parents=True);db=root/'versions.sqlite'
original=read_catalog(source,archives)
with contextlib.closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as src,contextlib.closing(sqlite3.connect(db)) as dst:
 src.backup(dst)
 dst.execute('DELETE FROM process_owner');dst.commit()
# The cloned process lease is reset only in this isolated disposable database.
cat=DataVersionCatalog(db,archives)
def head():
 with sqlite3.connect(db) as c:
  v=c.execute("SELECT version_id FROM data_heads WHERE frequency='day'").fetchone()[0]
  e=c.execute('SELECT event_id FROM data_events ORDER BY sequence DESC LIMIT 1').fetchone()[0]
  return v,e
first,epoch=head()
with sqlite3.connect(db) as c:other=c.execute('SELECT version_id FROM data_versions WHERE version_id<>?',(first,)).fetchone()[0]
def request(target,current,event):return {'request_id':uuid.uuid4().hex,'version_id':target,'expected_head':current,'expected_event_id':event,'actor':'isolated-acceptance','reason':'RESEARCH_REPRODUCTION'}
a=request(other,first,epoch);r1=apply(cat,a,now=datetime.now(UTC));assert head()[0]==other
r1replay=apply(cat,a,now=datetime.now(UTC));assert r1==r1replay
second,epoch2=head();assert epoch2!=epoch
r2=apply(cat,request(first,second,epoch2),now=datetime.now(UTC));third,epoch3=head();assert third==first and epoch3 not in (epoch,epoch2)
stale=apply(cat,request(other,first,epoch),now=datetime.now(UTC));assert head()==(third,epoch3)
replay=apply(cat,a,now=datetime.now(UTC));assert replay==r1 and head()==(third,epoch3)
# Production source has no writes; verify original head/event still identical.
final=read_catalog(source,archives)
assert original==final
out=signed({**AUTHORITY,'observed_at':datetime.now(UTC).isoformat(),'source_revision':'e107eb9c74001822dda220b1e6ca96a55ee00ef6','scope':'ISOLATED_SQLITE_COPY_WITH_REAL_ARCHIVES','live_source_catalog_unchanged':True,'round_trip_restored_version':True,'round_trip_epoch_changed':True,'idempotent_replay_preserved':True,'stale_epoch_head_unchanged':True,'receipts':[r1,r2,stale]})
write_json_immutable(b/'online-research/step1-isolated-rollback-e107eb9.json',out)
print(json.dumps(out),flush=True)
