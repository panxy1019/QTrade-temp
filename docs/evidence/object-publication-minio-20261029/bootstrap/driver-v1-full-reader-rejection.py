"""Freeze exact candidate sources, then run isolated Linux/MinIO evidence."""
from pathlib import Path
from hashlib import sha256,sha1
from datetime import datetime,timezone
import argparse,importlib.util,io,json,shlex,subprocess,tarfile,time

REPO=Path('E:/QTrade-git');LOCAL=Path('E:/QTrade-temp/minio-bootstrap-20261029')
ROOT='/root/qtrade-local/operator-evidence/minio-protocol-20261029T163700Z'
BASE='/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade'
REV='f59cc8923bfc247009d2c92f391231bc92333571'
OVERLAYS=['src/qtrade/research/object_publication.py','src/qtrade/research/r2_object_store.py',
          'tests/test_object_publication.py','tests/unit/test_r2_object_store.py','pyproject.toml']


def connect():
    s=importlib.util.spec_from_file_location('authorized_cluster_read','E:/QTrade-temp/authorized_cluster_read.py')
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m.connect()


def save(path,value):
    with path.open('x',encoding='utf-8',newline='\n') as f:json.dump(value,f,indent=2,sort_keys=True);f.write('\n')


def stage():
    archive=subprocess.check_output(['git','archive',REV,'src','scripts','configs','tests','pyproject.toml'],cwd=REPO)
    records={};files={}
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for member in source:
            assert member.isdir() or member.isfile()
            if member.isfile():
                raw=source.extractfile(member).read();files[member.name]=raw
                records[member.name]={'sha256':sha256(raw).hexdigest(),'bytes':len(raw),
                    'git_blob_sha1':sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),'overlay':False}
    for name in OVERLAYS:
        raw=(REPO/name).read_bytes();base=records.get(name)
        files[name]=raw;records[name]={'sha256':sha256(raw).hexdigest(),'bytes':len(raw),
            'git_blob_sha1':None,'base_blob_sha1':None if base is None else base['git_blob_sha1'],'overlay':True}
    manifest={'schema_version':'qtrade.minio-candidate-source.v1','base_git_revision':REV,
              'not_a_pure_git_tree':True,'declared_overlays':OVERLAYS,'files':records}
    payload=io.BytesIO()
    with tarfile.open(fileobj=payload,mode='w:gz') as target:
        for name,raw in files.items():
            info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o600;target.addfile(info,io.BytesIO(raw))
    raw=payload.getvalue();LOCAL.mkdir(exist_ok=True)
    (LOCAL/'candidate.tar.gz').write_bytes(raw);save(LOCAL/'candidate-source-manifest.json',manifest)
    sources={'candidate.tar.gz':raw,'candidate-source-manifest.json':(LOCAL/'candidate-source-manifest.json').read_bytes(),
             'minio_s3_fixture_check_20261029.py':Path('E:/QTrade-temp/minio_s3_fixture_check_20261029.py').read_bytes()}
    c=connect()
    try:
        s=c.open_sftp()
        for name,body in sources.items():
            with s.open(ROOT+'/'+name,'wx') as f:f.write(body)
        s.close()
        program=r'''from pathlib import Path
import hashlib,json,tarfile
r=Path(ROOT_VALUE);m=json.loads((r/'candidate-source-manifest.json').read_bytes())
assert hashlib.sha256((r/'candidate.tar.gz').read_bytes()).hexdigest()==TAR_SHA
target=r/'candidate';target.mkdir(mode=0o700)
with tarfile.open(r/'candidate.tar.gz') as t:t.extractall(target,filter='data')
actual={p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file()}
assert actual==set(m['files'])
for name,entry in m['files'].items():
 p=target/name;raw=p.read_bytes();assert not p.is_symlink()
 assert len(raw)==entry['bytes'] and hashlib.sha256(raw).hexdigest()==entry['sha256']
 if not entry['overlay']:assert hashlib.sha1(b'blob '+str(len(raw)).encode()+bytes([0])+raw).hexdigest()==entry['git_blob_sha1']
print(json.dumps({'state':'EXACT_CANDIDATE_VERIFIED','files':len(actual),'base_git':m['base_git_revision'],'overlay_count':len(m['declared_overlays'])}))
'''.replace('ROOT_VALUE',repr(ROOT)).replace('TAR_SHA',repr(sha256(raw).hexdigest()))
        _,o,e=c.exec_command(shlex.join([BASE+'/venv/bin/python','-B','-P','-']),timeout=60)
        o.channel.sendall(program.encode());o.channel.shutdown_write();out=o.read();err=e.read();rc=o.channel.recv_exit_status()
        (LOCAL/'stage.stdout').write_bytes(out);(LOCAL/'stage.stderr').write_bytes(err)
        save(LOCAL/'stage-result.json',{'rc':rc,'upload_sha256':{n:sha256(b).hexdigest() for n,b in sources.items()},'files':len(records)})
        print(out.decode());assert rc==0
    finally:c.close()


def execute(mode):
    c=connect();stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');dest=LOCAL/(mode+'-'+stamp);dest.mkdir()
    program=r'''from pathlib import Path
from hashlib import sha256
import importlib.util,json,os,shutil,subprocess,time,sys
r=Path(ROOT_VALUE);base=Path(BASE_VALUE);candidate=r/'candidate'
release=base/'releases/fba51aade2ee2410dda087e95cf054e53375ed3a'
sys.path[:0]=[str(release/'src'),str(release)]
m=json.loads((r/'candidate-source-manifest.json').read_bytes())
expected={'src/qtrade/research/object_publication.py':'d11c801a90142d4b7142b8f67c422c341b30b84201999dd297c186c3582fa7d0','src/qtrade/research/r2_object_store.py':'d26685bf8f95db48ef3db8d0c8d4716aaac56d68963fd2c302fa9b5c62c16202','tests/test_object_publication.py':'f55e4a4bec2f01c193a82904be67ea7468bc615218ac68597f2c336e49a36806','tests/unit/test_r2_object_store.py':'16c2034f0782258d1058906c11adaa8d2175e39458650a573f2903c3f71bb1d0'}
assert all(m['files'][n]['sha256']==v for n,v in expected.items())
assert sha256((r/'minio_s3_fixture_check_20261029.py').read_bytes()).hexdigest()=='ffd6691b3486b01a0630adee64d84ae673c26c5a1078ab616382303b2f1bc601'
for n,x in m['files'].items():assert sha256((candidate/n).read_bytes()).hexdigest()==x['sha256']
assert shutil.disk_usage(r).free>2*1024**3 and shutil.disk_usage(base).free>100*1024**3
s=importlib.util.spec_from_file_location('owned_cleanup',base/'releases/fba51aade2ee2410dda087e95cf054e53375ed3a/scripts/publish_research_snapshot.py')
cleanup=importlib.util.module_from_spec(s);s.loader.exec_module(cleanup)
env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1','PYTHONSAFEPATH':'1',
     'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1','LIVE_TRADING_ENABLED':'false',
     'PYTHONPATH':str(candidate/'src')+':'+str(candidate)+':'+str(r/'sdk')}
mode=MODE_VALUE;stamp=STAMP_VALUE
if mode=='linux-tests':
 command=[str(base/'venv/bin/python'),'-B','-P','-m','pytest','-q','-p','no:cacheprovider',
          'tests/test_object_publication.py','tests/unit/test_r2_object_store.py','--junitxml='+str(r/('linux-tests-'+stamp+'.xml'))]
 budget=180
else:
 command=[str(base/'venv/bin/python'),'-B','-P',str(r/'minio_s3_fixture_check_20261029.py')];budget=700
started=time.monotonic()
with (r/(mode+'-'+stamp+'.stdout')).open('xb') as out,(r/(mode+'-'+stamp+'.stderr')).open('xb') as err:
 child=subprocess.Popen(command,cwd=candidate,env=env,stdin=subprocess.DEVNULL,stdout=out,stderr=err,start_new_session=True)
 try:rc=child.wait(timeout=budget)
 except subprocess.TimeoutExpired:rc=None
 finally:cleanup._cleanup_git_group(child)
after=all(sha256((candidate/n).read_bytes()).hexdigest()==x['sha256'] for n,x in m['files'].items())
result={'mode':mode,'returncode':rc,'seconds':time.monotonic()-started,'source_unchanged':after,'at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()}
raw=(json.dumps(result,sort_keys=True,indent=2)+'\n').encode()
with (r/(mode+'-'+stamp+'.json')).open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
print(json.dumps(result),flush=True)
'''.replace('ROOT_VALUE',repr(ROOT)).replace('BASE_VALUE',repr(BASE)).replace('MODE_VALUE',repr(mode)).replace('STAMP_VALUE',repr(stamp))
    try:
        _,o,e=c.exec_command(shlex.join([BASE+'/venv/bin/python','-B','-P','-']),timeout=760)
        o.channel.sendall(program.encode());o.channel.shutdown_write();out=o.read();err=e.read();rc=o.channel.recv_exit_status()
        (dest/'driver.stdout').write_bytes(out);(dest/'driver.stderr').write_bytes(err);print(out.decode());print('driver_rc',rc,flush=True)
        s=c.open_sftp()
        for name in s.listdir(ROOT):
            if name.startswith(mode+'-'+stamp):s.get(ROOT+'/'+name,str(dest/name))
        if mode=='integration':
            for run in s.listdir(ROOT):
                if run.startswith('run-'):
                    d=dest/run;d.mkdir()
                    for name in s.listdir(ROOT+'/'+run):
                        if name.endswith('.json'):s.get(ROOT+'/'+run+'/'+name,str(d/name))
        s.close()
    finally:c.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['stage','linux-tests','integration']);a=p.parse_args()
    stage() if a.mode=='stage' else execute(a.mode)
