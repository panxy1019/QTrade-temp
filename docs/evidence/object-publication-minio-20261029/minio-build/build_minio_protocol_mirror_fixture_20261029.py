"""Owned pinned-source MinIO build using checksum-verified proxy transport only."""
from __future__ import annotations
import ast
from contextlib import suppress
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import tarfile
import time

ROOT=Path(__file__).resolve().parent
ORIGINAL=Path('/root/qtrade-local/operator-evidence/minio-protocol-20261029T163700Z')
BASE=Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
COMMIT='07c3a429bfed433e49018cb0f78a52145d4bedeb'
GO_SHA='1fc94b57134d51669c72173ad5d49fd62afb0f1db9bf3f798fd98ee423f8d730'
SOURCE_SHA='8819e3e7817e46b7b3798f8f200ead208562e571563c2e040352378031abe9f2'
HELPER=BASE/'releases/fba51aade2ee2410dda087e95cf054e53375ed3a/scripts/publish_research_snapshot.py'
HELPER_SHA='eed5960ee4aa35135cddbff04235e500c4c8209464d3b14745a5a52afc585918'


def hashed(p):return sha256(p.read_bytes()).hexdigest()
def save(name,value):
    descriptor=os.open(ROOT/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'w') as h:json.dump(value,h,sort_keys=True,indent=2);h.write('\n');h.flush();os.fsync(h.fileno())
def disk(path):
    item=os.statvfs(path);return item.f_frsize*item.f_bavail
def original_inventory():
    paths=[ORIGINAL/n for n in ('go.tar.gz','minio-source.tar.gz','build-failure.json','build.log')]
    paths +=[ORIGINAL/('minio-'+COMMIT)/n for n in ('go.mod','go.sum')]
    return {str(p.relative_to(ORIGINAL)):{'sha256':hashed(p),'bytes':p.stat().st_size} for p in paths}
def source_inventory(source):
    result={}
    for p in source.rglob('*'):
        assert not p.is_symlink()
        if p.is_file():result[p.relative_to(source).as_posix()]=hashed(p)
    return result

def extract(archive_path,expected):
    assert hashed(archive_path)==expected
    with tarfile.open(archive_path) as archive:
        members=archive.getmembers();assert len(members)<50_000 and sum(m.size for m in members)<800*1024**2
        for member in members:
            assert member.isdir() or member.isfile()
            assert (ROOT/member.name).resolve().is_relative_to(ROOT)
        archive.extractall(ROOT,filter='data')

def main():
    start=time.monotonic();phases={};failure=None
    assert ROOT.parent==ORIGINAL and ROOT.name.startswith('build-mirror-')
    assert all(not p.is_symlink() for p in (ROOT,*ROOT.parents))
    assert os.environ.get('LIVE_TRADING_ENABLED')=='false'
    assert disk(ROOT)>=2*1024**3 and disk(BASE)>=100*1024**3
    before=original_inventory();save('original-before.json',before)
    save('intent.json',{'commit':COMMIT,'go_archive_sha256':GO_SHA,'source_archive_sha256':SOURCE_SHA,
        'goproxy':'https://goproxy.cn','gosumdb':'sum.golang.org','goflags':'-mod=readonly',
        'direct_fallback':False,'build_budget_seconds':600,'cleanup_term_seconds':2,'cleanup_kill_seconds':2,
        'proxy_env_presence':{k:bool(os.environ.get(k)) for k in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy')},
        'nfs_free_bytes':disk(BASE),'local_free_bytes':disk(ROOT),'service_started':False,'production_changed':False})
    raw=HELPER.read_bytes();assert sha256(raw).hexdigest()==HELPER_SHA
    node=next(n for n in ast.parse(raw).body if isinstance(n,ast.FunctionDef) and n.name=='_cleanup_git_group')
    namespace={'os':os,'signal':signal,'time':time,'subprocess':subprocess,'suppress':suppress}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(HELPER),'exec'),namespace)
    cleanup=namespace['_cleanup_git_group']
    extract(ORIGINAL/'go.tar.gz',GO_SHA);extract(ORIGINAL/'minio-source.tar.gz',SOURCE_SHA)
    source=ROOT/('minio-'+COMMIT);source_before=source_inventory(source);save('source-before.json',source_before)
    for name in ('go-cache','go-modules','go-work','home','tmp'):(ROOT/name).mkdir()
    env={'PATH':str(ROOT/'go/bin')+':/usr/bin:/bin','HOME':str(ROOT/'home'),'TMPDIR':str(ROOT/'tmp'),
        'LC_ALL':'C.UTF-8','GOCACHE':str(ROOT/'go-cache'),'GOMODCACHE':str(ROOT/'go-modules'),
        'GOPATH':str(ROOT/'go-work'),'GOPROXY':'https://goproxy.cn','GOSUMDB':'sum.golang.org',
        'GOTOOLCHAIN':'local','GOTELEMETRY':'off','CGO_ENABLED':'0','GOMAXPROCS':'4','GOFLAGS':'-mod=readonly'}
    # Values remain in memory only; no proxy value is saved or printed.
    for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy'):
        if os.environ.get(name):env[name]=os.environ[name]
    def owned(name,args,budget):
        log_path=ROOT/(name+'.private.log');descriptor=os.open(log_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'wb') as log:
            child=subprocess.Popen(args,cwd=source,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            fields=Path(f'/proc/{child.pid}/stat').read_text().rsplit(')',1)[1].split()
            save(name+'-owned-child.json',{'pid':child.pid,'pgid':os.getpgid(child.pid),'start_ticks':fields[19],
                'argv':args,'budget_seconds':budget,'cleanup_source_sha256':HELPER_SHA})
            tick=time.monotonic()
            try:returncode=child.wait(timeout=budget)
            finally:cleanup(child)
        phases[name]={'returncode':returncode,'seconds':time.monotonic()-tick,'private_log_sha256':hashed(log_path),'private_log_bytes':log_path.stat().st_size}
        assert returncode==0,'OWNED_COMMAND_FAILED'
    go=str(ROOT/'go/bin/go')
    try:
        owned('go-version',[go,'version'],10)
        assert 'go1.24.13' in (ROOT/'go-version.private.log').read_text()
        owned('build',[go,'build','-p','4','-trimpath','-buildvcs=false','-o',str(ROOT/'minio'),'.'],600)
        owned('module-verify',[go,'mod','verify'],60)
        assert (ROOT/'module-verify.private.log').read_text().strip()=='all modules verified'
        owned('binary-version',[str(ROOT/'minio'),'--version'],10)
        owned('binary-build-info',[go,'version','-m',str(ROOT/'minio')],10)
    except BaseException as error:failure={'error_class':type(error).__name__}
    source_after=source_inventory(source);save('source-after.json',source_after)
    after=original_inventory();save('original-after.json',after)
    assert before==after and source_before==source_after
    result={'schema_version':'qtrade.minio-checksummed-mirror-build.v1','state':'BUILT_VERIFIED_NOT_STARTED' if failure is None else 'BUILD_FAILED_NOT_STARTED',
        'at':datetime.now(timezone.utc).isoformat(),'minio_source_commit':COMMIT,'go_archive_sha256':GO_SHA,
        'source_archive_sha256':SOURCE_SHA,'go_mod_sha256':source_before['go.mod'],'go_sum_sha256':source_before['go.sum'],
        'source_file_count':len(source_before),'source_before_after_equal':source_before==source_after,
        'original_before_after_equal':before==after,'goproxy':'https://goproxy.cn','gosumdb':'sum.golang.org',
        'checksum_verification_disabled':False,'module_verify_passed':failure is None,
        'module_zip_count':len(list((ROOT/'go-modules').rglob('*.zip'))),'phases':phases,'failure':failure,
        'elapsed_seconds':time.monotonic()-start,'max_child_rss_kib':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        'service_started':False,'production_changed':False,'cloud_account_used':False,
        'local_free_bytes':disk(ROOT),'nfs_free_bytes':disk(BASE)}
    if failure is None:
        result.update({'binary_sha256':hashed(ROOT/'minio'),'binary_bytes':(ROOT/'minio').stat().st_size,
            'binary_version':(ROOT/'binary-version.private.log').read_text().strip()})
    save('build-result.json',result);print(json.dumps(result,sort_keys=True),flush=True)
    if failure is not None:raise SystemExit(1)


if __name__=='__main__':main()
