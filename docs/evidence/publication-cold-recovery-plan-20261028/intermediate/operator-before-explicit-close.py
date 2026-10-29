"""PROPOSED one-shot DIRECT SSH cold recovery; send requires fresh exact-plan user approval.

Derived from frozen native-relay v3 SHA256 76bf3e2653ab51491b966db9f62c415c0d7161b4d642e7f89cefc30b687a61fc.
This file does not change the production publisher or its ordinary 45/90 budgets.
"""
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
import selectors
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

BASE = Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
SOURCE = BASE/'publication'
RELEASE = BASE/'releases/fba51aade2ee2410dda087e95cf054e53375ed3a'
ROOT = Path(__file__).parent
REPO = ROOT/'delivery.git'
APPROVED_ATTEMPT = Path('/root/qtrade-local/operator-evidence/publication-cold-recovery-20261028T143000Z')
LEGACY_ATTEMPT = Path('/root/qtrade-local/operator-evidence/publication-relay-cold-20261028T130000Z')
LEGACY_INTENT_SHA = 'e890b57749ef3cf849ec317acab2b83febb437968c948df0d2df8fcc4107429f'
LEGACY_UNCERTAIN_SHA = '1ad55b8098aba6f1532191373d818b9615b453fb30f94ae7bad77f9210a75859'
LEGACY_PLAN_SHA = 'f33c80814b18cc6e77150535b68b93e6525a1ccdadfaa0bd021707d4cc87416a'
LEGACY_TARGET = 'dde33b30a54d46beb0197a80ddbcb35afb5830da'
EXPECTED_MAIN = '5a0925f47e541726210a8879e2f2bed4ae2a62fd'
BASE_OPERATOR_SHA = '76bf3e2653ab51491b966db9f62c415c0d7161b4d642e7f89cefc30b687a61fc'
COLD_PUSH_SECONDS = 180  # PROPOSED; usable only after the exact prepared plan is explicitly approved.
AUDIT_BUDGET_SECONDS = 90
TREE_LISTING_LIMIT = 16_000_000
TREE_ENTRY_LIMIT = 100_000
REMOTE_SHA = 'ca4d67077d9c3bc08feb8d7e05aa8076e5d1c3114d1b4bbcf906e390ed8e8f2b'
SSH_SHA = '91e5fd67a08092e3ff795305eb4a0618a5755bf5a80a76f6c43f6a892c06125d'
PUBLISH_SHA = 'eed5960ee4aa35135cddbff04235e500c4c8209464d3b14745a5a52afc585918'

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
    require(type(timeout) in (int,float) and math.isfinite(timeout) and 0<timeout<=90,
        'ORDINARY_GIT_90_SECOND_BOUND')
    require(not arguments or arguments[0]!='push','PUSH_REQUIRES_EXPLICIT_COLD_SEND')
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


def audit_remaining(deadline):
    remaining=deadline-time.monotonic()
    require(remaining>0,'FULL_FROZEN_DELIVERY_AUDIT_BUDGET')
    return min(90,remaining)


def guard_repository(*, network=False, deadline=None):
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
    def checked_git(arguments):
        return git(REPO,arguments,timeout=90 if deadline is None else audit_remaining(deadline))[0]
    require(checked_git(['rev-parse','--is-bare-repository'])==b'true\n'
        and checked_git(['rev-parse','--absolute-git-dir']).decode().strip()==str(REPO)
        and checked_git(['rev-parse','--path-format=absolute','--git-common-dir']).decode().strip()==str(REPO),
        'FIXED_BARE_REPOSITORY_REQUIRED')
    if network:
        for arguments in (['remote','get-url','--all','origin'],
                          ['remote','get-url','--push','--all','origin']):
            raw=checked_git(arguments)
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


def parse_frozen_tree(raw, current_refs):
    require(len(raw)<=TREE_LISTING_LIMIT and (not raw or raw.endswith(b'\0')),
        'COMPLETE_FROZEN_TREE_LISTING_REQUIRED')
    rows=raw.split(b'\0')[:-1] if raw else []
    require(len(rows)<=TREE_ENTRY_LIMIT,'FROZEN_TREE_ENTRY_BOUND')
    objects={}
    for row in rows:
        match=re.fullmatch(rb'100644 blob ([0-9a-f]{40})\tunified-market/objects/([0-9a-f]{64})\.json',row)
        require(match is not None,'FROZEN_TREE_OBJECT_PATH_OR_MODE')
        oid,key=match[1].decode(),match[2].decode()
        require(key not in objects,'FROZEN_TREE_DUPLICATE_PATH')
        objects[key]=oid
    require(set(current_refs)<=set(objects),'ALL_CURRENT_FROZEN_TREE_OBJECTS_REQUIRED')
    return objects


@contextmanager
def public_objects_directory():
    # One anchored directory walk replaces repeated ancestor walks per shard.
    # Data hardlinks are allowed: immutable bytes are bound by SHA1 and SHA256.
    paths=[Path(SOURCE.anchor)]
    for part in (*SOURCE.parts[1:],'unified-market','objects'):
        paths.append(paths[-1]/part)
    opened=[]
    try:
        for path in paths:
            if opened:
                descriptor=os.open(path.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,
                    dir_fd=opened[-1][1])
            else:
                descriptor=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            metadata=os.fstat(descriptor)
            opened.append((path,descriptor,(metadata.st_dev,metadata.st_ino)))
            require(stat.S_ISDIR(metadata.st_mode),'PUBLIC_OBJECT_DIRECTORY_REQUIRED')
        yield opened[-1][1]
        for path,descriptor,identity in opened:
            current=path.lstat()
            require(path.resolve()==path and stat.S_ISDIR(current.st_mode)
                and (current.st_dev,current.st_ino)==identity
                and (os.fstat(descriptor).st_dev,os.fstat(descriptor).st_ino)==identity,
                'PUBLIC_OBJECT_DIRECTORY_CHANGED')
    finally:
        for _,descriptor,_ in reversed(opened):
            os.close(descriptor)


def read_frozen_shard(directory,key,expected_bytes,oid,deadline):
    audit_remaining(deadline)
    require(re.fullmatch('[0-9a-f]{64}',key) is not None
        and re.fullmatch('[0-9a-f]{40}',oid) is not None
        and type(expected_bytes) is int and 0<expected_bytes<=1_000_000,'PUBLIC_BLOB_IDENTITY_OR_BOUND')
    descriptor=os.open(key+'.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
    with os.fdopen(descriptor,'rb') as handle:
        before=os.fstat(handle.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink>=1
            and before.st_size==expected_bytes,'PUBLIC_BLOB_FILE_OR_SIZE')
        raw=handle.read(expected_bytes+1)
        after=os.fstat(handle.fileno())
        require(stat.S_ISREG(after.st_mode) and after.st_nlink>=1
            and after.st_size==expected_bytes and len(raw)==expected_bytes,'PUBLIC_BLOB_FILE_OR_SIZE')
    blob=hashlib.sha1()
    blob.update(b'blob '+str(len(raw)).encode()+b'\0');blob.update(raw)
    require(blob.hexdigest()==oid,'FROZEN_GIT_BLOB_BYTES_MISMATCH')
    require(hashlib.sha256(raw).hexdigest()==key,'PUBLIC_BLOB_SHA256_MISMATCH')
    audit_remaining(deadline)
    return raw


def bounded_regular_bytes(path, maximum):
    require(path.is_absolute() and not any(p.is_symlink() for p in (path,*path.parents)),
        'REGULAR_INPUT_PATH_REQUIRED')
    descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    with os.fdopen(descriptor,'rb') as handle:
        before=os.fstat(handle.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink==1
            and 0<before.st_size<=maximum,'REGULAR_INPUT_BOUND')
        raw=handle.read(maximum+1);after=os.fstat(handle.fileno())
    require((before.st_dev,before.st_ino,before.st_size)==(after.st_dev,after.st_ino,after.st_size)
        and len(raw)==after.st_size and len(raw)<=maximum,'REGULAR_INPUT_CHANGED')
    return raw


def legacy_claims():
    intent_raw=bounded_regular_bytes(LEGACY_ATTEMPT/'PUSH_INTENT.json',4096)
    uncertain_raw=bounded_regular_bytes(LEGACY_ATTEMPT/'PUSH_UNCERTAIN.json',4096)
    require(hashlib.sha256(intent_raw).hexdigest()==LEGACY_INTENT_SHA
        and hashlib.sha256(uncertain_raw).hexdigest()==LEGACY_UNCERTAIN_SHA,
        'LEGACY_UNCERTAIN_CLAIMS_CHANGED')
    intent=json.loads(intent_raw);uncertain=json.loads(uncertain_raw)
    require(intent['target_commit']==LEGACY_TARGET and intent['target_plan_sha256']==LEGACY_PLAN_SHA
        and intent['no_retry_on_uncertain'] is True
        and uncertain=={'state':'OUTCOME_UNCERTAIN_NO_RETRY'},'LEGACY_UNCERTAIN_BINDING')
    return {'attempt_root':str(LEGACY_ATTEMPT),'intent_sha256':LEGACY_INTENT_SHA,
        'uncertain_sha256':LEGACY_UNCERTAIN_SHA,'old_target_commit':LEGACY_TARGET,
        'old_target_plan_sha256':LEGACY_PLAN_SHA,'old_claims_modified':False}


def parse_direct_ssh(raw):
    require(hashlib.sha256(raw).hexdigest()==SSH_SHA,'EXISTING_SSH_COMMAND_CHANGED')
    original=shlex.split(raw.decode())
    require(len(original)==13 and original[0]=='ssh','SSH_COMMAND_SCOPE')
    identity=None;options={};position=1
    allowed={'IdentitiesOnly','UserKnownHostsFile','StrictHostKeyChecking','BatchMode',
        'ConnectTimeout','ConnectionAttempts','ServerAliveInterval','ServerAliveCountMax','LogLevel'}
    while position<len(original):
        flag,value=original[position:position+2];position+=2
        if flag=='-i':
            require(identity is None,'SINGLE_IDENTITY_REQUIRED');identity=Path(value)
        else:
            key,separator,setting=value.partition('=')
            require(flag=='-o' and separator and key in allowed and key not in options,
                'EXPLICIT_SSH_OPTION_SCOPE');options[key]=setting
    known=options.get('UserKnownHostsFile','')
    require(identity is not None and identity.is_absolute() and known not in {'','none'}
        and not any(c.isspace() for c in known) and ',' not in known
        and Path(known).is_absolute() and options.get('IdentitiesOnly')=='yes'
        and options.get('StrictHostKeyChecking')=='yes','SINGLE_FIXED_IDENTITY_AND_KNOWN_HOSTS_REQUIRED')
    return original,identity,Path(known)


def ssh_environment():
    raw=source(['config','--get','core.sshCommand']).strip()
    original,identity,known_hosts=parse_direct_ssh(raw)
    require(identity.is_file() and not any(p.is_symlink() for p in (identity,*identity.parents))
        and stat.S_ISREG(identity.stat().st_mode) and identity.stat().st_nlink==1
        and identity.stat().st_uid==os.getuid() and identity.stat().st_mode&0o077==0,
        'FIXED_PRIVATE_IDENTITY_METADATA_REQUIRED')  # Never read or hash the private key.
    known_raw=bounded_regular_bytes(known_hosts,262144)
    command=['/usr/bin/ssh','-F','/dev/null','-o','BatchMode=yes',
        '-o','StrictHostKeyChecking=yes','-o','PermitLocalCommand=no',
        '-o','ClearAllForwardings=yes','-o','GlobalKnownHostsFile=/dev/null',
        '-o','ControlMaster=no','-o','ControlPath=none','-o','ControlPersist=no',
        *original[1:]]
    env=clean_env();env['GIT_SSH_COMMAND']=shlex.join(command)
    binding={'ssh_command_sha256':SSH_SHA,'identity_path_sha256':hashlib.sha256(str(identity).encode()).hexdigest(),
        'known_hosts_path_sha256':hashlib.sha256(str(known_hosts).encode()).hexdigest(),
        'known_hosts_sha256':hashlib.sha256(known_raw).hexdigest(),'known_hosts_bytes':len(known_raw),
        'private_identity_read':False,'transport':'DIRECT_SSH_NO_PROXY_NO_MULTIPLEXING'}
    return env,binding

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
    audit_started=time.monotonic();deadline=audit_started+AUDIT_BUDGET_SECONDS
    guard_repository(deadline=deadline)
    old_claims=legacy_claims();_,ssh_binding=ssh_environment()
    runtime_origins()
    from qtrade.research.unified_market import validate_index,verify_shard_bytes
    require(not (ROOT/'target.json').exists(),'TARGET_ALREADY_FROZEN')
    target=source(['rev-parse','HEAD'],timeout=audit_remaining(deadline)).decode().strip()
    require(re.fullmatch('[a-f0-9]{40}',target) is not None,'EXACT_COMMIT_REQUIRED')
    source(['merge-base','--is-ancestor',EXPECTED_MAIN,target],timeout=audit_remaining(deadline))
    source(['merge-base','--is-ancestor',LEGACY_TARGET,target],timeout=audit_remaining(deadline))
    attempt_path=BASE/'unified-data/market-export-attempt.json'
    require(attempt_path.is_file() and not any(p.is_symlink() for p in (attempt_path,*attempt_path.parents))
        and attempt_path.stat().st_nlink==1 and attempt_path.stat().st_size<=65_536,'EXPORT_ATTEMPT_PATH_OR_SIZE')
    attempt_raw=attempt_path.read_bytes()
    require(len(attempt_raw)<=65_536,'EXPORT_ATTEMPT_PATH_OR_SIZE')
    attempt=json.loads(attempt_raw)
    raw=source(['show',target+':unified-market.json'],timeout=audit_remaining(deadline))
    now=datetime.now(timezone.utc)
    index=validate_index(json.loads(raw),now)
    validate_export_attempt(attempt,index,now)
    require(len(index['entries'])==3000,'COMPLETE_INDEX_ENTRIES_REQUIRED')
    require(index['implementation_sources']['scripts/publish_unified_market.py']
        =='64caab0023626ef0f531f563c3a392d4a2429a9a6c004c5789af17321bbd8d8a',
        'REVIEWED_EXPORT_SOURCE_REQUIRED')
    # Native Git's immutable objects bind the exact prevalidated committed tree.
    refs=sorted({r['sha256'] for entry in index['entries'] for r in entry['shards']})
    require(len(refs)==index['total_files']==5987,'COMPLETE_SHARD_REFERENCE_SET')
    specifications={key:[] for key in refs}
    for entry in index['entries']:
        for ref in entry['shards']:
            specifications[ref['sha256']].append((ref,entry))
    tree_started=time.monotonic()
    listing=source(['ls-tree','-r','-z',target,'--','unified-market/objects'],
        maximum=TREE_LISTING_LIMIT,timeout=audit_remaining(deadline))
    object_ids=parse_frozen_tree(listing,refs)
    tree_seconds=time.monotonic()-tree_started
    now=datetime.now(timezone.utc);started=time.monotonic();bytes_read=0;occurrences=0
    with public_objects_directory() as directory:
        for key in refs:
            ref,_=specifications[key][0]
            value=read_frozen_shard(directory,key,ref['bytes'],object_ids[key],deadline)
            bytes_read+=len(value)
            for occurrence,entry in specifications[key]:
                verify_shard_bytes(value,occurrence,index,entry,now)
                occurrences+=1
                audit_remaining(deadline)
    require(bytes_read==index['total_bytes'],'COMPLETE_CURRENT_BLOB_BYTES_REQUIRED')
    audit_remaining(deadline)
    plan={'schema_version':'qtrade.publication-cold-recovery-target.v1','target_commit':target,
        'attempt_root':str(APPROVED_ATTEMPT),
        'index_id':index['evidence_sha256'],'index_raw_sha256':hashlib.sha256(raw).hexdigest(),
        'index_published_at':index['published_at'],'index_entries':len(index['entries']),
        'shard_count':len(refs),'shard_refs_sha256':hashlib.sha256(json.dumps(refs).encode()).hexdigest(),
        'export_attempt_sha256':hashlib.sha256(attempt_raw).hexdigest(),
        'export_elapsed_seconds':attempt['elapsed_seconds'],'remote_url_sha256':REMOTE_SHA,
        'source_revision':'fba51aade2ee2410dda087e95cf054e53375ed3a',
        'source_ref_or_worktree_changes':False,'ordinary_publisher_success':False,
        'all_frozen_git_shards_verified':True,'private_raw_replay_performed':False,
        'frozen_shard_audit_method':'FROZEN_TREE_OID_AND_GUARDED_WORKTREE_DOUBLE_HASH',
        'frozen_tree_listing_sha256':hashlib.sha256(listing).hexdigest(),
        'frozen_tree_entry_count':len(object_ids),'verified_current_occurrences':occurrences,
        'worktree_bytes_read':bytes_read,'frozen_tree_listing_seconds':tree_seconds,
        'worktree_read_and_verify_seconds':time.monotonic()-started,
        'frozen_shard_audit_budget_seconds':AUDIT_BUDGET_SECONDS,
        'frozen_shard_audit_seconds':time.monotonic()-audit_started,
        'derived_from_operator_sha256':BASE_OPERATOR_SHA,'operator_sha256':file_sha(Path(__file__)),
        'expected_remote_main':EXPECTED_MAIN,'legacy_uncertain_claims':old_claims,
        'ssh_binding':ssh_binding,'approval_state':'PENDING_NEW_EXPLICIT_USER_AUTHORIZATION',
        'proposed_one_shot_cold_push_seconds':COLD_PUSH_SECONDS,
        'owned_cleanup_term_seconds':2,'owned_cleanup_kill_wait_seconds':2,
        'ordinary_export_budget_seconds':45,'ordinary_git_budget_seconds':90,
        'force_push':False,'automatic_retry':False,
        'preflight_advertisement_is_cross_transaction_lock':False,
        'captured_at':datetime.now(timezone.utc).isoformat()}
    audit_remaining(deadline)
    save('target.json',plan);return plan

def guard_owner():
    require(file_sha(RELEASE/'scripts/publish_research_snapshot.py')==PUBLISH_SHA,
        'OWNED_GIT_CLEANUP_SOURCE_CHANGED')
    process=Path('/proc/647839')
    fields=(process/'stat').read_text().rsplit(')',1)[1].split()
    require(fields[19]=='1022098698' and Path(os.readlink(process/'cwd'))==RELEASE
        and b'LIVE_TRADING_ENABLED=false\0' in (process/'environ').read_bytes(),
        'EXISTING_PUBLISHER_OWNER_CHANGED')


def exact_document(path, expected_sha, maximum):
    require(type(expected_sha) is str and re.fullmatch('[a-f0-9]{64}',expected_sha) is not None,
        'EXACT_DOCUMENT_SHA_REQUIRED')
    raw=bounded_regular_bytes(path,maximum)
    require(hashlib.sha256(raw).hexdigest()==expected_sha,'EXACT_DOCUMENT_BYTES_CHANGED')
    def pairs(items):
        result={}
        for key,value in items:
            require(key not in result,'DUPLICATE_DOCUMENT_KEY');result[key]=value
        return result
    def constant(_):raise ValueError('NATIVE_RELAY_NONFINITE_DOCUMENT')
    value=json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)
    require(type(value) is dict,'OBJECT_DOCUMENT_REQUIRED')
    return value


def approved_plan(plan_sha, approval_sha, now):
    plan=exact_document(ROOT/'target.json',plan_sha,65536)
    require(plan['schema_version']=='qtrade.publication-cold-recovery-target.v1'
        and plan['attempt_root']==str(APPROVED_ATTEMPT)
        and plan['source_revision']=='fba51aade2ee2410dda087e95cf054e53375ed3a'
        and plan['derived_from_operator_sha256']==BASE_OPERATOR_SHA
        and plan['operator_sha256']==file_sha(Path(__file__))
        and plan['expected_remote_main']==EXPECTED_MAIN
        and plan['legacy_uncertain_claims']==legacy_claims()
        and re.fullmatch('[a-f0-9]{40}',plan['target_commit']) is not None,
        'TARGET_AND_LINEAGE_BINDING_REQUIRED')
    require(plan['approval_state']=='PENDING_NEW_EXPLICIT_USER_AUTHORIZATION'
        and plan['proposed_one_shot_cold_push_seconds']==COLD_PUSH_SECONDS
        and plan['owned_cleanup_term_seconds']==plan['owned_cleanup_kill_wait_seconds']==2
        and plan['ordinary_export_budget_seconds']==45 and plan['ordinary_git_budget_seconds']==90
        and plan['force_push'] is False and plan['automatic_retry'] is False
        and plan['preflight_advertisement_is_cross_transaction_lock'] is False
        and plan['all_frozen_git_shards_verified'] is True and plan['index_entries']==3000
        and plan['shard_count']==5987 and plan['frozen_shard_audit_budget_seconds']==90
        and type(plan['export_elapsed_seconds']) in (int,float)
        and math.isfinite(plan['export_elapsed_seconds']) and 0<=plan['export_elapsed_seconds']<=45
        and type(plan['frozen_shard_audit_seconds']) in (int,float)
        and math.isfinite(plan['frozen_shard_audit_seconds']) and 0<=plan['frozen_shard_audit_seconds']<=90,
        'PREPARED_AUDIT_AND_PROPOSED_BUDGET_BINDING_REQUIRED')
    approval=exact_document(ROOT/'USER_AUTHORIZATION.json',approval_sha,16384)
    require(set(approval)=={'schema_version','plan_sha256','authorization_reference','authorized_at',
        'authorization_kind','one_shot_cold_push_seconds','ordinary_budgets_unchanged',
        'automatic_retry','force_push'}
        and approval['schema_version']=='qtrade.one-shot-cold-user-authorization.v1'
        and approval['authorization_kind']=='EXPLICIT_NEW_USER_APPROVAL_OF_EXACT_PREPARED_PLAN'
        and approval['plan_sha256']==plan_sha and approval['one_shot_cold_push_seconds']==180
        and approval['ordinary_budgets_unchanged'] is True
        and approval['automatic_retry'] is False and approval['force_push'] is False
        and type(approval['authorization_reference']) is str
        and 1<=len(approval['authorization_reference'])<=256,'NEW_EXACT_PLAN_USER_AUTHORIZATION_REQUIRED')
    captured=datetime.fromisoformat(plan['captured_at']);authorized=datetime.fromisoformat(approval['authorized_at'])
    require(now.tzinfo is not None and captured.tzinfo is not None and authorized.tzinfo is not None
        and captured<=authorized<=now and 0<=(now-authorized).total_seconds()<=3600,
        'NEW_USER_AUTHORIZATION_CLOCK_INVALID')
    return plan


class Progress:
    """Bounded safe extraction; never retain raw stderr or SSH configuration."""
    pattern=re.compile(rb'^(remote: )?(Counting objects|Compressing objects|Writing objects|Resolving deltas):\s+([0-9]{1,3})% \(([0-9]{1,12})/([0-9]{1,12})\)(?:[ ,].*)?$')
    enumerate=re.compile(rb'^(remote: )?Enumerating objects: ([0-9]{1,12}), done\.$')

    def __init__(self):
        self.pending=b'';self.events=[];self.stderr_sha=hashlib.sha256();self.stderr_bytes=0
        self.unknown_sha=hashlib.sha256();self.unknown_bytes=0;self.dropped_events=0

    def unknown(self,raw):
        self.unknown_sha.update(raw);self.unknown_bytes+=len(raw)

    def line(self,raw,elapsed):
        match=self.pattern.fullmatch(raw.strip()) if len(raw)<=4096 else None
        enumeration=self.enumerate.fullmatch(raw.strip()) if len(raw)<=4096 else None
        event=None
        if match:
            percent,current,total=(int(match[i]) for i in (3,4,5))
            if percent<=100 and current<=total:
                event={'phase':match[2].decode('ascii'),'remote':bool(match[1]),
                    'percent':percent,'current':current,'total':total,'monotonic_seconds':elapsed}
        elif enumeration:
            event={'phase':'Enumerating objects','remote':bool(enumeration[1]),
                'total':int(enumeration[2]),'monotonic_seconds':elapsed}
        if event is None:self.unknown(raw)
        elif len(self.events)<4096:self.events.append(event)
        else:self.dropped_events+=1

    def feed(self,raw,elapsed):
        require(type(raw) is bytes and len(raw)<=8192,'TELEMETRY_CHUNK_BOUND')
        self.stderr_sha.update(raw);self.stderr_bytes+=len(raw)
        chunks=re.split(b'[\r\n]',self.pending+raw)
        for line in chunks[:-1]:
            if line:self.line(line,elapsed)
        self.pending=chunks[-1]
        if len(self.pending)>4096:self.unknown(self.pending);self.pending=b''

    def finish(self,elapsed):
        if self.pending:self.line(self.pending,elapsed);self.pending=b''
        return {'schema_version':'qtrade.cold-push-safe-progress.v1','events':self.events,
            'stderr_bytes':self.stderr_bytes,'stderr_sha256':self.stderr_sha.hexdigest(),
            'unknown_text_bytes':self.unknown_bytes,'unknown_text_sha256':self.unknown_sha.hexdigest(),
            'dropped_event_count':self.dropped_events,'raw_stderr_saved':False,
            'writing_100_percent_is_server_ack':False,'no_progress_does_not_identify_network_failure':True}


def cold_push(plan,env):
    require(os.name=='posix','COLD_PUSH_OWNED_PROCESS_GROUP_REQUIRES_POSIX')
    intent_raw=bounded_regular_bytes(ROOT/'PUSH_INTENT.json',16384);intent=json.loads(intent_raw)
    require(intent['target_commit']==plan['target_commit']
        and approved_plan(intent['target_plan_sha256'],intent['user_authorization_sha256'],datetime.now(timezone.utc))==plan,
        'COLD_SEND_REQUIRES_DURABLE_AUTHORIZED_INTENT')
    guard_repository(network=True);runtime_origins();guard_owner()
    from scripts.publish_research_snapshot import _cleanup_git_group
    command=['/usr/bin/git','-c','pack.useSparse=false','-c','gc.auto=0',
        '-c','core.hooksPath=/dev/null','-c','remote.origin.mirror=false',
        '-c','push.followTags=false','--git-dir='+str(REPO),
        'push','--progress','origin',plan['target_commit']+':refs/heads/main']
    save('SEND_STARTED.json',{'intent_sha256':hashlib.sha256(intent_raw).hexdigest(),
        'at':datetime.now(timezone.utc).isoformat(),'automatic_retry':False})
    started=time.monotonic();progress=Progress();child=None
    try:
        with subprocess.Popen(command,env=env,start_new_session=True,stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE) as child:
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(child.stderr,selectors.EVENT_READ)
                    eof=False
                    while not (eof and child.poll() is not None):
                        remaining=COLD_PUSH_SECONDS-(time.monotonic()-started)
                        if remaining<=0:raise subprocess.TimeoutExpired('ONE_SHOT_COLD_PUSH',COLD_PUSH_SECONDS)
                        for key,_ in selector.select(min(0.25,remaining)):
                            raw=os.read(key.fileobj.fileno(),8192)
                            if not raw:selector.unregister(key.fileobj);eof=True
                            else:progress.feed(raw,time.monotonic()-started)
                require(child.returncode==0,'COLD_NONFORCE_PUSH_NOT_ACKNOWLEDGED')
            finally:
                _cleanup_git_group(child)  # Existing frozen helper: TERM 2 seconds + KILL/wait 2 seconds.
    finally:
        telemetry=progress.finish(time.monotonic()-started)
        telemetry.update(elapsed_seconds=time.monotonic()-started,
            git_returncode=None if child is None else child.returncode,
            proposed_cold_push_budget_seconds=180,cleanup_term_seconds=2,cleanup_kill_wait_seconds=2)
        save('PUSH_TELEMETRY.json',telemetry)
    return time.monotonic()-started


def send_cold(plan_sha,approval_sha):
    require(not any(os.path.lexists(ROOT/name) for name in ('PUSH_INTENT.json','PREFLIGHT.json')),
        'ONE_SHOT_ALREADY_CLAIMED_NO_RETRY')
    plan=approved_plan(plan_sha,approval_sha,datetime.now(timezone.utc))
    guard_repository(network=True);env,binding=ssh_environment()
    require(binding==plan['ssh_binding'],'PREPARED_DIRECT_SSH_BINDING_CHANGED')
    raw,elapsed=git(REPO,['ls-remote','origin','refs/heads/main'],env=env)
    require(re.fullmatch(b'[a-f0-9]{40}\trefs/heads/main\n',raw) is not None,'MAIN_ADVERTISEMENT_REQUIRED')
    remote=raw[:40].decode()
    save('PREFLIGHT.json',{'state':'DIRECT_MAIN_ADVERTISEMENT','remote_main':remote,
        'expected_remote_main':EXPECTED_MAIN,'target_commit':plan['target_commit'],
        'at':datetime.now(timezone.utc).isoformat(),'elapsed_seconds':elapsed,
        'cross_transaction_lock':False,'public_write':False})
    if remote==plan['target_commit']:
        result={'state':'REMOTE_ALREADY_EXACT_TARGET_NO_SEND','target_commit':remote,'public_write':False}
        save('ALREADY_TARGET.json',result);return result
    require(remote==EXPECTED_MAIN,'REMOTE_MAIN_DRIFT_RECONCILIATION_REQUIRED')
    require(legacy_claims()==plan['legacy_uncertain_claims'],'LEGACY_UNCERTAIN_CLAIMS_CHANGED')
    raw=source(['show',plan['target_commit']+':unified-market.json'])
    require(hashlib.sha256(raw).hexdigest()==plan['index_raw_sha256'],'FROZEN_INDEX_CHANGED')
    source(['merge-base','--is-ancestor',EXPECTED_MAIN,plan['target_commit']])
    guard_repository(network=True);guard_owner()
    # Recheck the exact authority and public hostkey bytes immediately before the durable one-shot claim.
    require(approved_plan(plan_sha,approval_sha,datetime.now(timezone.utc))==plan,'PREPARED_PLAN_CHANGED')
    env,binding=ssh_environment();require(binding==plan['ssh_binding'],'PREPARED_DIRECT_SSH_BINDING_CHANGED')
    save('PUSH_INTENT.json',{'schema_version':'qtrade.one-shot-direct-cold-intent.v1',
        'target_commit':plan['target_commit'],'target_plan_sha256':plan_sha,
        'user_authorization_sha256':approval_sha,'legacy_uncertain_claims':plan['legacy_uncertain_claims'],
        'operator_sha256':file_sha(Path(__file__)),'at':datetime.now(timezone.utc).isoformat(),
        'expected_remote_main':EXPECTED_MAIN,'no_retry_on_uncertain':True,'force_push':False,
        'transport':'DIRECT_SSH','one_shot_cold_push_seconds':180,
        'ordinary_export_budget_seconds':45,'ordinary_git_budget_seconds':90})
    try:elapsed=cold_push(plan,env)
    except BaseException:
        save('PUSH_UNCERTAIN.json',{'state':'OUTCOME_UNCERTAIN_NO_RETRY',
            'target_commit':plan['target_commit'],'target_plan_sha256':plan_sha})
        raise
    result={'state':'ONE_SHOT_DIRECT_COLD_PUSH_ACKNOWLEDGED_RECONCILIATION_REQUIRED',
        'target_commit':plan['target_commit'],'elapsed_seconds':elapsed,
        'ordinary_publisher_success':False,'ordinary_export_budget_seconds':45,
        'ordinary_git_budget_seconds':90,'one_shot_cold_push_budget_seconds':180,
        'independent_remote_reconciliation_performed':False}
    save('PUSH_RESULT.json',result);return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['init','prepare','ls','send-cold'])
    parser.add_argument('--expected-plan-sha256');parser.add_argument('--user-authorization-sha256')
    args=parser.parse_args()
    require(os.name=='posix' and sys.flags.optimize==0 and sys.dont_write_bytecode and sys.flags.safe_path
        and os.environ.get('LIVE_TRADING_ENABLED')=='false','SAFE_RUNTIME_REQUIRED')
    guard_scope();guard_owner();legacy_claims()
    if args.mode=='init':initialize();result={'state':'ISOLATED_REPOSITORY_INITIALIZED_NO_PUBLIC_WRITE'}
    elif args.mode=='prepare':result=prepare()
    elif args.mode=='ls':
        guard_repository(network=True);env,_=ssh_environment()
        raw,elapsed=git(REPO,['ls-remote','origin','refs/heads/main'],env=env)
        require(re.fullmatch(b'[a-f0-9]{40}\trefs/heads/main\n',raw) is not None,'MAIN_ADVERTISEMENT_REQUIRED')
        result={'state':'READONLY_DIRECT_MAIN','remote_main':raw[:40].decode(),'elapsed_seconds':elapsed,'public_write':False}
    else:result=send_cold(args.expected_plan_sha256,args.user_authorization_sha256)
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':
    try:main()
    except SystemExit:raise
    except BaseException as error:
        # No raw subprocess/SSH/hostkey configuration text or traceback in public logs.
        code=str(error) if type(error) is ValueError and str(error).startswith('NATIVE_RELAY_') else 'FAIL_CLOSED'
        print(json.dumps({'state':'STOPPED_NO_AUTOMATIC_RETRY','error_class':type(error).__name__,'code':code},sort_keys=True))
        raise SystemExit(2) from None
