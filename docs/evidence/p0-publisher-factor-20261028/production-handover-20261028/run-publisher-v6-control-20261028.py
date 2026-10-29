from authorized_cluster_read import connect
from pathlib import Path
from datetime import UTC, datetime
from hashlib import sha256
import argparse,json,shlex

parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['prepare','advance','observe']);args=parser.parse_args()
root='/root/qtrade-local/operator-evidence/publisher-handover-fba51aad-20261028T113923Z'
local=Path('E:/QTrade-temp');caller=local/'publisher_v6_control_call_20261028.py'
expected='9293cd719f398dcdd5c8603a89a6b4a9e77eb1598ae7766f1141544ce61f3e8c'
if sha256(caller.read_bytes()).hexdigest()!=expected:raise ValueError('FINAL_CALLER_CHANGED')
stamp=datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ');name='control-'+args.mode+'-'+stamp
py='/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python'
c=connect()
try:
 s=c.open_sftp();target=root+'/'+caller.name
 try:
  with s.open(target,'rb') as f:
   if sha256(f.read()).hexdigest()!=expected:raise ValueError('REMOTE_CALLER_CHANGED')
 except FileNotFoundError:s.put(str(caller),target);s.chmod(target,0o600)
 s.close()
 code='''import os,json,subprocess,hashlib
from pathlib import Path
r=Path(__ROOT__);name=__NAME__
release='/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/releases/fba51aade2ee2410dda087e95cf054e53375ed3a'
env=dict(os.environ,LIVE_TRADING_ENABLED='false',PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=release+'/src:'+release,PYTHONNOUSERSITE='1')
p=subprocess.run([__PY_EXEC__,'-B',str(r/'publisher_v6_control_call_20261028.py'),__MODE__],env=env,capture_output=True,timeout=180)
for suffix,raw in [('stdout',p.stdout),('stderr',p.stderr)]:
 path=r/(name+'.'+suffix)
 with path.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
print(json.dumps({'returncode':p.returncode,'stdout_sha256':hashlib.sha256(p.stdout).hexdigest(),'stderr_sha256':hashlib.sha256(p.stderr).hexdigest(),'name':name}))
'''.replace('__ROOT__',repr(root)).replace('__NAME__',repr(name)).replace('__PY_EXEC__',repr(py)).replace('__MODE__',repr(args.mode))
 stdin,out,err=c.exec_command(shlex.quote(py)+' -B -',timeout=210);stdin.write(code);stdin.channel.shutdown_write()
 raw=out.read();errors=err.read();rc=out.channel.recv_exit_status()
 dest=local/'cluster-evidence'/name;dest.mkdir();(dest/'driver.stdout').write_bytes(raw);(dest/'driver.stderr').write_bytes(errors)
 s=c.open_sftp()
 for suffix in ['stdout','stderr']:
  try:s.get(root+'/'+name+'.'+suffix,str(dest/('result.'+suffix)))
  except FileNotFoundError:pass
 s.close()
 print(json.dumps({'evidence':str(dest),'driver_returncode':rc,'metadata':json.loads(raw) if raw else None}))
 if (dest/'result.stdout').exists():
  result=(dest/'result.stdout').read_bytes()
  if result:
   doc=json.loads(result);r=doc['result'];control=r.get('controls',{});print(json.dumps({'clock':doc['checked_at'],'mode':doc['mode'],'state':r.get('state'),'new_owner':r.get('new_owner'),'comparisons':r.get('controls_comparison'),'control_context':control.get('context'),'tracked_owners':control.get('owners'),'controls_sha256':sha256(json.dumps(control,sort_keys=True).encode()).hexdigest() if control else None},sort_keys=True))
 if (dest/'result.stderr').exists():
  e=(dest/'result.stderr').read_text()
  if e:print(e[-2000:])
 if rc:raise SystemExit(rc)
 if raw and json.loads(raw)['returncode']:raise SystemExit(json.loads(raw)['returncode'])
finally:c.close()
