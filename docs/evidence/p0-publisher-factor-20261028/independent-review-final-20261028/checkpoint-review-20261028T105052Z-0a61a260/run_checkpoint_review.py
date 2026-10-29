
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
