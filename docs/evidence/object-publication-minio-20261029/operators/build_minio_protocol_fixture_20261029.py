"""Build a pinned MinIO fixture in one isolated local volume; no production writes."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime, timezone
from urllib.request import urlopen
import importlib.util
import json
import os
import resource
import shutil
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).parent
BASE = Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
RELEASE = BASE / 'releases/fba51aade2ee2410dda087e95cf054e53375ed3a'
COMMIT = '07c3a429bfed433e49018cb0f78a52145d4bedeb'
GO_SHA = '1fc94b57134d51669c72173ad5d49fd62afb0f1db9bf3f798fd98ee423f8d730'
GO_BYTES = 78709986


def save(name, value):
    with (ROOT/name).open('x') as f:
        json.dump(value, f, sort_keys=True, indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())


def download(url, target, bound, expected=None):
    started = time.monotonic(); digest = sha256(); count = 0
    with urlopen(url, timeout=20) as response, target.open('xb') as out:
        assert response.status == 200
        while True:
            assert time.monotonic()-started < 180
            raw = response.read(131072)
            if not raw: break
            count += len(raw); assert count <= bound
            digest.update(raw); out.write(raw)
        out.flush(); os.fsync(out.fileno())
    assert expected is None or digest.hexdigest() == expected
    return {'bytes':count,'sha256':digest.hexdigest(),'seconds':time.monotonic()-started}


def main():
    assert ROOT.parent == Path('/root/qtrade-local/operator-evidence')
    assert ROOT.name == 'minio-protocol-20261029T163700Z'
    assert not any(p.is_symlink() for p in (ROOT,*ROOT.parents))
    assert shutil.disk_usage(ROOT).free > 2*1024**3
    assert shutil.disk_usage(BASE).free > 100*1024**3
    started=time.monotonic(); phases={}
    phases['go_download']=download('https://dl.google.com/go/go1.24.13.linux-amd64.tar.gz',
        ROOT/'go.tar.gz', GO_BYTES, GO_SHA)
    assert phases['go_download']['bytes'] == GO_BYTES
    with tarfile.open(ROOT/'go.tar.gz') as archive:archive.extractall(ROOT,filter='data')
    phases['source_download']=download('https://codeload.github.com/minio/minio/tar.gz/'+COMMIT,
        ROOT/'minio-source.tar.gz', 30_000_000)
    with tarfile.open(ROOT/'minio-source.tar.gz') as archive:archive.extractall(ROOT,filter='data')
    source=ROOT/('minio-'+COMMIT); assert (source/'go.mod').is_file()
    with tarfile.open(ROOT/'sdk.tar.gz') as archive:archive.extractall(ROOT,filter='data')
    assert not (ROOT/'minio').exists()
    cleanup_spec=importlib.util.spec_from_file_location('frozen_publish_cleanup',RELEASE/'scripts/publish_research_snapshot.py')
    cleanup=importlib.util.module_from_spec(cleanup_spec);cleanup_spec.loader.exec_module(cleanup)
    env={'PATH':str(ROOT/'go/bin')+':/usr/bin:/bin','GOCACHE':str(ROOT/'go-cache'),
         'GOMODCACHE':str(ROOT/'go-modules'),'GOPATH':str(ROOT/'go-work'),
         'GOPROXY':'https://proxy.golang.org','GOSUMDB':'sum.golang.org','GOTOOLCHAIN':'local',
         'GOTELEMETRY':'off','CGO_ENABLED':'0','GOMAXPROCS':'4','GOFLAGS':'-mod=readonly'}
    command=[str(ROOT/'go/bin/go'),'build','-p','4','-trimpath','-buildvcs=false','-o',str(ROOT/'minio'),'.']
    build_started=time.monotonic()
    with (ROOT/'build.log').open('xb') as log:
        child=subprocess.Popen(command,cwd=source,env=env,stdin=subprocess.DEVNULL,
                               stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try: rc=child.wait(timeout=600)
        finally: cleanup._cleanup_git_group(child)
    phases['build_seconds']=time.monotonic()-build_started
    assert rc==0, 'PINNED_MINIO_BUILD_FAILED'
    binary=ROOT/'minio'; assert binary.is_file()
    version=subprocess.run([str(binary),'--version'],capture_output=True,timeout=10,check=True)
    report={'schema_version':'qtrade.minio-fixture-build.v1','at':datetime.now(timezone.utc).isoformat(),
        'minio_source_commit':COMMIT,'go_sha256':GO_SHA,'source_go_mod_sha256':sha256((source/'go.mod').read_bytes()).hexdigest(),
        'source_go_sum_sha256':sha256((source/'go.sum').read_bytes()).hexdigest(),
        'binary_sha256':sha256(binary.read_bytes()).hexdigest(),'binary_bytes':binary.stat().st_size,
        'version':version.stdout.decode().strip(),'phases':phases,'elapsed_seconds':time.monotonic()-started,
        'max_child_rss_kib':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        'service_started':False,'production_changed':False,'cloud_account_used':False,
        'local_free_bytes':shutil.disk_usage(ROOT).free,'nfs_free_bytes':shutil.disk_usage(BASE).free}
    save('build-result.json',report);print(json.dumps(report,sort_keys=True),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        result={'state':'FIXTURE_BUILD_FAILED','error_class':type(error).__name__,
                'production_changed':False,'service_started':False,'at':datetime.now(timezone.utc).isoformat()}
        save('build-failure.json',result);print(json.dumps(result),flush=True);raise SystemExit(1) from None
