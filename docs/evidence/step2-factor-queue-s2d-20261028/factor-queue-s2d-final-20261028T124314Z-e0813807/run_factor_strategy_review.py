
import gzip,hashlib,json,os,resource,subprocess,sys,tarfile,time
from pathlib import Path
root=Path(sys.argv[1]);assert root.parent==Path('/root/qtrade-local/operator-evidence')
source=root/'source';source.mkdir();(root/'tmp').mkdir()
manifest=json.loads((root/'package-manifest.json').read_bytes())
def h(raw):return hashlib.sha256(raw).hexdigest()
def save(name,value):(root/name).write_text(json.dumps(value,indent=2)+'\n')
def resources(name):
 result={}
 for label,path in [('nfs',Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')),('local',root)]:
  v=os.statvfs(path);result[label]=v.f_bavail*v.f_frsize
 save(name,result);assert result['nfs']>=100*1024**3 and result['local']>=2*1024**3
resources('resources-before.json')
for name,key in [('baseline.tar','baseline_sha256'),('overlay.tar','overlay_sha256')]:
 assert h((root/name).read_bytes())==manifest[key]
 with tarfile.open(root/name) as archive:
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
 *manifest['tests'],'--junitxml='+str(root/'regression.xml')]
started=time.perf_counter()
with (root/'stdout.log').open('w') as out,(root/'stderr.log').open('w') as err:
 result=subprocess.run(cmd,cwd=source,env=env,stdout=out,stderr=err,timeout=300)
save('result.json',{'returncode':result.returncode,'elapsed_seconds':time.perf_counter()-started,
 'python':sys.version,'maxrss_kib':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
 'command':cmd,'overlays':manifest['overlays']})
replay_code="""import gzip,json,hashlib,sys,time
from pathlib import Path
from qtrade.research.factor_evaluation import validate_report
p=Path(sys.argv[1]);raw=p.read_bytes();start=time.perf_counter()
r=validate_report(json.loads(gzip.decompress(raw)))
print(json.dumps({'evaluation_id':r['evaluation_id'],'report_sha256':hashlib.sha256(raw).hexdigest(),
'bytes_preserved':p.read_bytes()==raw,'manifest_sha256':r['report_manifest_sha256'],
'elapsed_seconds':time.perf_counter()-start}))"""
old=source/'docs/evidence/p0-publisher-factor-20261028/real-daily-evaluation/actual-891-stock/report.json.gz'
replay=subprocess.run([sys.executable,'-B','-c',replay_code,str(old)],cwd=source,env=env,capture_output=True,text=True,timeout=30)
save('old-report-replay.json',{'returncode':replay.returncode,'stdout':replay.stdout,'stderr':replay.stderr})
verify('source-after.json');resources('resources-after.json')
print((root/'result.json').read_text(),flush=True)
print((root/'stdout.log').read_text()[-3500:],flush=True)
print((root/'old-report-replay.json').read_text(),flush=True)
