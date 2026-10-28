"""Synthetic local-bare comparison; no URL, SSH, credentials or production path.

Each invocation creates a unique new fixture root and never reuses old refs.
The optional local auxiliary ref is a separate diagnostic, not approval to
change the production publisher's refs or configuration.
"""
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import struct
import shlex
from datetime import datetime, timezone


GIT=Path(shutil.which('git')).resolve()
ENV={key:os.environ[key] for key in ('SYSTEMROOT','WINDIR','COMSPEC','TEMP','TMP') if key in os.environ}
ENV.update(PATH=str(GIT.parent)+os.pathsep+str(GIT.parent.parent/'bin'),
    GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,
    GIT_TERMINAL_PROMPT='0',GIT_NO_REPLACE_OBJECTS='1',GIT_ALLOW_PROTOCOL='file',
    GIT_AUTHOR_NAME='SYNTHETIC',GIT_AUTHOR_EMAIL='synthetic@example.invalid',
    GIT_COMMITTER_NAME='SYNTHETIC',GIT_COMMITTER_EMAIL='synthetic@example.invalid',
    GIT_AUTHOR_DATE='2026-10-28T00:00:00+00:00',GIT_COMMITTER_DATE='2026-10-28T00:00:00+00:00',
    LIVE_TRADING_ENABLED='false')


def run(repo,args,raw=None,trace=None):
    assert repo.is_absolute() and repo.resolve()==repo
    env=dict(ENV)
    if trace is not None:env['GIT_TRACE_PACKET']=str(trace)
    result=subprocess.run([str(GIT),'-C',str(repo),'-c','core.hooksPath='+os.devnull,
        '-c','gc.auto=0','-c','protocol.allow=never','-c','protocol.file.allow=always',*args],
        input=raw,capture_output=True,env=env,timeout=30)
    assert result.returncode==0,(args,result.stderr.decode(errors='replace'))
    return result.stdout


def snapshot(repo):
    return {p.relative_to(repo).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in repo.rglob('*') if p.is_file()}


def commit(repo,label):
    run(repo,['add','--all']);run(repo,['commit','-m',label])
    return run(repo,['rev-parse','HEAD']).decode().strip()


def probe(root,negotiate,metadata,local_aux_ref):
    root.mkdir()
    source=root/'sender';source.mkdir();run(source,['init','--template=','-b','main'])
    (source/'latest.json').write_bytes(b'{"synthetic":"old-public"}\n')
    old=commit(source,'SYNTHETIC baseline')
    remote=root/'remote.git';remote.mkdir();run(remote,['init','--bare','--template='])
    run(remote,['config','receive.unpackLimit','0'])
    (remote/'synthetic-local-fixture-only').write_bytes(b'NO_NETWORK_OR_PRODUCTION\n')
    run(source,['push',str(remote),'HEAD:refs/heads/main'])
    initial_main_tree=run(remote,['rev-parse','refs/heads/main^{tree}']).strip().decode()
    rng=random.Random(17)
    for number in range(4):
        raw=(json.dumps({'synthetic_public':rng.randbytes(32768).hex(),
            'live_trading_enabled':False},sort_keys=True)+'\n').encode()
        path=source/'unified-market/objects'/(hashlib.sha256(raw).hexdigest()+'.json')
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    (source/'unified-market.json').write_bytes(b'{"synthetic":"complete-new-index"}\n')
    new=commit(source,'SYNTHETIC complete new public generation')
    target_oids={line.split(b'\t')[0].split()[2].decode() for line in
        run(source,['ls-tree','-r',new]).splitlines() if b'latest.json' not in line}
    assert len(target_oids)==5
    aux=root/'auxiliary-stage.git';aux.mkdir();run(aux,['init','--bare','--template='])
    (aux/'objects/info/alternates').write_bytes((str(source/'.git/objects')+'\n').encode())
    tree=run(aux,['mktree'],b''.join(f'100644 blob {oid}\tpublic-{i:04d}.json\n'.encode()
        for i,oid in enumerate(sorted(target_oids)))).strip().decode()
    aux_commit=run(aux,['commit-tree',tree],b'SYNTHETIC certified object stage\n').strip().decode()
    aux_ref='refs/heads/qtrade-object-stage/synthetic-0001'
    source_before=snapshot(source)
    run(aux,['push',str(remote),aux_commit+':'+aux_ref])
    assert snapshot(source)==source_before
    assert run(remote,['rev-parse','refs/heads/main']).strip().decode()==old
    assert run(remote,['rev-parse','refs/heads/main^{tree}']).strip().decode()==initial_main_tree
    assert run(remote,['show',aux_ref+':public-0000.json'])
    # Strictly bound and verify commit/tree metadata only; no blob import.
    metadata_pack=None
    if metadata:
        for oid,kind in ((aux_commit,'commit'),(tree,'tree')):
            raw=run(aux,['cat-file',kind,oid])
            assert hashlib.sha1((kind+' '+str(len(raw))+'\0').encode()+raw).hexdigest()==oid
        metadata_pack=run(aux,['pack-objects','--stdout','--window=0','--depth=0',
            '--no-reuse-delta','--no-reuse-object'],(aux_commit+'\n'+tree+'\n').encode())
        assert len(metadata_pack)<=2*1024*1024
        run(source,['index-pack','--strict','--stdin'],metadata_pack)
        assert run(source,['cat-file','-t',aux_commit]).strip()==b'commit'
        after=snapshot(source)
        assert all(after.get(name)==digest for name,digest in source_before.items())
        added=set(after)-set(source_before)
        assert added and all(name.startswith('.git/objects/pack/') and
            name.endswith(('.pack','.idx','.rev')) for name in added)
    if local_aux_ref:
        assert metadata
        run(source,['update-ref',aux_ref,aux_commit])
    refs_before=run(source,['show-ref'])
    packs_before=set((remote/'objects/pack').glob('*.idx'))
    trace=root/'final-push-packets.log'
    wire=root/'wire-input.bin'
    receiver=shlex.join([Path(sys.executable).as_posix(),'-B','-P',
        'E:/QTrade-temp/publication_local_receive_capture_20261028.py',wire.as_posix(),GIT.as_posix()])
    run(source,['-c','pack.useSparse=false','-c','push.negotiate='+str(negotiate).lower(),
        'push','--receive-pack='+receiver,str(remote),'HEAD:refs/heads/main'],trace=trace)
    assert run(source,['show-ref'])==refs_before
    assert run(remote,['rev-parse','refs/heads/main']).strip().decode()==new
    assert run(remote,['show','refs/heads/main:unified-market.json'])==b'{"synthetic":"complete-new-index"}\n'
    run(remote,['fsck','--strict','--no-reflogs'])
    received=set();packs=[]
    for idx in set((remote/'objects/pack').glob('*.idx'))-packs_before:
        report=run(remote,['verify-pack','-v',str(idx)])
        for line in report.splitlines():
            fields=line.split()
            if len(fields)>=5 and re.fullmatch(b'[0-9a-f]{40}',fields[0]):received.add(fields[0].decode())
        pack=idx.with_suffix('.pack');raw=pack.read_bytes()
        packs.append({'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
            'verify_pack':report.decode()})
    assert received and len(packs)==1
    wire_raw=wire.read_bytes();wire_pack=wire_raw[wire_raw.index(b'PACK'):]
    assert wire_pack[:4]==b'PACK' and struct.unpack('>I',wire_pack[4:8])[0]==2
    wire_count=struct.unpack('>I',wire_pack[8:12])[0]
    assert wire_count==len(received)
    assert len(wire_pack)==packs[0]['bytes']
    assert hashlib.sha256(wire_pack).hexdigest()==packs[0]['sha256']
    return {'wire_pack_object_count':wire_count,'wire_pack_bytes':len(wire_pack),
        'wire_equals_stored_pack':True,'wire_pack_sha256':hashlib.sha256(wire_pack).hexdigest(),
        'push_negotiate':negotiate,'metadata_installed':metadata,
        'sender_aux_ref_added_diagnostic_only':local_aux_ref,'old_main':old,'target_main':new,
        'target_public_blob_oids':sorted(target_oids),'transmitted_objects':sorted(received),
        'staged_blob_retransmitted':sorted(received & target_oids),
        'staged_blob_retransmission_count':len(received & target_oids),
        'final_pack_objects':len(received),'final_pack_bytes':sum(p['bytes'] for p in packs),
        'final_packs':packs,'metadata_pack_bytes':len(metadata_pack) if metadata_pack else 0,
        'main_unchanged_during_aux_staging':True,'source_refs_unchanged_during_main_push':True,
        'packet_trace_sha256':hashlib.sha256(trace.read_bytes()).hexdigest(),
        'has_upload_pack_negotiation':b'upload-pack' in trace.read_bytes(),
        'fixture_root':str(root)}


def main():
    parent=Path('E:/QTrade-temp')/('push-negotiate-wire-probe-v2-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    parent.mkdir()
    cases=[]
    for metadata,local_aux_ref in ((False,False),(True,False),(True,True)):
        for negotiate in (False,True):
            name=f'metadata-{int(metadata)}-localref-{int(local_aux_ref)}-negotiate-{int(negotiate)}'
            cases.append(probe(parent/name,negotiate,metadata,local_aux_ref))
            print(name,'retransmitted',cases[-1]['staged_blob_retransmission_count'],
                'pack_objects',cases[-1]['final_pack_objects'],'pack_bytes',cases[-1]['final_pack_bytes'],flush=True)
    report={'schema':'qtrade.push-negotiate-local-bare-wire-probe.v2',
        'git_version':run(parent,['--version']).decode().strip(),'cases':cases,
        'production_actions':False,'network_calls':False,'credentials_accessed':False,
        'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    raw=(json.dumps(report,indent=2,sort_keys=True)+'\n').encode()
    with (parent/'report.json').open('xb') as handle:handle.write(raw)
    print('REPORT',parent/'report.json','SHA256',hashlib.sha256(raw).hexdigest())


if __name__=='__main__':main()
