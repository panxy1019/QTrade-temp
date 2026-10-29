import hashlib, importlib.util, io, json, shlex, tarfile
from pathlib import Path
from datetime import UTC,datetime
from uuid import uuid4

repo=Path('E:/QTrade-git')
old=Path('E:/QTrade-temp/factor-contract-s2a-20261028T094016Z-01e1eb42')
run='checkpoint-review-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
work=Path('E:/QTrade-temp')/run;work.mkdir()
remote='/root/qtrade-local/operator-evidence/'+run
paths=['scripts/unified_market_checkpoint.py','scripts/publish_unified_market.py',
       'src/qtrade/research/market_validation_receipts.py','tests/test_unified_market_checkpoints.py']
def h(raw):return hashlib.sha256(raw).hexdigest()
expected=json.loads((old/'package-manifest.json').read_bytes())['git_baseline_files']
overlays={}
with tarfile.open(work/'review-overlay.tar','w') as archive:
    for name in paths+['tests/test_unified_market_checkpoint_independent.py']:
        path=repo/name
        raw=path.read_bytes();info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o644
        archive.addfile(info,io.BytesIO(raw));overlays[name]={'sha256':h(raw),'bytes':len(raw)}
expected={**expected,**overlays}
manifest={'expected_files':expected,'base_commit':'f036bca197272df4b1f885dfe8165a27b67c36cd',
          'overlays':overlays,'baseline_sha256':h((old/'baseline.tar').read_bytes()),
          'overlay_sha256':h((work/'review-overlay.tar').read_bytes())}
(work/'source-manifest.json').write_text(json.dumps(manifest,indent=2))
operator=r'''
import os,sys,shutil,tarfile,hashlib,json,subprocess,time
from pathlib import Path
root=Path(sys.argv[1]);assert root.parent==Path('/root/qtrade-local/operator-evidence')
source=root/'source';source.mkdir();(root/'tmp').mkdir()
manifest=json.loads((root/'source-manifest.json').read_bytes())
def h(raw):return hashlib.sha256(raw).hexdigest()
def save(name,value):(root/name).write_text(json.dumps(value,indent=2))
res={}
for name,path in [('nfs',Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')),('local',root)]:
 s=os.statvfs(path);res[name]=s.f_bavail*s.f_frsize
save('resources-before.json',res);assert res['nfs']>=100*1024**3 and res['local']>=2*1024**3
baseline=Path('/root/qtrade-local/operator-evidence/factor-contract-s2a-20261028T094016Z-01e1eb42/baseline.tar')
assert h(baseline.read_bytes())==manifest['baseline_sha256']
assert h((root/'review-overlay.tar').read_bytes())==manifest['overlay_sha256']
for path in (baseline,root/'review-overlay.tar'):
 with tarfile.open(path) as archive:
  for member in archive.getmembers():
   assert (member.isdir() or member.isfile()) and (source/member.name).resolve().is_relative_to(source.resolve())
  archive.extractall(source,filter='data')
def verify(name):
 actual={p.relative_to(source).as_posix():{'sha256':h(p.read_bytes()),'bytes':p.stat().st_size} for p in source.rglob('*') if p.is_file()}
 save(name,{'matches':actual==manifest['expected_files'],'files':actual})
 assert actual==manifest['expected_files']
verify('source-before.json')
env={**os.environ,'PYTHONPATH':str(source/'src')+':'+str(source),'LIVE_TRADING_ENABLED':'false',
     'PYTHONDONTWRITEBYTECODE':'1','TMPDIR':str(root/'tmp')}
cmd=[sys.executable,'-B','-m','pytest','-p','no:cacheprovider','--basetemp='+str(root/'tmp/pytest'),
 'tests/test_unified_market_checkpoint_independent.py','--junitxml='+str(root/'independent-green.xml')]
clock=time.perf_counter()
with (root/'stdout.log').open('w') as out,(root/'stderr.log').open('w') as err:
 result=subprocess.run(cmd,cwd=source,env=env,stdout=out,stderr=err,timeout=120)
verify('source-after.json')
save('result.json',{'returncode':result.returncode,'elapsed_seconds':time.perf_counter()-clock,'command':cmd,'python':sys.version,'overlays':manifest['overlays']})
print((root/'result.json').read_text(),flush=True)
print((root/'stdout.log').read_text(),flush=True)
'''
(work/'run_checkpoint_review.py').write_text(operator)
spec=importlib.util.spec_from_file_location('authorized_review','E:/QTrade-temp/authorized_cluster_read.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);client=m.connect()
try:
 sftp=client.open_sftp()
 try:
  sftp.mkdir(remote)
  for name in ('review-overlay.tar','source-manifest.json','run_checkpoint_review.py'):
   sftp.put(str(work/name),remote+'/'+name)
  cmd='/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python -P -B '+shlex.quote(remote+'/run_checkpoint_review.py')+' '+shlex.quote(remote)
  _,out,err=client.exec_command(cmd,timeout=180)
  stdout,stderr=out.read().decode(),err.read().decode();status=out.channel.recv_exit_status()
  (work/'remote-stdout.log').write_text(stdout);(work/'remote-stderr.log').write_text(stderr)
  print('LOCAL_REVIEW_ROOT',work,flush=True);print('REMOTE_STATUS',status,flush=True)
  print(stdout,flush=True)
  if stderr: print(stderr,flush=True)
  for name in ('resources-before.json','source-before.json','source-after.json',
               'result.json','independent-green.xml','stdout.log','stderr.log'):
   try:sftp.get(remote+'/'+name,str(work/name))
   except FileNotFoundError:pass
  files=[{'file':p.name,'bytes':p.stat().st_size,'sha256':h(p.read_bytes())} for p in sorted(work.iterdir()) if p.is_file()]
  (work/'evidence-manifest.json').write_text(json.dumps({'remote_root':remote,'files':files,'operator_status':status},indent=2))
 finally:sftp.close()
finally:client.close()
