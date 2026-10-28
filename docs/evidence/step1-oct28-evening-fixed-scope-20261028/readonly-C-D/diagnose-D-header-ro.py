"""Inspect only frozen D header/preconditions, retaining the real failure."""
import json,shlex,sys,hashlib
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,'E:/QTrade-temp')
from authorized_cluster_read import connect
base='/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade'
code=r'''
import json,traceback
from pathlib import Path
from datetime import datetime,timezone
from qtrade.monitoring.data_freshness import AUTHORITY,signed,Calendar
from scripts.audit_qlib_candidate_transition import verify
b=Path(BASE);l=Path('/root/qtrade-local');model='98d27455a7cb224ef3ecbd7cf850455f386fbb93baf8137a4d789e142ab91795'
index=json.loads((b/'online-research/control/models'/(model+'.json')).read_bytes())
header_path=b/index['archive']/'summary.json';header=json.loads(header_path.read_bytes())
error=None
try:verify(b,l/'online-models.sqlite',l/'workflow.sqlite',l/'qlib-versions.sqlite',model,
 'b82baaac956745f4e02a520a35b2aa277b0037510a4d164d798db803ce77e0d5',
 '29589b4486a09159eb3a07153c54fba609fbc80b52c168dd665cd46660be8487',
 '6f1ec61bda1a28d918dd331ad0e0ae6ea7ebcc39320f8e4dade424bf726bd238',
 Calendar(Path.cwd()/'configs/observer_calendar_2026.json'),datetime.now(timezone.utc),{}, {})
except Exception as exc:error={'type':type(exc).__name__,'code':str(exc),'traceback':traceback.format_exc()}
signals=json.loads((b/'workflow/public/signals.json').read_bytes())['manifest']
print(json.dumps(signed({**AUTHORITY,'kind':'READ_ONLY_D_ARCHIVE_HEADER_DIAGNOSIS',
 'checked_at':datetime.now(timezone.utc).isoformat(),'header_path':str(header_path),
 'header_file_sha256':__import__('hashlib').sha256(header_path.read_bytes()).hexdigest(),
 'header_keys':sorted(header),'header':{k:header.get(k) for k in ['run_id','model','data_end','source_revision','completed_at','rolling_plan','dataset_sha256']},
 'error':error,'current_signal':{k:signals.get(k) for k in ['signal_family_id','data_end','generated_at','bindings']},
 'read_only':True,'auditor_modified':False,'production_mutations':False}),sort_keys=True))
'''.replace('Path(BASE)','Path('+repr(base)+')')
client=connect()
try:
 stdin,stdout,stderr=client.exec_command('cd '+shlex.quote(base+'/releases/9846a7f30774d6ae4b4d41f02b493c5246be87d9')+' && env LIVE_TRADING_ENABLED=false PYTHONPATH=src:. '+shlex.quote(base+'/venv/bin/python')+' -B -',timeout=90)
 stdin.write(code);stdin.flush();stdin.channel.shutdown_write();raw=stdout.read();err=stderr.read();rc=stdout.channel.recv_exit_status()
finally:client.close()
path=Path(__file__).parent/('D-header-diagnosis-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+('.json' if rc==0 else '.error'))
with path.open('xb') as f:f.write(raw if rc==0 else err)
if rc:print({'exit':rc,'failure':str(path)});raise SystemExit(rc)
v=json.loads(raw)
print(json.dumps({'evidence':str(path),'header':v['header'],'error':v['error'],'signals':v['current_signal'],'sha256':hashlib.sha256(raw).hexdigest()}))
