import json,sqlite3,os,importlib.util
from pathlib import Path
from contextlib import closing
from datetime import UTC,datetime
from hashlib import sha256
from qtrade.monitoring.data_freshness import AUTHORITY,signed
base=Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade');local=Path('/root/qtrade-local')
spec=importlib.util.spec_from_file_location('readonly_handover_helpers',local/'operator-evidence/forward-handover-9846a7f-20261028/directed_handover.py');h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
assert os.environ.get('LIVE_TRADING_ENABLED','false').lower()=='false'
started=datetime.now(UTC);before=h.context()
def document(relative):
 p=base/relative
 if not p.is_file(): return {'path':str(p),'exists':False}
 assert not p.is_symlink() and p.stat().st_size<=4000000
 raw=p.read_bytes();value=json.loads(raw)
 valid=not isinstance(value,dict) or 'evidence_sha256' not in value or signed({k:v for k,v in value.items() if k!='evidence_sha256'})==value
 return {'path':str(p),'exists':True,'file_sha256':sha256(raw).hexdigest(),'bytes':len(raw),'signature_valid':valid,'document':value}
def ro(path):
 c=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('BEGIN');return c
def rows(c,sql,params=()): return [dict(row) for row in c.execute(sql,params).fetchall()]
files={p:document(p) for p in ['unified-data/qlib-schedule/service-state.json','unified-data/qlib-schedule/cycle.json','unified-data/qlib-schedule/days/2026-10-28/request.json','unified-data/qlib-schedule/days/2026-10-28/attempt.json','unified-data/qlib-schedule/days/2026-10-28/build-attempt.json','unified-data/qlib-schedule/days/2026-10-28/completed.json','unified-data/qlib-preparation/latest.json','workflow/public/online.json','workflow/public/signals.json','workflow/public/workflow.json','workflow/service-state.json']}
with closing(ro(local/'qlib-versions.sqlite')) as c:
 data={'head':rows(c,'SELECT * FROM data_heads'),'versions':rows(c,'SELECT * FROM data_versions ORDER BY admitted_at DESC LIMIT 12'),'events':rows(c,'SELECT * FROM data_events ORDER BY sequence DESC LIMIT 5')}
with closing(ro(local/'online-models.sqlite')) as c:
 models={'models':rows(c,'SELECT model_id,state,registered_at,activated_at,activation_count,json_extract(definition,\'$.training_data_version_id\') training_data_version_id FROM models ORDER BY registered_at DESC LIMIT 20'),'validations':rows(c,'SELECT * FROM model_validations ORDER BY rowid DESC LIMIT 20'),'predictions':rows(c,'SELECT * FROM prediction_tasks ORDER BY updated_at DESC LIMIT 10')}
with closing(ro(local/'workflow.sqlite')) as c:
 tables={r['name'] for r in rows(c,"SELECT name FROM sqlite_master WHERE type='table'")}
 workflow={'pending':rows(c,"SELECT id,state,updated,error,json_extract(request,'$.kind') kind FROM jobs WHERE state NOT IN ('COMPLETE','FAILED','REJECTED','CANCELLED') ORDER BY updated DESC LIMIT 20"),'validation_jobs':rows(c,"SELECT id,state,updated,error,json_extract(request,'$.kind') kind,json_extract(request,'$.model_id') model_id,json_extract(request,'$.data_version_id') data_version_id,result FROM jobs WHERE json_extract(request,'$.kind')='QLIB_MODEL_VALIDATION' ORDER BY updated DESC LIMIT 20"),'rolling_policy':rows(c,'SELECT * FROM rolling_policy LIMIT 5') if 'rolling_policy' in tables else []}
processes={}
for name,pid in [('forward',383859),('workflow',338258),('data',493083),('publisher',294214)]:
 p=Path('/proc')/str(pid)
 if not p.exists(): processes[name]={'pid':pid,'exists':False};continue
 env=dict(v.split(b'=',1) for v in (p/'environ').read_bytes().split(b'\0') if b'=' in v)
 processes[name]={'pid':pid,'exists':True,'cwd':str((p/'cwd').resolve()),'argv':[v.decode() for v in (p/'cmdline').read_bytes().split(b'\0') if v],'LIVE_TRADING_ENABLED':env.get(b'LIVE_TRADING_ENABLED',b'').decode(),'start_ticks':(p/'stat').read_text().rsplit(')',1)[1].split()[19]}
print(json.dumps(signed({**AUTHORITY,'kind':'STEP1_OCT28_RESUMED_READ_ONLY_CD_PREREQUISITES','started_at':started.isoformat(),'checked_at':datetime.now(UTC).isoformat(),'context_before':before,'context_after':h.context(),'files':files,'data':data,'models':models,'workflow':workflow,'processes':processes,'production_mutations':False,'auditors_not_invoked_in_this_snapshot':True}),ensure_ascii=False,sort_keys=True))
