"""Read-only facts + unchanged C auditor; only new operator/local evidence writes."""
import json,hashlib,shlex,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,'E:/QTrade-temp')
from authorized_cluster_read import connect
root=Path(__file__).parent
repo=Path('E:/QTrade-git')
revision='9846a7f30774d6ae4b4d41f02b493c5246be87d9'
base='/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade'
source=base+'/releases/'+revision
paths=['scripts/audit_qlib_session_transition.py','scripts/qlib_public_delivery.py',
       'scripts/audit_qlib_candidate_transition.py','scripts/run_qlib_worker.py',
       'src/qtrade/research/session_acceptance.py','src/qtrade/research/model_promotion_policy.py']
expected={p:hashlib.sha256(subprocess.check_output(['git','show',revision+':'+p],cwd=repo)).hexdigest() for p in paths}
code=r'''
import json,sqlite3,hashlib,sys,contextlib,io,traceback
from pathlib import Path
from datetime import datetime,timezone
from uuid import uuid4
from qtrade.monitoring.data_freshness import AUTHORITY,signed
from qtrade.research.prediction_registry import active_approval
BASE=Path(BASE_VALUE);LOCAL=Path('/root/qtrade-local');EXPECTED=EXPECTED_VALUE
source=Path.cwd()
def hashes():return {p:hashlib.sha256((source/p).read_bytes()).hexdigest() for p in EXPECTED}
assert hashes()==EXPECTED
def ro(path):
 c=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('BEGIN');return c
def rows(c,sql,args=()):return [dict(r) for r in c.execute(sql,args).fetchall()]
model='98d27455a7cb224ef3ecbd7cf850455f386fbb93baf8137a4d789e142ab91795'
later='b82baaac956745f4e02a520a35b2aa277b0037510a4d164d798db803ce77e0d5'
previous='09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407'
prediction='f9a443b2c89aed5c9a43330a759ce60f22a0ebed7e4147188bbf0e7721fc0b10'
started=datetime.now(timezone.utc).isoformat()
with contextlib.closing(ro(LOCAL/'online-models.sqlite')) as c:
 facts={'candidate':rows(c,'SELECT * FROM models WHERE model_id=?',(model,)),
 'active':rows(c,'SELECT * FROM active_model'), 'approval':active_approval(c),
 'candidate_validations':rows(c,'SELECT * FROM model_validations WHERE model_id=?',(model,)),
 'model_events':rows(c,'SELECT * FROM model_events WHERE model_id IN (?,?) ORDER BY sequence',(model,'68d9287efc49c24280ab8c6e4df5c69724c0e6e9876396e7312a6b771405a81c')),
 'promotion_policy':rows(c,'SELECT * FROM model_promotion_policy'),
 'promotion_commands':rows(c,'SELECT * FROM model_promotion_commands'),
 'promotion_approvals':rows(c,'SELECT * FROM model_promotion_approvals'),
 'model_commands':rows(c,'SELECT * FROM model_commands ORDER BY rowid DESC LIMIT 8')}
with contextlib.closing(ro(LOCAL/'workflow.sqlite')) as c:
 facts['candidate_jobs']=rows(c,"SELECT * FROM jobs WHERE json_extract(request,'$.kind')='QLIB_MODEL_VALIDATION' AND json_extract(request,'$.model_id')=? ORDER BY rowid",(model,))
 facts['pending_jobs']=rows(c,"SELECT * FROM jobs WHERE state NOT IN ('COMPLETE','FAILED','CANCELLED','INTERRUPTED','REJECTED') ORDER BY rowid LIMIT 20")
 signal=json.loads((BASE/'workflow/public/signals.json').read_bytes())['manifest']['signal_family_id']
 candidates=rows(c,"SELECT id,state,result FROM jobs WHERE json_extract(result,'$.signal_family_id')=?",(signal,))
 assert len(candidates)==1 and candidates[0]['state']=='COMPLETE'
 signal_root=BASE/'workflow/jobs'/candidates[0]['id']/'output'/signal
facts['candidate_expected_new_job_id']=signed({'kind':'QLIB_MODEL_VALIDATION','model_id':model,'data_version_id':later,'source_revision':'096387b8088e4361c9e5cd9cd2c165e21819fead'})['evidence_sha256'][:32]
facts['scheduler_source_sha256']=EXPECTED['scripts/run_qlib_worker.py']
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
out=LOCAL/'operator-evidence'/('step1-evening-C-'+stamp+'-'+uuid4().hex[:8]);out.mkdir(mode=0o700)
from scripts import audit_qlib_session_transition as m
from scripts import qlib_public_delivery as p
captured_http=[];private=[]
get=p._get
def capture_get(*args,**kwargs):
 value=get(*args,**kwargs);captured_http.append(value);return value
p._get=capture_get
verify=m.verify_public_delivery
def capture_private(*args,**kwargs):
 private.append(args[0]);return verify(*args,**kwargs)
m.verify_public_delivery=capture_private
argv=['scripts.audit_qlib_session_transition','--previous-version',str(BASE/'unified-data/qlib-versions/versions'/previous),
 '--current-version',str(BASE/'unified-data/qlib-versions/versions'/later),
 '--automatic-completion',str(BASE/'unified-data/qlib-schedule/days/2026-10-28/completed.json'),
 '--prediction',str(BASE/'online-research/control/predictions'/prediction/'output'/prediction),
 '--signals',str(signal_root),'--database',str(LOCAL/'qlib-versions.sqlite'),
 '--output',str(out/'C-with-public.json'),'--verify-production',
 '--expected-production-revision','ae33a7c8c27ea721eb3be1e4430b562a44b67f91']
sys.argv=argv
stdout=io.StringIO();error=None
try:
 with contextlib.redirect_stdout(stdout):m.main()
except Exception as exc:error={'type':type(exc).__name__,'code':str(exc),'traceback':traceback.format_exc()}
with contextlib.closing(ro(LOCAL/'online-models.sqlite')) as c:after={'active':rows(c,'SELECT * FROM active_model'),'approval':active_approval(c)}
assert hashes()==EXPECTED
report=signed({**AUTHORITY,'kind':'ACTUAL_OCT28_FROZEN_295_C_AND_RO_D_SCHEDULER_REASONS','started_at':started,
 'checked_at':datetime.now(timezone.utc).isoformat(),'source_revision':'9846a7f30774d6ae4b4d41f02b493c5246be87d9',
 'source_files_before_after':EXPECTED,'facts_before':facts,'facts_after':after,
 'argv':argv,'operator_output_root':str(out),'production_mutations':False,
 'private_result':private[0] if private else None,'public_result':json.loads(stdout.getvalue()) if stdout.getvalue() else None,
 'public_http_captures':captured_http,'error':error,
 'capture_hook_scope':'ORIGINAL_AUDITOR_MAIN_ARGUMENTS_AND_CHECKS_UNCHANGED; OBSERVE_PRIVATE_RESULT_AND_ORIGINAL_HTTP_GET_RETURN_VALUES_ONLY',
 'candidate_jobs_enqueued':False,'candidate_activated':False})
print(json.dumps(report,sort_keys=True))
'''.replace('BASE_VALUE',repr(base)).replace('EXPECTED_VALUE',repr(expected))
with (root/'remote-c-and-d-reasons.py').open('x',encoding='utf-8') as f:f.write(code)
client=connect()
try:
 stdin,stdout,stderr=client.exec_command('cd '+shlex.quote(source)+' && env LIVE_TRADING_ENABLED=false PYTHONPATH=src:. '+shlex.quote(base+'/venv/bin/python')+' -B -',timeout=300)
 stdin.write(code);stdin.flush();stdin.channel.shutdown_write();raw=stdout.read();err=stderr.read();rc=stdout.channel.recv_exit_status()
finally:client.close()
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
output=root/('C-and-D-reasons-'+stamp+('.json' if rc==0 else '.error'))
with output.open('xb') as f:f.write(raw if rc==0 else err)
if rc:print(json.dumps({'exit':rc,'failure':str(output)}));raise SystemExit(rc)
value=json.loads(raw)
print(json.dumps({'evidence':str(output),'checked_at':value['checked_at'],'private_state':(value['private_result'] or {}).get('state'),'private_model':(value['private_result'] or {}).get('approved_model_id'),'public_state':(value['public_result'] or {}).get('state'),'error':value['error'],'http_count':len(value['public_http_captures']),'active':value['facts_after']['active'],'candidate_job_count':len(value['facts_before']['candidate_jobs']),'candidate_new_job':value['facts_before']['candidate_expected_new_job_id']},ensure_ascii=False))
