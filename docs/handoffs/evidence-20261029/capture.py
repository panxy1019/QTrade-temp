from pathlib import Path
from contextlib import closing
from datetime import datetime,timezone
from hashlib import sha256
import json,sqlite3,shutil
B=Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade');L=Path('/root/qtrade-local')
def ro(path):
 c=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');return c
with closing(ro(L/'qlib-versions.sqlite')) as c:
 heads=[dict(x) for x in c.execute("SELECT h.frequency,v.version_id,v.actual_end,v.admitted_at FROM data_heads h JOIN data_versions v ON v.version_id=h.version_id LIMIT 4")]
 versions=[dict(x) for x in c.execute('SELECT version_id,actual_end,admitted_at FROM data_versions ORDER BY admitted_at DESC LIMIT 4')]
with closing(ro(L/'online-models.sqlite')) as c:active=[dict(x) for x in c.execute('SELECT m.model_id,m.state,m.activated_at FROM active_model a JOIN models m ON a.model_id=m.model_id WHERE a.singleton=1')]
scopes=[]
for identity in dict.fromkeys([x['version_id'] for x in heads]+['09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407']):
 root=B/'unified-data/qlib-versions/versions'/identity
 raw=(root/'version.json').read_bytes();assert len(raw)<8000000;v=json.loads(raw);d=json.loads((root/'dataset.json').read_bytes());supported=(root/'provider/instruments/workflow_supported.txt').read_text().splitlines();calendar=(root/'provider/calendars/day.txt').read_text().splitlines()
 scopes.append({'version_id':identity,'version_raw_sha256':sha256(raw).hexdigest(),'root':str(root),'actual_end':v['actual_end'],'state':v['state'],'current_symbols':len(v['current_symbols']),'historical_symbols':len(v['historical_symbols']),'blocked_symbols':v['blocked_symbols'],'manifest_file_count':len(v['files']),'supported_instruments':len(supported),'supported_universe_raw_sha256':sha256((root/'provider/instruments/workflow_supported.txt').read_bytes()).hexdigest(),'calendar_start':calendar[0],'calendar_last':calendar[-1],'calendar_actual_sessions':sum(x<=v['actual_end'] for x in calendar),'dataset_accepted_symbols':d['accepted_symbols'],'stock_starts_min':min(x['start'] for x in d['stocks']),'stock_starts_max':max(x['start'] for x in d['stocks']),'subset_891_is_not_rectangular_certification':True,'full_verify_version_rerun':False})
processes=[]
for pid in (647839,383859):
 p=Path('/proc')/str(pid);exists=p.exists();processes.append({'previous_owner_pid':pid,'exists':exists,'start_ticks':(p/'stat').read_text().rsplit(')',1)[1].split()[19] if exists else None,'cwd':str((p/'cwd').resolve()) if exists else None})
print(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'heads':heads,'recent_versions':versions,'active':active,'scopes':scopes,'previous_owners':processes,'nfs_free_bytes':shutil.disk_usage(B).free,'local_free_bytes':shutil.disk_usage(L).free,'read_only':True,'services_changed':False,'jobs_submitted':False,'secrets_read_or_logged':False},sort_keys=True))
