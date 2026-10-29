from authorized_cluster_read import connect
from pathlib import Path
from datetime import UTC, datetime
from hashlib import sha256
import json, shlex

local=Path('E:/QTrade-temp')
package=local/'publisher-v6-actual-bundle-20261028T114221Z-0eec8c94'
remote='/root/qtrade-local/operator-evidence/publisher-handover-fba51aad-20261028T113923Z'
names=['publisher_actual_evidence_v6_20261028.py','publisher_actual_composite_v6_20261028.py','publisher_handover_v6_preparation_20261028.py','publisher_handover_bridge_v6_20261028.py','test_publisher_actual_evidence_v6_20261028.py','test_publisher_actual_composite_v6_20261028.py']
py='/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python'
c=connect()
try:
 s=c.open_sftp()
 for directory in [remote+'/gate-evidence',remote+'/v6-tools']:
  try:s.mkdir(directory,0o700)
  except OSError:s.stat(directory)
 for name in ['manifest.json','gates.json']:
  target=remote+'/'+name
  try:
   with s.open(target,'rb') as f:assert f.read()==(package/name).read_bytes()
  except FileNotFoundError:s.put(str(package/name),target);s.chmod(target,0o600)
 for p in (package/'gate-evidence').iterdir():
  target=remote+'/gate-evidence/'+p.name
  try:
   with s.open(target,'rb') as f:assert sha256(f.read()).hexdigest()==sha256(p.read_bytes()).hexdigest()
  except FileNotFoundError:s.put(str(p),target);s.chmod(target,0o600)
 for name in names:
  target=remote+'/v6-tools/'+name
  try:
   with s.open(target,'rb') as f:assert f.read()==(local/name).read_bytes()
  except FileNotFoundError:s.put(str(local/name),target);s.chmod(target,0o600)
 s.close()
 code='''import os,subprocess,json,hashlib
from pathlib import Path
r=Path(REMOTE); t=r/'v6-tools'
before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in t.glob('*.py')}
env=dict(os.environ,LIVE_TRADING_ENABLED='false',PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',ACTUAL_V6_PRIVATE_PACKAGE=str(r))
commands=[['-B',str(t/'publisher_handover_v6_preparation_20261028.py'),'--check','--manifest',str(r/'manifest.json'),'--gates',str(r/'gates.json')],['-B','-m','pytest','-q','-c','/dev/null','-p','no:cacheprovider','--noconftest','--import-mode=importlib','--junitxml='+str(r/'v6-linux-47.xml'),str(t/'test_publisher_actual_evidence_v6_20261028.py'),str(t/'test_publisher_actual_composite_v6_20261028.py')]]
results=[]
for i,args in enumerate(commands):
 p=subprocess.run([__PY_EXEC__,*args],cwd=t,env=env,capture_output=True,timeout=120)
 (r/('v6-linux-'+str(i)+'.stdout')).write_bytes(p.stdout);(r/('v6-linux-'+str(i)+'.stderr')).write_bytes(p.stderr)
 results.append({'index':i,'returncode':p.returncode,'stdout_sha256':hashlib.sha256(p.stdout).hexdigest(),'stderr_sha256':hashlib.sha256(p.stderr).hexdigest(),'tail':p.stdout.decode()[-500:]})
 if p.returncode:break
after={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in t.glob('*.py')};assert before==after
out={'source_before':before,'source_after':after,'results':results,'production_mutations':False}
(r/'v6-linux-final-check.json').write_text(json.dumps(out,sort_keys=True))
print(json.dumps(out,sort_keys=True))
'''.replace('REMOTE',repr(remote)).replace('__PY_EXEC__',repr(py))
 stdin,out,err=c.exec_command(shlex.quote(py)+' -B -',timeout=180)
 stdin.write(code);stdin.channel.shutdown_write();raw=out.read();errors=err.read();rc=out.channel.recv_exit_status()
 dest=local/'cluster-evidence'/('publisher-v6-linux-final-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ'));dest.mkdir()
 (dest/'output.json').write_bytes(raw);(dest/'stderr.txt').write_bytes(errors)
 s=c.open_sftp()
 for name in ['v6-linux-47.xml','v6-linux-0.stdout','v6-linux-0.stderr','v6-linux-1.stdout','v6-linux-1.stderr','v6-linux-final-check.json']:
  try:s.get(remote+'/'+name,str(dest/name))
  except FileNotFoundError:pass
 s.close();print(json.dumps({'local_evidence':str(dest),'returncode':rc}));print(raw.decode()[-4000:])
 if rc:print(errors.decode()[-1000:]);raise SystemExit(rc)
finally:c.close()
