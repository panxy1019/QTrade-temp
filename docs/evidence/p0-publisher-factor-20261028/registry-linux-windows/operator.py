"""Hash-bound isolated STEP2 test copy; no production stores or service controls."""
import json,shlex
from datetime import UTC,datetime
from pathlib import Path
from hashlib import sha256
from authorized_cluster_read import connect

ROOT=Path('E:/QTrade-git')
B='/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade'
BASE='/root/qtrade-local/operator-evidence/graded-quality-458552d-20261028'
stamp=datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
R='/root/qtrade-local/operator-evidence/step2-factor-implementation-'+stamp
L=Path('E:/QTrade-temp/cluster-evidence')/Path(R).name
L.mkdir()
names=['src/qtrade/research/factor_registry.py','tests/test_factor_registry.py']
files={n:(ROOT/n).read_bytes() for n in names}
manifest={'base_revision':'458552de1fea1faf77d02dcd9ec69251a0c364b7',
 'candidate_files':{n:sha256(raw).hexdigest() for n,raw in files.items()},
 'scope':'ISOLATED_NONPRODUCTION_FACTOR_REGISTRY_TESTS','production_writes':False}
code='''
import shutil,json
from pathlib import Path
from hashlib import sha256
r=Path(REMOTE);base=Path(BASE)
assert shutil.disk_usage(DATA_MOUNT).free>100*1024**3
assert shutil.disk_usage(base).free>1024**3
m=json.loads((base/'source-manifest.json').read_bytes())
for group in ('files','independent_tests'):
 for name,digest in m[group].items():assert sha256((base/'source'/name).read_bytes()).hexdigest()==digest
r.mkdir(mode=0o700)
shutil.copytree(base/'source',r/'source',ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
'''.replace('Path(REMOTE)','Path('+repr(R)+')').replace('Path(BASE)','Path('+repr(BASE)+')').replace('DATA_MOUNT',repr(B))
c=connect()
try:
 _,out,err=c.exec_command(shlex.quote(B+'/venv/bin/python')+" -B - <<'PY'\n"+code+'\nPY',timeout=25)
 assert out.channel.recv_exit_status()==0,err.read().decode()[-300:]
 with c.open_sftp() as s:
  for name,raw in files.items():
   with s.open(R+'/source/'+name,'wx') as f:f.write(raw)
  with s.open(R+'/manifest.json','wx') as f:f.write(json.dumps(manifest,sort_keys=True).encode())
 verify='from pathlib import Path\nfrom hashlib import sha256\nimport json\nr=Path('+repr(R)+')\nm=json.loads((r/"manifest.json").read_bytes())\nfor n,h in m["candidate_files"].items(): assert sha256((r/"source"/n).read_bytes()).hexdigest()==h\n'
 cmd='cd '+shlex.quote(R+'/source')+' && env LIVE_TRADING_ENABLED=false PYTHONPATH=src:.:tests '+shlex.quote(B+'/venv/bin/python')
 for label,suffix in [('verify'," -B - <<'PY'\n"+verify+'\nPY'),('tests',' -B -m pytest -q -o addopts= tests/test_factor_registry.py tests/test_factor_search.py tests/test_signal_baselines.py --junitxml='+shlex.quote(R+'/tests.xml'))]:
  _,out,err=c.exec_command(cmd+suffix,timeout=60)
  raw=out.read()+err.read();rc=out.channel.recv_exit_status();(L/(label+'.log')).write_bytes(raw)
  print(json.dumps({'step':label,'exit':rc,'output':raw.decode()[-1800:]}),flush=True)
  if rc:raise SystemExit(rc)
 with c.open_sftp() as s:
  for name in ('manifest.json','tests.xml'):s.get(R+'/'+name,str(L/name))
 print('EVIDENCE='+str(L))
finally:c.close()
