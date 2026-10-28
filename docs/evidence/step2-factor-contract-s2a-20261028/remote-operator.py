
from pathlib import Path
from datetime import datetime, UTC
import gzip, hashlib, json, os, resource, subprocess, sys, tarfile, time
import xml.etree.ElementTree as ET

root = Path(sys.argv[1])
assert root.parent == Path('/root/qtrade-local/operator-evidence')
manifest = json.loads((root/'package-manifest.json').read_bytes())
source, evidence = root/'source', root/'evidence'
source.mkdir(); evidence.mkdir(); (root/'tmp').mkdir()
def digest(data): return hashlib.sha256(data).hexdigest()
def save(name, value): (evidence/name).write_text(json.dumps(value, indent=2))
def reserves():
    result = {}
    for name, path in [('nfs', Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')),
                       ('local', root)]:
        stat = os.statvfs(path)
        result[name] = {'path':str(path), 'free_bytes':stat.f_bavail*stat.f_frsize,
                        'device':path.stat().st_dev}
    result['passes'] = result['nfs']['free_bytes'] >= 100*1024**3 and result['local']['free_bytes'] >= 2*1024**3
    return result
preflight=reserves(); save('preflight.json', preflight)
assert preflight['passes'], 'RESERVE_REFUSED'
assert preflight['local']['device'] != preflight['nfs']['device'], 'LOCAL_NOT_DISTINCT_FROM_NFS'
assert digest((root/'baseline.tar').read_bytes()) == manifest['baseline_archive_sha256']
assert digest((root/'candidate-overlay.tar').read_bytes()) == manifest['overlay_archive_sha256']
def extract(path, target):
    with tarfile.open(path) as archive:
        for member in archive.getmembers():
            assert (member.isdir() or member.isfile()) and (target/member.name).resolve().is_relative_to(target.resolve())
        archive.extractall(target, filter='data')
extract(root/'baseline.tar', source)
extract(root/'candidate-overlay.tar', source)
def verify_source(name):
    actual = {p.relative_to(source).as_posix(): {'sha256':digest(p.read_bytes()),'bytes':p.stat().st_size}
              for p in source.rglob('*') if p.is_file()}
    save(name, {'expected_files':len(manifest['expected_files']), 'actual_files':len(actual),
                'all_bytes_match':actual == manifest['expected_files'], 'files':actual})
    assert actual == manifest['expected_files'], 'SOURCE_BYTES_DRIFT'
verify_source('source-before.json')
extract(root/'prior-windows-evidence.tar', root/'prior-windows-evidence')
env={**os.environ,'LIVE_TRADING_ENABLED':'false','PYTHONDONTWRITEBYTECODE':'1',
     'PYTHONPATH':str(source/'src')+':'+str(source),'TMPDIR':str(root/'tmp')}
command=[sys.executable,'-B','-m','pytest','-p','no:cacheprovider',
         '--basetemp='+str(root/'tmp/pytest'), *manifest['test_files'],
         '--junitxml='+str(evidence/'linux-junit.xml')]
started=datetime.now(UTC).isoformat(); clock=time.perf_counter()
with (evidence/'linux-stdout.log').open('w') as out, (evidence/'linux-stderr.log').open('w') as err:
    result=subprocess.run(command,cwd=source,env=env,stdout=out,stderr=err,timeout=180)
runtime={'started_at':started,'finished_at':datetime.now(UTC).isoformat(),
         'elapsed_seconds':time.perf_counter()-clock,'returncode':result.returncode,
         'python':sys.version,'command':command,'live_trading_enabled':env['LIVE_TRADING_ENABLED'],
         'pythonpath':env['PYTHONPATH'],'tmpdir':env['TMPDIR'],
         'max_child_rss_kib':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss}
if (evidence/'linux-junit.xml').exists():
    suites=ET.parse(evidence/'linux-junit.xml').getroot().findall('testsuite')
    runtime['tests']={key:sum(int(s.attrib.get(key,0)) for s in suites)
                     for key in ('tests','failures','errors','skipped')}
save('linux-runtime.json',runtime)
print('LINUX_TESTS',json.dumps(runtime),flush=True)
report_path=source/'docs/evidence/p0-publisher-factor-20261028/real-daily-evaluation/actual-891-stock/report.json.gz'
before=digest(report_path.read_bytes())
assert before=='47bed3add84aff16f901531115c17ac4534cfd667b6716595a1ce098b9af37dd'
replay_code="""import gzip,hashlib,json,sys,time
from pathlib import Path
from qtrade.research.factor_evaluation import validate_report
p=Path(sys.argv[1]);start=time.perf_counter();report=json.loads(gzip.decompress(p.read_bytes()))
validate_report(report)
print(json.dumps({'result':'ORIGINAL_891_REPORT_REPLAY_PASSED','evaluation_id':report['evaluation_id'],
'report_manifest_sha256':report['report_manifest_sha256'],'elapsed_seconds':time.perf_counter()-start,
'reader_source_sha256':hashlib.sha256(Path('src/qtrade/research/factor_evaluation.py').read_bytes()).hexdigest()}))
"""
replay=subprocess.run([sys.executable,'-B','-c',replay_code,str(report_path)],cwd=source,env=env,
                      capture_output=True,text=True,timeout=90)
(evidence/'old-report-stdout.log').write_text(replay.stdout)
(evidence/'old-report-stderr.log').write_text(replay.stderr)
receipt={'returncode':replay.returncode,'compressed_sha256_before':before,
         'compressed_sha256_after':digest(report_path.read_bytes())}
if replay.returncode==0: receipt.update(json.loads(replay.stdout))
save('old-report-replay.json',receipt)
verify_source('source-after.json')
postflight=reserves();save('postflight.json',postflight)
assert postflight['passes'], 'POST_RESERVE_FAILED'
save('final-status.json',{'linux_returncode':result.returncode,'replay_returncode':replay.returncode,
    'source_verified_before_and_after':True,'original_report_unchanged':receipt['compressed_sha256_after']==before,
    'runtime_source_sha256':manifest['overlay_files']['src/qtrade/research/factor_evaluation.py']['sha256'],
    'result':'PASSED' if result.returncode==replay.returncode==0 and runtime['tests']=={'tests':120,'failures':0,'errors':0,'skipped':0} else 'FAILED',
    'base_commit':manifest['base_commit'],'overlay_files':manifest['overlay_files']})
print('COMPLETE', (evidence/'final-status.json').read_text(),flush=True)
sys.exit(0 if result.returncode==replay.returncode==0 else 1)
