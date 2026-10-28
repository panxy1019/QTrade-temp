from __future__ import annotations
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shlex
import subprocess
import tarfile
from datetime import datetime,timezone
import uuid

LOCAL=Path(__file__).resolve().parent
REPO=Path('E:/QTrade-git')
REV='f59cc8923bfc247009d2c92f391231bc92333571'
EXPECTED={
 'src/qtrade/research/factor_strategy_backtest.py':'8864f939e86ea2d031b93617a9621aba5f43ad54da5caeff801e0f16fbe492a8',
 'scripts/run_factor_strategy_backtest.py':'b6902a2a13f3e13dae1998d7fe0d68d3bc054d0a5943aedf03ee2ccdc2d1a907',
 'tests/test_factor_strategy_backtest.py':'e484eff0374a65b93acc4e0fe0e666218dacf7d660f2679fb0044615c0287d5f'}
BASE='/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade'


def sha(raw):return hashlib.sha256(raw).hexdigest()
def save(path,value):path.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n',encoding='utf-8',newline='\n')


def main():
    name='factor-backtest-native-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    folder=LOCAL/name;folder.mkdir()
    tar=folder/'source.tar'
    subprocess.run(['git','archive','--format=tar','--output='+str(tar),REV,
        'src','scripts','configs','tests'],cwd=REPO,check=True,timeout=60,capture_output=True)
    files={}
    with tarfile.open(tar,'r') as archive:
        for member in archive:
            assert member.isdir() or member.isfile()
            if member.isfile():files[member.name]=sha(archive.extractfile(member).read())
    for name,expected in EXPECTED.items():assert sha((REPO/name).read_bytes())==expected
    overlay=folder/'overlay.tar'
    with tarfile.open(overlay,'w') as archive:
        for name,expected in EXPECTED.items():
            value=(REPO/name).read_bytes();assert sha(value)==expected
            member=tarfile.TarInfo(name);member.size=len(value);member.mode=0o644
            archive.addfile(member,io.BytesIO(value));files[name]=expected
    manifest={'base_git_revision':REV,'selected_roots':['src','scripts','configs','tests'],
        'overlay_sha256':EXPECTED,'files':files,'source_tar_sha256':sha(tar.read_bytes()),
        'overlay_tar_sha256':sha(overlay.read_bytes()),'pure_git_claim':False}
    save(folder/'package-manifest.json',manifest)
    remote='/root/qtrade-local/operator-evidence/'+folder.name
    save(folder/'dispatch.json',{'remote_root':remote,'source_files':len(files),'live':False})
    spec=importlib.util.spec_from_file_location('authorized_factor_native_connect',
        'E:/QTrade-temp/authorized_cluster_read.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    client=module.connect()
    try:
        sftp=client.open_sftp();sftp.mkdir(remote);sftp.mkdir(remote+'/source');sftp.mkdir(remote+'/evidence')
        for path in (tar,overlay,folder/'package-manifest.json',LOCAL/'linux_native_driver_v3.py'):
            sftp.put(str(path),remote+'/'+path.name)
            with sftp.open(remote+'/'+path.name,'rb') as handle:assert sha(handle.read())==sha(path.read_bytes())
        stage="""import pathlib,tarfile,hashlib,json
r=pathlib.Path(%r)
m=json.loads((r/'package-manifest.json').read_bytes())
for name,key in [('source.tar','source_tar_sha256'),('overlay.tar','overlay_tar_sha256')]:
 assert hashlib.sha256((r/name).read_bytes()).hexdigest()==m[key]
 with tarfile.open(r/name) as t:
  for x in t.getmembers():
   p=(r/'source'/x.name).resolve()
   assert p.is_relative_to(r/'source') and (x.isdir() or x.isfile())
  t.extractall(r/'source',filter='data')
actual={p.relative_to(r/'source').as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (r/'source').rglob('*') if p.is_file()}
assert actual==m['files']
print(json.dumps({'source_files':len(actual),'stage':'EXACT_SELECTED_GIT_PLUS_THREE_OVERLAYS'}))
""" % remote
        cmd=shlex.join([BASE+'/qlib-env/bin/python3.11','-B','-P','-c',stage])
        _,out,err=client.exec_command(cmd,timeout=60)
        raw=out.read();error=err.read();code=out.channel.recv_exit_status()
        (folder/'stage.stdout').write_bytes(raw);(folder/'stage.stderr').write_bytes(error)
        assert code==0,{'stage_rc':code,'stderr_sha256':sha(error)}
        cmd=shlex.join(['env','-i','PATH=/usr/bin:/bin','LC_ALL=C.UTF-8',
            'LIVE_TRADING_ENABLED=false','PYTHONNOUSERSITE=1','PYTHONDONTWRITEBYTECODE=1',
            'PYTHONPATH='+remote+'/source/src:'+remote+'/source',
            BASE+'/qlib-env/bin/python3.11','-P','-B',remote+'/linux_native_driver_v3.py'])
        _,out,err=client.exec_command(cmd,timeout=300)
        raw=out.read();error=err.read();code=out.channel.recv_exit_status()
        (folder/'driver.stdout').write_bytes(raw);(folder/'driver.stderr').write_bytes(error)
        for name in sftp.listdir(remote+'/evidence'):
            (folder/'linux-actual').mkdir(exist_ok=True)
            sftp.get(remote+'/evidence/'+name,str(folder/'linux-actual'/name))
        save(folder/'driver-result.json',{'returncode':code,'remote_root':remote,
             'stdout_sha256':sha(raw),'stderr_sha256':sha(error),'source_files':len(files)})
        sftp.close()
        print(json.dumps({'evidence':str(folder),'remote_root':remote,'returncode':code,
            'source_files':len(files)}))
    finally:client.close()


if __name__=='__main__':main()
