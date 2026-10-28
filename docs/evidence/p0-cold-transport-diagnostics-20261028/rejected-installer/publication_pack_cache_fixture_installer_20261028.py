"""NONPRODUCTION additive pack-cache proof; no production installation API.

Requires a synthetic fixture sentinel and source exactly <fixture>/source.
Git subprocesses permit only local file transport. Public JSON checks here are
the synthetic fixture contract, not the full production historical allowlist.
Directory fsync is native on POSIX; Windows tests explicitly instrument it.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
import uuid

MAX_PACK=64*1024*1024
MAX_INDEX=8*1024*1024
MAX_RAW=512*1024*1024
MAX_OBJECT=32*1024*1024
MAX_OBJECTS=100_000
GIT=Path(shutil.which('git')).resolve()
MARKER=b'{"production":false,"schema":"qtrade.synthetic-pack-cache-fixture.v1"}\n'
PUBLIC_PATH=re.compile(r'(?:latest\.json|unified-market\.json|unified-market/objects/[0-9a-f]{64}\.json)')
OID=re.compile(r'[0-9a-f]{40}')
SHA=re.compile(r'[0-9a-f]{64}')


def require(ok,code):
    if not ok:raise ValueError('PACK_CACHE_'+code)


def remaining(deadline):
    value=deadline-time.monotonic();require(value>0,'DEADLINE');return value


def environment():
    env={k:os.environ[k] for k in ('SYSTEMROOT','WINDIR','COMSPEC','TEMP','TMP') if k in os.environ}
    env.update(PATH=str(GIT.parent)+os.pathsep+str(GIT.parent.parent/'bin')+os.pathsep+'/usr/bin:/bin',
        GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_TERMINAL_PROMPT='0',
        GIT_NO_REPLACE_OBJECTS='1',GIT_ALLOW_PROTOCOL='file',LIVE_TRADING_ENABLED='false')
    return env


def git(repo,args,deadline):
    result=subprocess.run([str(GIT),'-C',str(repo),'-c','gc.auto=0',
        '-c','core.hooksPath='+os.devnull,'-c','protocol.allow=never',
        '-c','protocol.file.allow=always',*args],capture_output=True,env=environment(),
        timeout=min(30,remaining(deadline)))
    require(result.returncode==0,'GIT_REJECTED')
    return result.stdout


def guard(path):
    require(path.is_absolute() and path.resolve()==path
        and not any(p.is_symlink() for p in (path,*path.parents)),'PATH_ALIAS')


def read_regular(path,limit):
    guard(path);before=path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink>=1
        and 0<before.st_size<=limit,'FILE_BOUND')
    descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)
        |getattr(os,'O_NONBLOCK',0))
    with os.fdopen(descriptor,'rb') as handle:
        opened=os.fstat(handle.fileno())
        require(stat.S_ISREG(opened.st_mode) and
            (opened.st_dev,opened.st_ino)==(before.st_dev,before.st_ino),'FILE_CHANGED')
        raw=handle.read(limit+1)
        require(len(raw)==before.st_size and len(raw)<=limit,'FILE_CHANGED')
    return raw


def sync_directory(path):
    guard(path)
    descriptor=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def record(path,value):
    raw=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
    with path.open('xb') as handle:handle.write(raw);handle.flush();os.fsync(handle.fileno())
    sync_directory(path.parent)


def public_json(raw):
    def pairs(items):
        out={}
        for key,value in items:
            require(key not in out,'DUPLICATE_JSON_KEY');out[key]=value
        return out
    def bad_constant(_):raise ValueError('PACK_CACHE_NONFINITE_JSON')
    value=json.loads(raw,object_pairs_hook=pairs,parse_constant=bad_constant)
    require(type(value) is dict,'PUBLIC_JSON_OBJECT')
    def check(item):
        if isinstance(item,dict):
            for key,value in item.items():
                require(key.lower() not in {'password','secret','private_key','token','credential'},'PRIVATE_JSON_FIELD')
                if key in {'live_trading_enabled','external_order_transmission'}:
                    require(value is False,'PUBLIC_AUTHORITY')
                check(value)
        elif isinstance(item,list):
            for value in item:check(value)
    check(value)


@dataclass(frozen=True)
class Candidate:
    pack:Path
    index:Path
    target:str
    base:str
    pack_sha256:str
    index_sha256:str
    # Pinned, source-derived raw SHA256/type/size certificate. Unknown fields fail.
    objects:dict


def validate_fixture(fixture,candidate,deadline):
    fixture=Path(fixture);guard(fixture)
    require(read_regular(fixture/'fixture.json',1024)==MARKER,'SYNTHETIC_FIXTURE_REQUIRED')
    source=fixture/'source';guard(source);guard(source/'.git');guard(source/'.git/objects/pack')
    require(not os.path.lexists(source/'.git/commondir')
        and not os.path.lexists(source/'.git/objects/info/alternates')
        and not os.path.lexists(source/'.git/objects/info/http-alternates'),'SOURCE_REDIRECTION')
    for path in (candidate.pack,candidate.index):
        require(path.is_relative_to(fixture) and path.parent!=source/'.git/objects/pack','CANDIDATE_SCOPE')
    require(OID.fullmatch(candidate.target) and OID.fullmatch(candidate.base)
        and SHA.fullmatch(candidate.pack_sha256) and SHA.fullmatch(candidate.index_sha256),'CANDIDATE_IDENTITY')
    require(type(candidate.objects) is dict and 0<len(candidate.objects)<=MAX_OBJECTS,'CERTIFICATE_BOUND')
    for oid,item in candidate.objects.items():
        require(OID.fullmatch(oid) and type(item) is dict and set(item)=={'kind','bytes','sha256'}
            and item['kind'] in {'blob','tree','commit'} and type(item['bytes']) is int
            and 0<item['bytes']<=MAX_OBJECT and SHA.fullmatch(item['sha256']),'CERTIFICATE_SCHEMA')
    require(sum(item['bytes'] for item in candidate.objects.values())<=MAX_RAW,'RAW_BYTE_BOUND')
    pack=read_regular(candidate.pack,MAX_PACK);index=read_regular(candidate.index,MAX_INDEX)
    require(hashlib.sha256(pack).hexdigest()==candidate.pack_sha256
        and hashlib.sha256(index).hexdigest()==candidate.index_sha256,'CANDIDATE_BYTES_CHANGED')
    require(pack[:4]==b'PACK' and pack[4:8]==b'\0\0\0\2'
        and hashlib.sha1(pack[:-20]).digest()==pack[-20:],'PACK_CHECKSUM')
    pack_id=pack[-20:].hex()
    git(source,['merge-base','--is-ancestor',candidate.base,candidate.target],deadline)
    selected=git(source,['rev-list','--objects','--no-object-names',candidate.target,'^'+candidate.base],deadline).decode().splitlines()
    require(len(selected)==len(set(selected)) and set(selected)==set(candidate.objects),'SOURCE_OBJECT_CLOSURE')
    verification=git(source,['verify-pack','-v',str(candidate.index)],deadline)
    actual={}
    for row in verification.splitlines():
        fields=row.split()
        if fields and re.fullmatch(b'[0-9a-f]{40}',fields[0]):
            oid=fields[0].decode()
            require(len(fields)==5 and oid not in actual,'NONDELTA_PACK_REQUIRED')
            actual[oid]=(fields[1].decode(),int(fields[2]))
    require(set(actual)==set(candidate.objects)
        and all(actual[oid]==(item['kind'],item['bytes']) for oid,item in candidate.objects.items()),'PACK_OBJECT_CLOSURE')
    scratch=fixture/('validation-'+uuid.uuid4().hex+'.git');scratch.mkdir()
    git(scratch,['init','--bare','--template='],deadline)
    for raw,suffix in ((pack,'.pack'),(index,'.idx')):
        with (scratch/'objects/pack'/('pack-'+pack_id+suffix)).open('xb') as handle:handle.write(raw)
    result=subprocess.run([str(GIT),'-C',str(scratch),'cat-file','--batch'],
        input=('\n'.join(sorted(candidate.objects))+'\n').encode(),capture_output=True,
        env=environment(),timeout=min(30,remaining(deadline)))
    require(result.returncode==0,'CANDIDATE_OBJECT_READ')
    require(len(result.stdout)<=MAX_RAW+MAX_OBJECTS*128,'RAW_BYTE_BOUND')
    offset=0;blob_bytes={}
    for oid in sorted(candidate.objects):
        item=candidate.objects[oid];end=result.stdout.index(b'\n',offset)
        header=result.stdout[offset:end].split()
        require(header==[oid.encode(),item['kind'].encode(),str(item['bytes']).encode()],'RAW_OBJECT_HEADER')
        raw=result.stdout[end+1:end+1+item['bytes']];offset=end+item['bytes']+2
        require(result.stdout[offset-1:offset]==b'\n' and len(raw)==item['bytes']
            and hashlib.sha256(raw).hexdigest()==item['sha256']
            and hashlib.sha1((item['kind']+' '+str(len(raw))+'\0').encode()+raw).hexdigest()==oid,'RAW_OBJECT_BYTES')
        if item['kind']=='blob':public_json(raw);blob_bytes[oid]=raw
    require(offset==len(result.stdout),'RAW_OBJECT_TRAILING_BYTES')
    seen_blobs=set()
    for oid,item in candidate.objects.items():
        if item['kind']!='commit':continue
        listing=git(source,['ls-tree','-r','-t','-z',oid],deadline)
        require(not listing or listing.endswith(b'\0'),'PUBLIC_TREE_RECORD')
        for row in listing.split(b'\0')[:-1]:
            metadata,path=row.split(b'\t',1);mode,kind,object_id=metadata.decode().split();name=path.decode('ascii')
            if kind=='tree':
                require(mode=='040000' and name in {'unified-market','unified-market/objects'},'PUBLIC_TREE_PATH')
            else:
                require(mode=='100644' and kind=='blob' and PUBLIC_PATH.fullmatch(name),'PUBLIC_BLOB_PATH')
                if object_id in blob_bytes:
                    if name.startswith('unified-market/objects/'):
                        require(name.split('/')[-1]==hashlib.sha256(blob_bytes[object_id]).hexdigest()+'.json','PUBLIC_CONTENT_ADDRESS')
                    seen_blobs.add(object_id)
    require(seen_blobs==set(blob_bytes),'ALL_NEW_PUBLIC_BLOBS_REVIEWED')
    remaining(deadline)
    return source,pack_id


def copy_checked(source,destination,expected,limit,deadline):
    raw=read_regular(source,limit);require(hashlib.sha256(raw).hexdigest()==expected,'COPY_SOURCE_CHANGED')
    remaining(deadline)
    with destination.open('xb') as handle:handle.write(raw);handle.flush();os.fsync(handle.fileno())
    require(hashlib.sha256(read_regular(destination,limit)).hexdigest()==expected,'COPY_READBACK_CHANGED')
    remaining(deadline)


def existing_identical(path,digest,limit):
    if not os.path.lexists(path):return False
    require(hashlib.sha256(read_regular(path,limit)).hexdigest()==digest,'EXISTING_CONTENT_CONFLICT')
    return True


class InstallationUncertain(RuntimeError):
    pass


def install_fixture_pack(fixture,candidate,old_manual_intent_sha256,deadline,phase_hook=lambda phase:None):
    """Never pushes, installs into only sentinel-bound synthetic <fixture>/source."""
    require(SHA.fullmatch(old_manual_intent_sha256),'OLD_MANUAL_INTENT_BINDING')
    source,pack_id=validate_fixture(fixture,candidate,deadline)
    identity={'schema':'qtrade.synthetic-pack-cache-install.v1','source':str(source),
        'target':candidate.target,'base':candidate.base,'pack_sha256':candidate.pack_sha256,
        'index_sha256':candidate.index_sha256,'old_manual_intent_sha256':old_manual_intent_sha256,
        'object_certificate_sha256':hashlib.sha256(json.dumps(candidate.objects,sort_keys=True).encode()).hexdigest(),
        'manual_intent_consumed':False,'push_performed':False,'production':False}
    # Claim key excludes caller-selected namespace and old-intent substitutions.
    claim_key=hashlib.sha256((str(source)+'\0'+pack_id).encode()).hexdigest()
    claims=fixture/'claims'
    if not claims.exists():claims.mkdir();sync_directory(fixture)
    guard(claims)
    claim=claims/claim_key
    destination=source/'.git/objects/pack'
    final_pack=destination/('pack-'+pack_id+'.pack');final_index=destination/('pack-'+pack_id+'.idx')
    if claim.exists():
        require(read_regular(claim/'INTENT.json',65536)==(json.dumps(identity,sort_keys=True,indent=2)+'\n').encode(),'CLAIM_BINDING_CHANGED')
        require((claim/'RESULT.json').is_file(),'RECONCILIATION_REQUIRED')
        require(existing_identical(final_pack,candidate.pack_sha256,MAX_PACK)
            and existing_identical(final_index,candidate.index_sha256,MAX_INDEX),'COMPLETED_CACHE_MISSING')
        return {'state':'VERIFIED_CACHE_HIT','claim':str(claim),'production':False}
    require(not os.path.lexists(claim),'CLAIM_ALIAS')
    claim.mkdir();sync_directory(claims)
    record(claim/'INTENT.json',identity)
    phase='INTENT_DURABLE'
    try:
        pack_exists=existing_identical(final_pack,candidate.pack_sha256,MAX_PACK)
        index_exists=existing_identical(final_index,candidate.index_sha256,MAX_INDEX)
        require(not index_exists or pack_exists,'INDEX_WITHOUT_PACK')
        if not (pack_exists and index_exists):
            temp_pack=destination/('.qtrade-'+claim_key+'.pack.tmp')
            temp_index=destination/('.qtrade-'+claim_key+'.idx.tmp')
            copy_checked(candidate.pack,temp_pack,candidate.pack_sha256,MAX_PACK,deadline)
            copy_checked(candidate.index,temp_index,candidate.index_sha256,MAX_INDEX,deadline)
            phase='COPIES_VERIFIED';phase_hook(phase)
            require(hashlib.sha256(read_regular(temp_pack,MAX_PACK)).hexdigest()==candidate.pack_sha256
                and hashlib.sha256(read_regular(temp_index,MAX_INDEX)).hexdigest()==candidate.index_sha256,'COPY_FINAL_CHANGED')
            if not pack_exists:os.link(temp_pack,final_pack,follow_symlinks=False)
            phase='PACK_LINKED';phase_hook(phase)
            sync_directory(destination)
            phase='PACK_DIRECTORY_DURABLE';phase_hook(phase)
            if not index_exists:os.link(temp_index,final_index,follow_symlinks=False)
            phase='INDEX_LINKED';phase_hook(phase)
            sync_directory(destination)
            phase='INDEX_DIRECTORY_DURABLE';phase_hook(phase)
            require(existing_identical(final_pack,candidate.pack_sha256,MAX_PACK)
                and existing_identical(final_index,candidate.index_sha256,MAX_INDEX),'INSTALLED_BYTES_CHANGED')
            temp_pack.unlink();temp_index.unlink();sync_directory(destination)
        remaining(deadline)
        result={'state':'CACHE_INSTALLED_LOCAL_FIXTURE_ONLY','pack_id':pack_id,
            'claim':str(claim),'manual_intent_consumed':False,'push_performed':False,'production':False}
        record(claim/'RESULT.json',result)
        return result
    except Exception as error:
        raise InstallationUncertain('PACK_CACHE_INSTALLATION_UNCERTAIN:'+phase+':'+str(claim)) from error
