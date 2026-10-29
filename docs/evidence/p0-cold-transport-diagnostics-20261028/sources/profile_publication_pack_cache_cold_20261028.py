"""Isolated read-only-source pack profile. NEVER install, import or push."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import shutil
import stat
import subprocess
import time
from datetime import datetime,timezone

ROOT=Path(__file__).parent
BASE=Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
OPROOT=Path('/root/qtrade-local/operator-evidence/publication-relay-cold-20261028T130000Z')
TARGET='dde33b30a54d46beb0197a80ddbcb35afb5830da'
ANCESTOR='5a0925f47e541726210a8879e2f2bed4ae2a62fd'
PLAN_SHA='f33c80814b18cc6e77150535b68b93e6525a1ccdadfaa0bd021707d4cc87416a'
LIMIT=64*1024*1024
BUDGET=120


def require(ok,code):
    if not ok:raise ValueError('COLD_PACK_'+code)


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name,value):
    require(ROOT.parent==Path('/root/qtrade-local/operator-evidence')
        and ROOT.name.startswith('publication-pack-cache-stage-')
        and not any(p.is_symlink() for p in (ROOT,*ROOT.parents)), 'FIXED_ISOLATION_PATH')
    raw=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
    with (ROOT/name).open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    fd=os.open(ROOT,os.O_DIRECTORY|os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def stream_pack(command,env,input_raw,output,deadline,cleanup):
    count=0;checksum=hashlib.sha256();started=time.monotonic()
    with output.open('xb') as target:
        with subprocess.Popen(command,env=env,start_new_session=True,stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL) as child:
            try:
                pending=memoryview(input_raw)
                os.set_blocking(child.stdin.fileno(),False)
                if not pending:child.stdin.close()
                while True:
                    remaining=deadline-time.monotonic();require(remaining>0,'STAGING_DEADLINE')
                    ready,writable,_=select.select([child.stdout],
                        [child.stdin] if pending else [],[],min(.2,remaining))
                    if writable:
                        sent=os.write(child.stdin.fileno(),pending[:65536]);pending=pending[sent:]
                        if not pending:child.stdin.close()
                    if not ready:continue
                    raw=os.read(child.stdout.fileno(),65536)
                    if not raw:break
                    require(count+len(raw)<=LIMIT,'PACK_BYTE_LIMIT')
                    target.write(raw);checksum.update(raw);count+=len(raw)
                child.wait(timeout=max(.001,deadline-time.monotonic()))
                require(child.returncode==0,'PACK_COMMAND_FAILED')
            finally:cleanup(child)
        target.flush();os.fsync(target.fileno())
    require(time.monotonic()<deadline,'STAGING_DEADLINE')
    return {'bytes':count,'sha256':checksum.hexdigest(),'elapsed_seconds':time.monotonic()-started}


def main():
    started=time.monotonic();deadline=started+BUDGET
    require(ROOT.parent==Path('/root/qtrade-local/operator-evidence')
        and ROOT.name.startswith('publication-pack-cache-stage-')
        and not any(p.is_symlink() for p in (ROOT,*ROOT.parents)),'FIXED_ISOLATION_PATH')
    require(sys_flags_safe(),'SAFE_PYTHON_RUNTIME')
    require(shutil.disk_usage(ROOT).free>=2*1024**3,'LOCAL_FREE_SPACE')
    source=OPROOT/'publication_native_relay_operator_v3_20261028.py'
    require(digest(source)=='76bf3e2653ab51491b966db9f62c415c0d7161b4d642e7f89cefc30b687a61fc','FROZEN_OPERATOR_SOURCE')
    require(digest(OPROOT/'target.json')==PLAN_SHA,'FROZEN_PLAN')
    spec=importlib.util.spec_from_file_location('readonly_frozen_relay',source)
    op=importlib.util.module_from_spec(spec);spec.loader.exec_module(op)
    op.guard_scope();op.runtime_origins()
    from scripts.publish_research_snapshot import _cleanup_git_group
    config_hash=digest(op.SOURCE/'.git/config')
    phases={}
    def git(args,repo=None,maximum=16_000_000):
        remaining=deadline-time.monotonic();require(remaining>0,'STAGING_DEADLINE')
        stamp=time.monotonic()
        result=op.git(repo or op.SOURCE/'.git',args,maximum=maximum,timeout=min(90,remaining))[0]
        phases[' '.join(args[:2])]=time.monotonic()-stamp
        return result
    git(['merge-base','--is-ancestor',ANCESTOR,TARGET])
    git(['merge-base','--is-ancestor',TARGET,'HEAD'])
    listing=git(['rev-list','--objects',TARGET,'^'+ANCESTOR],maximum=16_000_000)
    rows=listing.splitlines();require(0<len(rows)<=100_000,'OBJECT_COUNT_BOUND')
    identities=[row.split(b' ',1)[0] for row in rows]
    require(all(re.fullmatch(b'[a-f0-9]{40}',oid) for oid in identities)
        and len(set(identities))==len(identities),'OBJECT_IDENTITY_LIST')
    with (ROOT/'selected-object-list.txt').open('xb') as f:
        f.write(listing);f.flush();os.fsync(f.fileno())
    command=['/usr/bin/git','-c','core.hooksPath=/dev/null','-c','gc.auto=0',
        '--git-dir='+str(op.SOURCE/'.git'),'pack-objects','--stdout','--no-reuse-delta',
        '--no-reuse-object','--window=0','--depth=0']
    pack=ROOT/'candidate.pack'
    pack_profile=stream_pack(command,op.clean_env(),b'\n'.join(identities)+b'\n',pack,deadline,_cleanup_git_group)
    cache=ROOT/'cache.git'
    require(not os.path.lexists(cache) and not os.path.lexists(pack.with_suffix('.idx')),
        'NEW_CACHE_AND_INDEX_REQUIRED')
    cache.mkdir()
    require(cache.resolve()==cache and not cache.is_symlink(),'CACHE_DIRECTORY_ALIAS')
    git(['init','--bare',str(cache)],repo=cache)
    alternate=cache/'objects/info/alternates'
    require(not any(p.is_symlink() for p in (alternate,*alternate.parents)), 'CACHE_DIRECTORY_ALIAS')
    with alternate.open('xb') as f:
        f.write((str(op.SOURCE/'.git/objects')+'\n').encode());f.flush();os.fsync(f.fileno())
    raw=git(['index-pack','--strict',str(pack)],repo=cache,maximum=4096)
    require(re.fullmatch(b'[a-f0-9]{40}\n',raw) is not None,'INDEX_PACK_ID')
    verification=git(['verify-pack','-v',str(pack.with_suffix('.idx'))],repo=cache)
    object_rows=[]
    for row in verification.splitlines():
        fields=row.split()
        if fields and re.fullmatch(b'[a-f0-9]{40}',fields[0]):
            require(len(fields)==5 and fields[1] in {b'blob',b'tree',b'commit',b'tag'},'NONDELTA_PACK_REQUIRED')
            object_rows.append(fields)
    require(len(object_rows)==len(identities)
        and {r[0] for r in object_rows}==set(identities),'EXACT_PACK_OBJECT_CLOSURE')
    require(digest(op.SOURCE/'.git/config')==config_hash,'SOURCE_CONFIG_CHANGED')
    require(time.monotonic()<deadline,'STAGING_DEADLINE')
    profile={'schema_version':'qtrade.cold-pack-stage-profile.v1','state':'CANDIDATE_VALIDATED_NOT_INSTALLED',
        'captured_at':datetime.now(timezone.utc).isoformat(),'target_commit':TARGET,'base_commit':ANCESTOR,
        'failed_manual_plan_sha256':PLAN_SHA,'cold_staging_budget_seconds':BUDGET,
        'normal_git_budget_seconds':90,'normal_export_budget_seconds':45,
        'selected_object_count':len(identities),'selected_object_list_sha256':hashlib.sha256(listing).hexdigest(),
        'pack':pack_profile,'pack_index_sha256':digest(pack.with_suffix('.idx')),
        'pack_id':raw.strip().decode(),'phase_seconds':phases,'elapsed_seconds':time.monotonic()-started,
        'source_config_unchanged':True,'source_objects_or_refs_written':False,
        'production_cache_installed':False,'main_push_attempted':False,'manual_intent_replayed':False,
        'historical_public_graph_allowlist_audited':False,'network_fixed':False,
        'limitation':'READ_ONLY_SOURCE_DIAGNOSTIC_NO_CACHE_INSTALLATION_OR_DELIVERY_ACCEPTANCE'}
    save('profile.json',profile);print(json.dumps(profile,sort_keys=True))


def sys_flags_safe():
    import sys
    return sys.flags.safe_path and sys.dont_write_bytecode and not sys.flags.optimize and os.environ.get('LIVE_TRADING_ENABLED')=='false'


if __name__=='__main__':
    try:main()
    except Exception as error:
        value={'state':'FAILED_NOT_INSTALLED','error_class':type(error).__name__,
            'error_code':str(error) if re.fullmatch('COLD_PACK_[A-Z_]+',str(error)) else 'COLD_PACK_DIAGNOSTIC_FAILED',
            'source_objects_or_refs_written':False,'main_push_attempted':False}
        save('failure.json',value);print(json.dumps(value));raise SystemExit(1)
