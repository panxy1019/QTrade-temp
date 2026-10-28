import hashlib
import importlib.util
import io
import json
import shlex
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

repo = Path('E:/QTrade-git')
run = 'factor-queue-s2d-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8]
work = Path('E:/QTrade-temp') / run
work.mkdir()
remote = '/root/qtrade-local/operator-evidence/' + run
commit = 'a89d45f96f0b89811ad07464e9ebb7ee3a9bbf12'
paths = ['src/qtrade/research/factor_strategy_contract.py',
 'src/qtrade/research/factor_strategy_catalog.py','scripts/factor_research_contracts.py',
 'scripts/run_factor_strategy_registration.py','scripts/run_qlib_worker.py',
 'tests/test_factor_research_worker.py','tests/fixtures/factor_strategy_register_v1.json',
 'src/qtrade/api/research_workflow.py','scripts/publish_research_snapshot.py',
 'tests/test_factor_research_api.py']
tests = ['tests/test_factor_strategy_contract.py', 'tests/test_factor_registry.py',
    'tests/test_daily_factor_evaluation.py', 'tests/test_step2_factor_contract_independent.py',
    'tests/test_step2_factor_contract_readiness.py', 'tests/test_qlib_strategy_contract.py',
    'tests/test_step2_strategy_readiness.py', 'tests/test_qlib_bound_research_integration.py',
    'tests/test_qlib_worker.py', 'tests/test_qlib_workflow.py',
    'tests/test_strategy_registry.py', 'tests/test_strategy_comparison.py',
 'tests/test_factor_research_worker.py','tests/test_factor_research_api.py']

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

subprocess.run(['git', 'archive', commit, '-o', str(work / 'baseline.tar')], cwd=repo, check=True)
with tarfile.open(work / 'baseline.tar') as archive:
    baseline = {entry.name: {'sha256': digest(archive.extractfile(entry).read()),
                            'bytes': entry.size} for entry in archive.getmembers() if entry.isfile()}
overlays = {}
with tarfile.open(work / 'overlay.tar', 'w') as archive:
    for name in paths:
        raw = (repo / name).read_bytes()
        overlays[name] = {'sha256': digest(raw), 'bytes': len(raw)}
        entry = tarfile.TarInfo(name)
        entry.size, entry.mode = len(raw), 0o644
        archive.addfile(entry, io.BytesIO(raw))
manifest = {'baseline_commit': commit, 'baseline_files': baseline,
    'expected_files': {**baseline, **overlays}, 'overlays': overlays, 'tests': tests,
    'baseline_sha256': digest((work / 'baseline.tar').read_bytes()),
    'overlay_sha256': digest((work / 'overlay.tar').read_bytes())}
(work / 'package-manifest.json').write_text(json.dumps(manifest, indent=2))
operator = r'''
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
'''
(work / 'run_factor_strategy_review.py').write_text(operator)
print('PACKAGE_ROOT',work,flush=True)
print('OVERLAYS',json.dumps(overlays),flush=True)
spec = importlib.util.spec_from_file_location('authorized_s2c', 'E:/QTrade-temp/authorized_cluster_read.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
client = module.connect()
try:
    sftp = client.open_sftp()
    try:
        sftp.mkdir(remote)
        for name in ('baseline.tar', 'overlay.tar', 'package-manifest.json', 'run_factor_strategy_review.py'):
            sftp.put(str(work / name), remote + '/' + name)
        command = '/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python -P -B ' + shlex.quote(remote + '/run_factor_strategy_review.py') + ' ' + shlex.quote(remote)
        _, out, err = client.exec_command(command, timeout=420)
        stdout, stderr = out.read().decode(), err.read().decode()
        status = out.channel.recv_exit_status()
        (work / 'remote-stdout.log').write_text(stdout)
        (work / 'remote-stderr.log').write_text(stderr)
        print('LOCAL_ROOT', work, flush=True)
        print('OPERATOR_STATUS', status, flush=True)
        print(stdout, flush=True)
        if stderr:
            print(stderr, flush=True)
        for name in ('resources-before.json', 'resources-after.json', 'source-before.json',
                     'source-after.json', 'result.json', 'regression.xml', 'stdout.log',
                     'stderr.log', 'old-report-replay.json'):
            try:
                sftp.get(remote + '/' + name, str(work / name))
            except FileNotFoundError:
                pass
        files = [{'file': p.name, 'bytes': p.stat().st_size, 'sha256': digest(p.read_bytes())}
                 for p in sorted(work.iterdir()) if p.is_file()]
        (work / 'evidence-manifest.json').write_text(json.dumps({'remote_root': remote,
            'operator_status': status, 'files': files}, indent=2))
    finally:
        sftp.close()
finally:
    client.close()
