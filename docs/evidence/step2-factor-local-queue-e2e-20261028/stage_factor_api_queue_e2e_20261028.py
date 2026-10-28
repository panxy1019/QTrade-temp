"""One isolated complete READY mirror and actual local-transport integration."""
import hashlib
import importlib.machinery
import json
import os
import resource
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
from pathlib import Path

owned=Path(sys.argv[1]).resolve()
assert owned.parent==Path('/root/qtrade-local/operator-evidence')
mount=Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111')
nfs=mount/('step2-factor-api-queue-'+owned.name.rsplit('-',1)[-1])
assert nfs.parent==mount and not nfs.exists()
source=owned/'source'
original_base=mount/'qtrade'
original_db=Path('/root/qtrade-local/qlib-versions.sqlite')
real_archive=Path('/root/qtrade-local/operator-evidence/factor-queue-s2d-final-20261028T124314Z-e0813807/approved-factor-archive')
original_old=Path('/root/qtrade-local/operator-evidence/step2-real-daily-20261028T045123Z')
original_longer=Path('/root/qtrade-local/operator-evidence/factor-longer-s2b-20261028T112943Z-86cd84da')
identity='09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407'
version=original_base/'unified-data/qlib-versions/versions'/identity
manifest=json.loads((owned/'source-manifest.json').read_bytes())
assert manifest['baseline_commit']=='45de5bd2455b9ed6bdaddc1646d69e148846e3da' and not manifest['overlays']
assert os.environ['LIVE_TRADING_ENABLED']=='false'
os.nice(10)
def h(p):
    value=hashlib.sha256()
    with p.open('rb') as handle:
        for part in iter(lambda:handle.read(1024*1024),b''):value.update(part)
    return value.hexdigest()
def save(name,value):(owned/name).write_text(json.dumps(value,indent=2)+'\n')
def inventory(root):
    return {p.relative_to(root).as_posix():{'sha256':h(p),'bytes':p.stat().st_size}
        for p in sorted(root.rglob('*')) if p.is_file()}
def reserve(name):
    free={'nfs':shutil.disk_usage(mount).free,'local':shutil.disk_usage(owned).free}
    save(name,free);assert free['nfs']>=100*1024**3 and free['local']>=2*1024**3
def selected_admission(database):
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=3) as c:
        row=c.execute('SELECT * FROM data_versions WHERE version_id=?',(identity,)).fetchall()
        event=c.execute("SELECT * FROM data_events WHERE version_id=? AND kind='QLIB_DATA_READY' ORDER BY sequence",(identity,)).fetchall()
    assert len(row)==len(event)==1
    return {'row':row,'event':event}
def control():
    with sqlite3.connect('file:///root/qtrade-local/online-models.sqlite?mode=ro',uri=True,timeout=3) as c:
        return {'active_model':c.execute('SELECT model_id FROM active_model WHERE singleton=1').fetchall()}
reserve('resources-before.json')
print('PHASE source_extract_verify',flush=True)
assert h(owned/'source.tar')==manifest['source_tar_sha256']
source.mkdir()
with tarfile.open(owned/'source.tar') as archive:
    for member in archive.getmembers():
        assert (member.isdir() or member.isfile()) and (source/member.name).resolve().is_relative_to(source)
    archive.extractall(source,filter='data')
assert inventory(source)==manifest['expected_files']
save('operator-source-before.json',inventory(source))
originals={'old':inventory(original_old),'longer':inventory(original_longer),'approved_factor_archive':inventory(real_archive)}
save('originals-before.json',originals)
admission=selected_admission(original_db);save('admission-before.json',admission)
save('controls-before.json',control())
assert h(original_old/'report.json.gz')=='47bed3add84aff16f901531115c17ac4534cfd667b6716595a1ce098b9af37dd'
assert h(original_longer/'report.json.gz')=='74c246d7822b4e860706dcc2c212763ede3aef62f38f5b94297237acf5457ada'
version_before=inventory(version);save('ready-original-before.json',version_before)
total=sum(item['bytes'] for item in version_before.values())
print('PHASE full_READY_mirror files='+str(len(version_before))+' bytes='+str(total),flush=True)
assert shutil.disk_usage(mount).free-total>=100*1024**3
nfs.mkdir()
mirror=nfs/'unified-data/qlib-versions/versions'/identity
mirror.parent.mkdir(parents=True)
for p in version.rglob('*'):assert not p.is_symlink()
shutil.copytree(version,mirror)
assert inventory(mirror)==version_before
print('PHASE READY_mirror_hash_verified',flush=True)
save('ready-mirror.json',{'files':version_before,'bytes':total,'file_count':len(version_before),'nfs_base':str(nfs),'matches_original':True})
(owned/'db').mkdir()
destination=owned/'db/qlib-versions.sqlite'
with sqlite3.connect(original_db.resolve().as_uri()+'?mode=ro',uri=True,timeout=3) as src:
    with sqlite3.connect(destination) as dst:src.backup(dst)
assert selected_admission(destination)==admission
archive=owned/'approved-factor-archive'
shutil.copytree(real_archive,archive)
assert inventory(archive)==originals['approved_factor_archive']
save('staging.json',{'scope':'COMPLETE_REAL_READY_BYTES_AND_CONSISTENT_RO_CATALOG_BACKUP',
    'version_id':identity,'archive_bytes_unchanged':True,'selected_admission_unchanged':True,
    'nfs_base':str(nfs),'local_databases':str(owned/'db'),'readonly_original_db':str(original_db)})
sys.path[:0]=[str(source/'src'),str(source)]
origin_names=['qtrade','qtrade.api.research_workflow','qtrade.research.factor_strategy_catalog',
 'qtrade.research.factor_strategy_contract','qtrade.research.workflow_queue',
 'scripts.run_qlib_worker','scripts.run_factor_strategy_registration']
origins={}
for name in origin_names:
    module=__import__(name,fromlist=['__file__'])
    path=Path(module.__file__).resolve()
    assert path.is_relative_to(source)
    relative=path.relative_to(source).as_posix()
    assert h(path)==manifest['expected_files'][relative]['sha256']
    origins[name]={'file':str(path),'sha256':h(path)}
save('origins.json',origins)
command=[sys.executable,'-P','-B',str(owned/'factor_api_queue_e2e_20261028.py'),
 '--mode','real-e2e','--source',str(source),'--owned',str(owned),
 '--revision',manifest['baseline_commit'],'--base',str(nfs),
 '--database',str(owned/'db/workflow.sqlite'),'--archive',str(archive),
 '--source-manifest',str(owned/'source-manifest.json')]
env={k:v for k,v in os.environ.items() if not k.startswith('QTRADE_RESEARCH_')}
env.update(PYTHONPATH=str(source/'src')+':'+str(source),PYTHONDONTWRITEBYTECODE='1',
 LIVE_TRADING_ENABLED='false',OMP_NUM_THREADS='2')
started=time.perf_counter()
print('PHASE actual_ASGI_local_Git_worker_started',flush=True)
with (owned/'integration-stdout.log').open('w') as out,(owned/'integration-stderr.log').open('w') as err:
    result=subprocess.run(command,cwd=owned,env=env,stdout=out,stderr=err,timeout=420)
save('operator-result.json',{'returncode':result.returncode,'elapsed_seconds':time.perf_counter()-started,
    'maxrss_kib':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,'command':command})
assert inventory(source)==manifest['expected_files'];save('operator-source-after.json',inventory(source))
assert inventory(version)==version_before;save('ready-original-after.json',inventory(version))
assert selected_admission(original_db)==admission;save('admission-after.json',selected_admission(original_db))
assert {'old':inventory(original_old),'longer':inventory(original_longer),'approved_factor_archive':inventory(real_archive)}==originals
save('originals-after.json',originals);save('controls-after.json',control())
reserve('resources-after.json')
print((owned/'operator-result.json').read_text(),flush=True)
print((owned/'integration-stdout.log').read_text()[-5000:],flush=True)
print((owned/'integration-stderr.log').read_text()[-8000:],flush=True)
assert result.returncode==0
