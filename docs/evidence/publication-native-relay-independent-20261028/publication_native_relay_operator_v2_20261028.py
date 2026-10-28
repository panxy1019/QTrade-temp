"""Frozen public Git delivery through a temporary reviewed transport only."""
import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import importlib.machinery
import math
import stat
from datetime import datetime, timezone
from pathlib import Path

BASE = Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
SOURCE = BASE/'publication'
RELEASE = BASE/'releases/fba51aade2ee2410dda087e95cf054e53375ed3a'
ROOT = Path(__file__).parent
REPO = ROOT/'delivery.git'
APPROVED_ATTEMPT = Path('/root/qtrade-local/operator-evidence/publication-relay-cold-20261028T130000Z')
REMOTE_SHA = 'ca4d67077d9c3bc08feb8d7e05aa8076e5d1c3114d1b4bbcf906e390ed8e8f2b'
SSH_SHA = '91e5fd67a08092e3ff795305eb4a0618a5755bf5a80a76f6c43f6a892c06125d'
PUBLISH_SHA = 'eed5960ee4aa35135cddbff04235e500c4c8209464d3b14745a5a52afc585918'
PROXY_SHA = 'f4146011c52405de87570e95e9b2cf084bb2896072453e01fe175d2c9a11aac2'

def require(value, code):
    if not value: raise ValueError('NATIVE_RELAY_'+code)

def file_sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def save(name, value):
    raw = (json.dumps(value, sort_keys=True, indent=2)+'\n').encode()
    with (ROOT/name).open('xb') as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    descriptor = os.open(ROOT, os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)

def clean_env():
    # No inherited Git override, trace, replace, arbitrary configuration or hook.
    return {'PATH':'/usr/bin:/bin','LC_ALL':'C','GIT_CONFIG_NOSYSTEM':'1',
        'GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0',
        'GIT_NO_REPLACE_OBJECTS':'1','PYTHONDONTWRITEBYTECODE':'1',
        'PYTHONNOUSERSITE':'1','LIVE_TRADING_ENABLED':'false',
        'PYTHONSAFEPATH':'1',
        'PYTHONPATH':str(RELEASE/'src')+':'+str(RELEASE)}

def runtime_origins():
    require(sys.flags.safe_path,'SAFE_IMPORT_PATH_REQUIRED')
    for name,relative in {'scripts.publish_research_snapshot':'scripts/publish_research_snapshot.py',
            'qtrade.research.unified_market':'src/qtrade/research/unified_market.py'}.items():
        paths=None;prefix=''
        for part in name.split('.'):
            prefix=(prefix+'.' if prefix else '')+part
            spec=importlib.machinery.PathFinder.find_spec(prefix,paths)
            require(spec is not None,'RUNTIME_IMPORT_MISSING')
            paths=spec.submodule_search_locations
        require(Path(spec.origin).resolve()==RELEASE/relative,'RUNTIME_IMPORT_SHADOW')

def git(repo, arguments, *, env=None, maximum=16_000_000, timeout=90, input_data=None):
    if arguments and arguments[0] in {'ls-remote','push'}:
        require(repo==REPO,'NETWORK_REPOSITORY_REQUIRED')
        guard_repository(network=True)
    runtime_origins()
    from scripts.publish_research_snapshot import _cleanup_git_group
    command = ['/usr/bin/git','-c','pack.useSparse=false','-c','gc.auto=0',
        '-c','core.hooksPath=/dev/null','--git-dir='+str(repo),*arguments]
    started=time.monotonic()
    with subprocess.Popen(command,env=env or clean_env(),start_new_session=True,
        stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL) as child:
        try:
            raw,_=child.communicate(input=input_data,timeout=timeout)
        finally:
            _cleanup_git_group(child)
    require(child.returncode==0,'GIT_COMMAND_REJECTED')
    require(len(raw)<=maximum,'GIT_OUTPUT_BOUND')
    return raw,time.monotonic()-started

def source(command, **kwargs):return git(SOURCE/'.git',command,**kwargs)[0]

def guard_scope():
    require(ROOT==APPROVED_ATTEMPT and REPO==ROOT/'delivery.git'
        and SOURCE==BASE/'publication'
        and not any(p.is_symlink() for path in (ROOT,SOURCE,SOURCE/'.git',SOURCE/'.git/objects')
                    for p in (path,*path.parents))
        and all(path.is_dir() and path.resolve()==path for path in
                (ROOT,SOURCE,SOURCE/'.git',SOURCE/'.git/objects')),
        'FIXED_PATHS_REQUIRED')
    require(not os.path.lexists(SOURCE/'.git/commondir'),'SOURCE_COMMON_DIRECTORY_FORBIDDEN')


def guard_repository(*, network=False):
    guard_scope()
    paths=(REPO,REPO/'objects',REPO/'objects/info',REPO/'config',REPO/'objects/info/alternates')
    require(all(path.exists() and path.resolve()==path
                and not any(p.is_symlink() for p in (path,*path.parents)) for path in paths)
        and REPO.is_dir() and REPO!=SOURCE/'.git'
        and (REPO/'objects').is_dir() and (REPO/'objects/info').is_dir()
        and all(stat.S_ISREG(path.stat().st_mode) and path.stat().st_nlink==1
                for path in (REPO/'config',REPO/'objects/info/alternates')),
        'ISOLATED_REPOSITORY_ALIAS_OR_PATH')
    require(not os.path.lexists(REPO/'commondir')
        and not os.path.lexists(REPO/'objects/info/http-alternates')
        and not os.path.lexists(SOURCE/'.git/objects/info/alternates')
        and not os.path.lexists(SOURCE/'.git/objects/info/http-alternates')
        and (REPO/'objects/info/alternates').read_bytes()==(
            str(SOURCE/'.git/objects')+'\n').encode(),'READONLY_ALTERNATES_SCOPE')
    require(git(REPO,['rev-parse','--is-bare-repository'])[0]==b'true\n'
        and git(REPO,['rev-parse','--absolute-git-dir'])[0].decode().strip()==str(REPO)
        and git(REPO,['rev-parse','--path-format=absolute','--git-common-dir'])[0].decode().strip()==str(REPO),
        'FIXED_BARE_REPOSITORY_REQUIRED')
    if network:
        for arguments in (['remote','get-url','--all','origin'],
                          ['remote','get-url','--push','--all','origin']):
            raw=git(REPO,arguments)[0]
            lines=raw.splitlines()
            require(len(lines)==1 and lines[0] and hashlib.sha256(lines[0]).hexdigest()==REMOTE_SHA,
                'EFFECTIVE_REMOTE_DESTINATION_CHANGED')


def validate_export_attempt(attempt, index, now):
    runtime_origins()
    from qtrade.research.unified_market import document
    # Reuse the producer contract's signed-document/authority validator.
    document(attempt,{'schema_version','started_at','state','index_id','error_class',
        'error_code','completed_at','elapsed_seconds'},65_536)
    elapsed=attempt['elapsed_seconds']
    require(attempt['schema_version']=='qtrade.unified-market-export-attempt.v1'
        and attempt['state']=='SUCCEEDED' and attempt['error_class'] is None
        and attempt['error_code'] is None and attempt['index_id']==index['evidence_sha256']
        and type(elapsed) in (int,float) and math.isfinite(elapsed) and 0<=elapsed<=45
        and type(attempt['started_at']) is str and type(attempt['completed_at']) is str,
        'ACTUAL_SUCCESSFUL_EXPORT_BINDING_REQUIRED')
    started=datetime.fromisoformat(attempt['started_at'])
    completed=datetime.fromisoformat(attempt['completed_at'])
    published=datetime.fromisoformat(index['published_at'])
    require(now.tzinfo is not None and started.tzinfo is not None and completed.tzinfo is not None
        and published.tzinfo is not None and started<=published<=completed<=now,
        'EXPORT_ATTEMPT_CLOCK_INVALID')


def ssh_environment(port):
    require(1024<=port<=65535,'PORT_BOUND')
    raw=source(['config','--get','core.sshCommand']).strip()
    require(hashlib.sha256(raw).hexdigest()==SSH_SHA,'EXISTING_SSH_COMMAND_CHANGED')
    original=shlex.split(raw.decode())
    require(original[0]=='ssh' and len(original)==13,'SSH_COMMAND_SCOPE')
    require(not any('ProxyCommand' in item or 'ControlMaster' in item or item in {'-M','-S'}
                    for item in original),'SSH_TRANSPORT_OVERRIDE')
    proxy=ROOT/'publication_loopback_proxy_20261028.py'
    require(not proxy.is_symlink() and file_sha(proxy)==PROXY_SHA,'PROXY_SOURCE_CHANGED')
    proxy_command=shlex.join([str(BASE/'venv/bin/python'),'-B',str(proxy),str(port)])
    command=['/usr/bin/ssh','-F','/dev/null','-o','BatchMode=yes',
        '-o','StrictHostKeyChecking=yes','-o','PermitLocalCommand=no',
        '-o','ClearAllForwardings=yes',
        '-o','ControlMaster=no','-o','ControlPath=none','-o','ControlPersist=no',
        '-o','ProxyCommand='+proxy_command,*original[1:]]
    env=clean_env();env['GIT_SSH_COMMAND']=shlex.join(command)
    return env

def initialize():
    guard_scope()
    require(not REPO.exists(),'NEW_OPERATOR_REPOSITORY_REQUIRED')
    raw=source(['config','--get','remote.origin.url']).strip()
    require(hashlib.sha256(raw).hexdigest()==REMOTE_SHA,'FIXED_OBSERVATIONS_REMOTE_REQUIRED')
    git(REPO,['init','--bare',str(REPO)])
    alternate=REPO/'objects/info/alternates'
    with alternate.open('xb') as f:
        f.write((str(SOURCE/'.git/objects')+'\n').encode());f.flush();os.fsync(f.fileno())
    git(REPO,['config','remote.origin.url',raw.decode()])
    guard_repository(network=True)

def prepare():
    guard_repository()
    runtime_origins()
    from qtrade.research.unified_market import validate_index,verify_shard_bytes
    require(not (ROOT/'target.json').exists(),'TARGET_ALREADY_FROZEN')
    target=source(['rev-parse','HEAD']).decode().strip()
    require(re.fullmatch('[a-f0-9]{40}',target) is not None,'EXACT_COMMIT_REQUIRED')
    attempt_path=BASE/'unified-data/market-export-attempt.json'
    require(attempt_path.is_file() and not any(p.is_symlink() for p in (attempt_path,*attempt_path.parents))
        and attempt_path.stat().st_nlink==1 and attempt_path.stat().st_size<=65_536,'EXPORT_ATTEMPT_PATH_OR_SIZE')
    attempt_raw=attempt_path.read_bytes()
    require(len(attempt_raw)<=65_536,'EXPORT_ATTEMPT_PATH_OR_SIZE')
    attempt=json.loads(attempt_raw)
    raw=source(['show',target+':unified-market.json'])
    now=datetime.now(timezone.utc)
    index=validate_index(json.loads(raw),now)
    validate_export_attempt(attempt,index,now)
    require(len(index['entries'])==3000,'COMPLETE_INDEX_ENTRIES_REQUIRED')
    require(index['implementation_sources']['scripts/publish_unified_market.py']
        =='64caab0023626ef0f531f563c3a392d4a2429a9a6c004c5789af17321bbd8d8a',
        'REVIEWED_EXPORT_SOURCE_REQUIRED')
    # Native Git's immutable objects bind the exact prevalidated committed tree.
    refs=sorted({r['sha256'] for entry in index['entries'] for r in entry['shards']})
    require(len(refs)==index['total_files'],'COMPLETE_SHARD_REFERENCE_SET')
    specifications={key:[] for key in refs}
    for entry in index['entries']:
        for ref in entry['shards']:
            specifications[ref['sha256']].append((ref,entry))
    names=[target+':unified-market/objects/'+key+'.json' for key in refs]
    metadata=source(['cat-file','--batch-check'],input_data=('\n'.join(names)+'\n').encode())
    rows=metadata.splitlines();require(len(rows)==len(refs),'ALL_GIT_OBJECTS_REQUIRED')
    object_ids=[]
    for key,row in zip(refs,rows,strict=True):
        fields=row.split();ref,_=specifications[key][0]
        require(len(fields)==3 and re.fullmatch(b'[a-f0-9]{40}',fields[0]) is not None
            and fields[1]==b'blob' and int(fields[2])==ref['bytes'],'GIT_OBJECT_SIZE_OR_TYPE')
        object_ids.append(fields[0])
    bound=index['total_bytes']+len(refs)*128
    payload=source(['cat-file','--batch'],input_data=b'\n'.join(object_ids)+b'\n',maximum=bound)
    offset=0;now=datetime.now(timezone.utc);started=time.monotonic()
    for key,oid in zip(refs,object_ids,strict=True):
        end=payload.index(b'\n',offset);header=payload[offset:end].split();ref,_=specifications[key][0]
        require(header==[oid,b'blob',str(ref['bytes']).encode()],'GIT_BATCH_BINDING')
        value=payload[end+1:end+1+ref['bytes']];offset=end+ref['bytes']+2
        for occurrence,entry in specifications[key]:
            verify_shard_bytes(value,occurrence,index,entry,now)
        require(time.monotonic()-started<=90,'FULL_FROZEN_DELIVERY_AUDIT_BUDGET')
    require(offset==len(payload),'GIT_BATCH_TRAILING_BYTES')
    plan={'schema_version':'qtrade.native-relay-target.v1','target_commit':target,
        'attempt_root':str(APPROVED_ATTEMPT),
        'index_id':index['evidence_sha256'],'index_raw_sha256':hashlib.sha256(raw).hexdigest(),
        'index_published_at':index['published_at'],'index_entries':len(index['entries']),
        'shard_count':len(refs),'shard_refs_sha256':hashlib.sha256(json.dumps(refs).encode()).hexdigest(),
        'export_attempt_sha256':hashlib.sha256(attempt_raw).hexdigest(),
        'export_elapsed_seconds':attempt['elapsed_seconds'],'remote_url_sha256':REMOTE_SHA,
        'source_revision':'fba51aade2ee2410dda087e95cf054e53375ed3a',
        'source_ref_or_worktree_changes':False,'ordinary_publisher_success':False,
        'all_frozen_git_shards_verified':True,'private_raw_replay_performed':False,
        'frozen_shard_audit_seconds':time.monotonic()-started,
        'captured_at':datetime.now(timezone.utc).isoformat()}
    save('target.json',plan);return plan

def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['init','prepare','ls','push'])
    parser.add_argument('--port',type=int);parser.add_argument('--expected-plan-sha256');args=parser.parse_args()
    require(sys.flags.optimize==0 and sys.dont_write_bytecode and sys.flags.safe_path
        and os.environ.get('LIVE_TRADING_ENABLED')=='false','SAFE_RUNTIME_REQUIRED')
    guard_scope()
    require(file_sha(RELEASE/'scripts/publish_research_snapshot.py')==PUBLISH_SHA,
        'OWNED_GIT_CLEANUP_SOURCE_CHANGED')
    process=Path('/proc/647839')
    fields=(process/'stat').read_text().rsplit(')',1)[1].split()
    require(fields[19]=='1022098698' and Path(os.readlink(process/'cwd'))==RELEASE
        and b'LIVE_TRADING_ENABLED=false\0' in (process/'environ').read_bytes(),
        'EXISTING_PUBLISHER_OWNER_CHANGED')
    if args.mode=='init':initialize();result={'state':'ISOLATED_REPOSITORY_INITIALIZED'}
    elif args.mode=='prepare':result=prepare()
    else:
        require(args.port is not None,'EXPLICIT_LEASE_PORT_REQUIRED')
        guard_repository(network=True)
        env=ssh_environment(args.port)
        if args.mode=='ls':
            guard_repository(network=True)
            raw,elapsed=git(REPO,['ls-remote','origin','refs/heads/main'],env=env)
            require(re.fullmatch(b'[a-f0-9]{40}\trefs/heads/main\n',raw) is not None,'MAIN_ADVERTISEMENT_REQUIRED')
            result={'state':'READONLY_NATIVE_RELAY_PASS','remote_main':raw[:40].decode(),
                'elapsed_seconds':elapsed,'public_write':False}
        else:
            require(type(args.expected_plan_sha256) is str
                and re.fullmatch('[a-f0-9]{64}',args.expected_plan_sha256) is not None
                and file_sha(ROOT/'target.json')==args.expected_plan_sha256,'EXPECTED_PLAN_HASH_REQUIRED')
            plan=json.loads((ROOT/'target.json').read_bytes())
            require(plan['attempt_root']==str(APPROVED_ATTEMPT)
                and plan['source_revision']=='fba51aade2ee2410dda087e95cf054e53375ed3a'
                and re.fullmatch('[a-f0-9]{40}',plan['target_commit']) is not None,'TARGET_BINDING_REQUIRED')
            raw=source(['show',plan['target_commit']+':unified-market.json'])
            require(hashlib.sha256(raw).hexdigest()==plan['index_raw_sha256'],'FROZEN_INDEX_CHANGED')
            guard_repository(network=True)
            save('PUSH_INTENT.json',{'target_commit':plan['target_commit'],
                'target_plan_sha256':file_sha(ROOT/'target.json'),
                'at':datetime.now(timezone.utc).isoformat(),'no_retry_on_uncertain':True})
            try:
                guard_repository(network=True)
                _,elapsed=git(REPO,['push','origin',plan['target_commit']+':refs/heads/main'],env=env)
            except Exception:
                save('PUSH_UNCERTAIN.json',{'state':'OUTCOME_UNCERTAIN_NO_RETRY'})
                raise
            result={'state':'OPERATOR_ASSISTED_NATIVE_PUSH_ACKNOWLEDGED',
                'target_commit':plan['target_commit'],'elapsed_seconds':elapsed,
                'ordinary_publisher_success':False,'export_budget_seconds':45,'git_budget_seconds':90}
            save('PUSH_RESULT.json',result)
    print(json.dumps(result,sort_keys=True))

if __name__=='__main__':main()
