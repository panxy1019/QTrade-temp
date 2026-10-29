import json,sqlite3,os,importlib.util
from pathlib import Path
from contextlib import closing
from datetime import UTC,datetime
from hashlib import sha256
from qtrade.monitoring.data_freshness import AUTHORITY,signed
base=Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade');local=Path('/root/qtrade-local')
assert os.environ.get('LIVE_TRADING_ENABLED','false').lower()=='false'
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
from qtrade.research.prediction_registry import active_approval
EXPECTED_ACTIVE='68d9287efc49c24280ab8c6e4df5c69724c0e6e9876396e7312a6b771405a81c'
EXPECTED_APPROVAL='750de28e05933013e4cacf828a10ee28f638861d7d3adbc6ef7566f0d09cd510'
def context():
 with closing(ro(local/'online-models.sqlite')) as c:
  approval=active_approval(c)
  assert approval is not None and approval['model_id']==EXPECTED_ACTIVE and approval['evidence_sha256']==EXPECTED_APPROVAL
  states={x['model_id']:x['state'] for x in rows(c,'SELECT model_id,state FROM models')}
 with closing(ro(local/'qlib-versions.sqlite')) as c:
  head=rows(c,"SELECT * FROM data_heads WHERE frequency='day'")[0]['version_id']
  event=rows(c,'SELECT event_id FROM data_events WHERE version_id=? ORDER BY sequence DESC LIMIT 1',(head,))[0]['event_id']
 return {'active':EXPECTED_ACTIVE,'approval_sha256':EXPECTED_APPROVAL,'data_version':head,'data_event_id':event,'model_states':states}
started=datetime.now(UTC);before=context()
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
print(json.dumps(signed({**AUTHORITY,'kind':'STEP1_OCT28_CURRENT_68_READ_ONLY_CD_PREREQUISITES_NOT_OLD_SCOPE_REPLACEMENT','started_at':started.isoformat(),'checked_at':datetime.now(UTC).isoformat(),'context_before':before,'context_after':context(),'files':files,'data':data,'models':models,'workflow':workflow,'processes':processes,'production_mutations':False,'auditors_not_invoked_in_this_snapshot':True}),ensure_ascii=False,sort_keys=True))
