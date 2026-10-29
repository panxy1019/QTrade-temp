"""Run existing D with the frozen old ACTIVE/approval; never enqueue or activate."""
import hashlib,json,shlex,sys,subprocess
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,'E:/QTrade-temp')
from authorized_cluster_read import connect
root=Path(__file__).parent;repo=Path('E:/QTrade-git')
base='/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade'
revision='9846a7f30774d6ae4b4d41f02b493c5246be87d9'
expected=hashlib.sha256(subprocess.check_output(['git','show',revision+':scripts/audit_qlib_candidate_transition.py'],cwd=repo)).hexdigest()
code=r'''
import json,hashlib
from pathlib import Path
from qtrade.monitoring.data_freshness import AUTHORITY,signed
from scripts.audit_qlib_candidate_transition import audit
base=Path(BASE_VALUE);local=Path('/root/qtrade-local')
path=Path.cwd()/'scripts/audit_qlib_candidate_transition.py'
assert hashlib.sha256(path.read_bytes()).hexdigest()==EXPECTED_VALUE
model='98d27455a7cb224ef3ecbd7cf850455f386fbb93baf8137a4d789e142ab91795'
index=json.loads((base/'online-research/control/models'/(model+'.json')).read_bytes())
result=audit(base,local/'online-models.sqlite',local/'workflow.sqlite',local/'qlib-versions.sqlite',
 model,'b82baaac956745f4e02a520a35b2aa277b0037510a4d164d798db803ce77e0d5',
 '29589b4486a09159eb3a07153c54fba609fbc80b52c168dd665cd46660be8487',
 '6f1ec61bda1a28d918dd331ad0e0ae6ea7ebcc39320f8e4dade424bf726bd238')
assert hashlib.sha256(path.read_bytes()).hexdigest()==EXPECTED_VALUE
print(json.dumps(signed({**AUTHORITY,'kind':'ACTUAL_D_OCT28_FROZEN_RIDGE_AND_OLD_ACTIVE_SCOPE','audit':result,
 'auditor_revision':'9846a7f30774d6ae4b4d41f02b493c5246be87d9','auditor_source_sha256':EXPECTED_VALUE,
 'model_index':index,'production_mutations':False,'model_activated':False,'jobs_enqueued':False}),sort_keys=True))
'''.replace('BASE_VALUE',repr(base)).replace('EXPECTED_VALUE',repr(expected))
with (root/'remote-D-fixed-scope.py').open('x',encoding='utf-8') as f:f.write(code)
client=connect()
try:
 stdin,stdout,stderr=client.exec_command('cd '+shlex.quote(base+'/releases/'+revision)+' && env LIVE_TRADING_ENABLED=false PYTHONPATH=src:. '+shlex.quote(base+'/venv/bin/python')+' -B -',timeout=90)
 stdin.write(code);stdin.flush();stdin.channel.shutdown_write();raw=stdout.read();err=stderr.read();rc=stdout.channel.recv_exit_status()
finally:client.close()
path=root/('D-fixed-scope-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+('.json' if rc==0 else '.error'))
with path.open('xb') as f:f.write(raw if rc==0 else err)
if rc:print({'exit':rc,'failure':str(path)});raise SystemExit(rc)
v=json.loads(raw)
print(json.dumps({'evidence':str(path),'state':v['audit']['state'],'errors':v['audit']['errors'],'model':v['audit']['model_id'],'checked_at':v['audit']['checked_at'],'archive':v['model_index']['archive'],'sha256':hashlib.sha256(raw).hexdigest()}))
