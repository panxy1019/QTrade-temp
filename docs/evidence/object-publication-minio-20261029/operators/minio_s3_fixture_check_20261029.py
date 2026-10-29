"""Owned, loopback-only MinIO integration; no production/cloud endpoint selection.

Random fixture credentials exist only in parent/child memory. Fault wrappers
simulate lost acknowledgements around real S3 operations, not a TCP fault test.
"""
from __future__ import annotations

import importlib.util
import json
import os
import resource
import re
import secrets
import signal
import shutil
import socket
import subprocess
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import Barrier
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

ROOT = Path(__file__).parent
BASE = Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
FBA = BASE/'releases/fba51aade2ee2410dda087e95cf054e53375ed3a'
SOURCE_GIT = 'abe2986cd6e3f3faeb4984454498e4c67f1646d3'
BUILD = ROOT/'build-result.json'


def dump(path, value):
    raw=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
    with path.open('xb') as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(d)
    finally:os.close(d)


def module(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def unused_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def main():
    from qtrade.research import object_publication as op
    from qtrade.research import r2_object_store as sdk
    import boto3
    import botocore.session
    from botocore.config import Config
    from botocore.loaders import Loader
    from botocore.plugin import PluginContext,reset_plugin_context,set_plugin_context

    assert ROOT.parent == Path('/root/qtrade-local/operator-evidence')
    assert ROOT.name == 'minio-protocol-20261029T163700Z'
    assert all(not p.is_symlink() for p in (ROOT,*ROOT.parents))
    assert shutil.disk_usage(ROOT).free>2*1024**3
    assert shutil.disk_usage(BASE).free>100*1024**3
    build=json.loads(BUILD.read_bytes());binary=ROOT/'minio'
    assert sha256(binary.read_bytes()).hexdigest()==build['binary_sha256']
    run=ROOT/('run-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    run.mkdir(mode=0o700);(run/'home').mkdir(mode=0o700);(run/'data').mkdir(mode=0o700)
    started=time.monotonic();rows=[];details={};clients=[]
    access='fixture'+secrets.token_hex(12);secret=secrets.token_hex(24)
    port=unused_port();console=unused_port();assert port!=console
    endpoint=f'http://127.0.0.1:{port}'
    # The external supervisor starts THIS harness as a new process group.
    # MinIO remains in that owned group, so forced outer cleanup also covers it.
    assert os.getpgrp()==os.getpid()
    def interrupted(*_):
        raise SystemExit('FIXTURE_INTERRUPTED')
    signal.signal(signal.SIGTERM,interrupted)
    env={'PATH':'/usr/bin:/bin','HOME':str(run/'home'),
         'MINIO_ROOT_USER':access,'MINIO_ROOT_PASSWORD':secret,
         'MINIO_BROWSER':'off','MINIO_UPDATE':'off'}
    child=subprocess.Popen([str(binary),'server','--quiet','--address',f'127.0.0.1:{port}',
        '--console-address',f'127.0.0.1:{console}',str(run/'data')],cwd=run,env=env,
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        start_new_session=False)

    def client():
        sdk._no_sdk_debug()
        session=botocore.session.Session(session_vars={
            'config_file':(None,None,os.devnull,None),
            'credentials_file':(None,None,os.devnull,None),
            'profile':(None,None,None,None),'data_path':(None,None,None,None)})
        session.register_component('data_loader',Loader(
            extra_search_paths=[Loader.BUILTIN_DATA_PATH],include_default_search_paths=False))
        token=set_plugin_context(PluginContext(plugins='DISABLED'))
        try:
            c=boto3.session.Session(botocore_session=session).client('s3',endpoint_url=endpoint,
                region_name='us-east-1',aws_access_key_id=access,aws_secret_access_key=secret,
                use_ssl=False,verify=True,config=Config(signature_version='s3v4',
                    connect_timeout=2,read_timeout=5,retries={'total_max_attempts':1,'mode':'standard'},
                    proxies={},s3={'addressing_style':'path'},max_pool_connections=4,
                    request_checksum_calculation='when_required',response_checksum_validation='when_required'))
        finally:reset_plugin_context(token)
        clients.append(c);return c

    def store(bucket, *, create=True):
        c=client()
        if create:c.create_bucket(Bucket=bucket)
        # Test seam only: retain native SDK/transport guards while marking the
        # entirely different local origin/scope. The R2 production factory is
        # unchanged and cannot select HTTP or localhost.
        result=sdk.R2ObjectStore(c,'0'*32,bucket,writable=True)
        result._endpoint=endpoint
        result.scope_id=sha256(('MINIO_NONPRODUCTION_V1\0'+endpoint+'\0'+bucket).encode()).hexdigest()
        c.meta.events.register('before-send.s3',result._before_send)
        return result

    def check(name, function):
        t=time.monotonic()
        try:
            value=function();rows.append({'case':name,'state':'PASSED','seconds':time.monotonic()-t})
            if value is not None:details[name]=value
        except Exception as error:
            rows.append({'case':name,'state':'FAILED','error_class':type(error).__name__,
                         'seconds':time.monotonic()-t})
            if re.fullmatch(r'(?:NATIVE_RELAY|OBJECT_PUBLICATION|R2_PUBLICATION)_[A-Z0-9_]{1,160}',str(error)):
                rows[-1]['safe_error_code']=str(error)
        dump(run/('case-%02d.json'%len(rows)),rows[-1])

    class Counted:
        def __init__(self, base):
            self.base=base;self.scope_id=base.scope_id;self.calls=Counter()
        def get(self,key):
            self.calls['GET']+=1;return self.base.get(key)
        def put(self,key,raw,**conditions):
            self.calls['PUT']+=1
            self.calls['PUT_LATEST' if key==op.LATEST else 'PUT_IMMUTABLE']+=1
            return self.base.put(key,raw,**conditions)

    class Fault(Counted):
        def __init__(self, base, mode):
            super().__init__(base);self.mode=mode;self.pending=None;self.once=False
        def put(self,key,raw,**conditions):
            if not self.once and ((key==op.LATEST and self.mode in ('ACK_LOST','LATE_CAS'))
                or (key!=op.LATEST and self.mode=='UPLOAD_ACK_LOST')):
                self.once=True;self.pending=(key,raw,conditions)
                if self.mode!='LATE_CAS':super().put(key,raw,**conditions)
                else:self.calls['SUPPRESSED_LATEST']+=1
                raise op.OutcomeUnknown('FIXTURE_RESPONSE_LOSS')
            return super().put(key,raw,**conditions)

    def prepare(base,parent=None):
        return op.prepare_market(data.__getitem__,parent=parent,source_git_commit='c'*40,
                                 now=datetime.now(timezone.utc))[0]

    try:
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        opener=build_opener(ProxyHandler({}),NoRedirect());deadline=time.monotonic()+30
        while True:
            assert child.poll() is None
            try:
                with opener.open(endpoint+'/minio/health/live',timeout=1) as r:
                    assert r.geturl()==endpoint+'/minio/health/live'
                    if r.status==200:break
            except OSError:pass
            assert time.monotonic()<deadline
            time.sleep(.1)
        # Verify the listener belongs to this exact owned process and is IPv4
        # loopback. No command line, environment or startup banner is captured.
        inodes={os.readlink(p)[8:-1] for p in (Path('/proc')/str(child.pid)/'fd').iterdir()
                if p.is_symlink() and os.readlink(p).startswith('socket:[')}
        listeners=[]
        for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
            columns=line.split()
            if columns[3]=='0A' and columns[9] in inodes:listeners.append(columns[1])
        assert f'0100007F:{port:04X}' in listeners
        assert all(value.startswith('0100007F:') for value in listeners)
        details['listener']={'owned_pid':child.pid,'addresses':listeners,'loopback_only':True}
        fixture=module('minio_core_synthetic_fixture',ROOT/'candidate/tests/test_object_publication.py')
        data=fixture.fixture_data()

        def conditional():
            s=store('fixture-conditional');raw=b'{"fixture":1}';key='market/shards/'+sha256(raw).hexdigest()+'.json'
            assert s.get(key) is None
            one=s.put(key,raw,if_none_match='*');assert s.get(key)==one
            try:s.put(key,raw,if_none_match='*')
            except op.PreconditionFailed:pass
            else:raise AssertionError('CREATE_CONDITION_NOT_ENFORCED')
            before=s.put(op.LATEST,b'{"v":1}',if_none_match='*')
            after=s.put(op.LATEST,b'{"v":2}',if_match=before.etag)
            assert after.etag!=before.etag
            try:s.put(op.LATEST,b'{"v":3}',if_match=before.etag)
            except op.PreconditionFailed:pass
            else:raise AssertionError('MATCH_CONDITION_NOT_ENFORCED')
            assert s.get(op.LATEST)==after
            return {'create_412':True,'stale_etag_412':True,'sha256_not_etag':one.etag!=sha256(raw).hexdigest()}
        check('native_conditional_put_and_full_get',conditional)

        def race():
            first=store('fixture-race');second=store('fixture-race',create=False);barrier=Barrier(2)
            def contend(pair):
                s,body=pair;barrier.wait(timeout=5)
                try:s.put(op.LATEST,body,if_none_match='*');return 'WIN'
                except op.PreconditionFailed:return 'REJECTED'
            with ThreadPoolExecutor(2) as pool:
                result=list(pool.map(contend,[(first,b'{"writer":1}'),(second,b'{"writer":2}')]))
            assert sorted(result)==['REJECTED','WIN'];assert first.get(op.LATEST).raw in (b'{"writer":1}',b'{"writer":2}')
            return {'results':sorted(result)}
        check('native_two_writer_conditional_create',race)

        def lost_ack():
            base=store('fixture-cas-ack');fault=Fault(base,'ACK_LOST');p=prepare(base)
            pub=op.Publisher(fault,run/'cas-ack-receipts')
            assert pub.publish(p,now=datetime.now(timezone.utc)).state=='COMMITTED_RECONCILED'
            before=dict(fault.calls)
            assert pub.publish(p,now=datetime.now(timezone.utc)).state=='COMMITTED_RECONCILED'
            assert before==dict(fault.calls-Counter({'GET':fault.calls['GET']-before.get('GET',0)}))
            assert fault.calls['PUT_LATEST']==1
            return {'latest_puts':1,'fault':'APPLICATION_ACK_LOSS_AFTER_REAL_S3_COMMIT'}
        check('committed_cas_ack_loss_readonly_reconciliation',lost_ack)

        def delayed():
            base=store('fixture-late');fault=Fault(base,'LATE_CAS');p=prepare(base)
            pub=op.Publisher(fault,run/'late-receipts')
            assert pub.publish(p,now=datetime.now(timezone.utc)).state=='UNCERTAIN_OLD_HEAD_OBSERVED'
            assert base.get(op.LATEST) is None
            puts=fault.calls['PUT']
            assert op.Publisher(fault,run/'late-receipts').publish(p,now=datetime.now(timezone.utc)).state=='UNCERTAIN_OLD_HEAD_OBSERVED'
            assert fault.calls['PUT']==puts
            key,raw,conditions=fault.pending;base.put(key,raw,**conditions)
            assert pub.reconcile(p).state=='COMMITTED_RECONCILED';assert fault.calls['PUT']==puts
            return {'blind_retry':False,'fault':'APPLICATION_DELAYED_REAL_S3_CAS'}
        check('negative_read_and_restart_never_resend_cas',delayed)

        def upload_ack():
            base=store('fixture-upload-ack');fault=Fault(base,'UPLOAD_ACK_LOST');p=prepare(base)
            pub=op.Publisher(fault,run/'upload-ack-receipts')
            try:pub.publish(p,now=datetime.now(timezone.utc))
            except op.OutcomeUnknown:pass
            else:raise AssertionError('UNKNOWN_UPLOAD_EXPECTED')
            key,raw,_=fault.pending;assert base.get(key).raw==raw
            assert pub.publish(p,now=datetime.now(timezone.utc)).state=='COMMITTED_RECONCILED'
            return {'fault':'APPLICATION_ACK_LOSS_AFTER_REAL_IMMUTABLE_PUT','recovery':'FULL_BODY_READBACK'}
        check('immutable_upload_ack_loss_readback_resume',upload_ack)

        def partial():
            base=store('fixture-partial');first=prepare(base)
            assert op.Publisher(base,run/'partial-first').publish(first,now=datetime.now(timezone.utc)).state=='COMMITTED_RECONCILED'
            old=base.get(op.LATEST);p=prepare(base,parent=op.read_parent(base))
            pub=op.Publisher(base,run/'partial-receipts')
            original=pub.journal.write
            def blocked(kind,binding,result):
                if kind=='cas-intent':raise OSError('SYNTHETIC_BEFORE_POINTER')
                return original(kind,binding,result)
            pub.journal.write=blocked
            try:pub.publish(p,now=datetime.now(timezone.utc))
            except OSError:pass
            else:raise AssertionError('BEFORE_POINTER_FAILURE_EXPECTED')
            assert base.get(op.LATEST)==old
            counted=Counted(base)
            result=op.Publisher(counted,run/'partial-receipts').publish(p,now=datetime.now(timezone.utc))
            assert result.state=='COMMITTED_RECONCILED' and counted.calls['PUT_IMMUTABLE']==0 and counted.calls['PUT_LATEST']==1
            return {'fault':'APPLICATION_FAILURE_BEFORE_POINTER','immutable_reuploads':0,'latest_puts':1}
        check('incomplete_generation_keeps_old_head_and_resumes',partial)

        def corruption():
            base=store('fixture-corrupt');p=prepare(base);pub=op.Publisher(base,run/'corrupt-receipts')
            assert pub.publish(p,now=datetime.now(timezone.utc)).state=='COMMITTED_RECONCILED'
            _,objects=op.prepare_market(data.__getitem__,parent=None,source_git_commit='c'*40,now=p.created_at)
            key,raw=next(iter(objects.items()))
            admin=client();admin.put_object(Bucket='fixture-corrupt',Key=key,Body=raw+b' ')
            try:pub.reconcile(p)
            except (ValueError,op.OutcomeUnknown):pass
            else:raise AssertionError('CORRUPTION_MUST_BE_REJECTED')
            return {'privileged_fixture_overwrite_rejected':True,'delete_acl_certified':False}
        check('remote_bytes_corruption_is_not_a_receipt_hit',corruption)

        def full_load():
            cold=Path('/root/qtrade-local/operator-evidence/publication-cold-recovery-20261028T143000Z')
            helper_path=cold/'publication_cold_recovery_operator_20261028.py'
            assert sha256(helper_path.read_bytes()).hexdigest()=='738c295d55d9b1a4081ca2547d2bfdad3ea59553d0102cc152bb34b3916917fa'
            helper=module('minio_exact_frozen_reader',helper_path)
            def git_read(arguments,maximum=16_000_000):
                assert arguments[0] in ('show','ls-tree')
                env={'PATH':'/usr/bin:/bin','HOME':str(run/'home'),'GIT_CONFIG_NOSYSTEM':'1',
                     'GIT_CONFIG_GLOBAL':'/dev/null','GIT_NO_REPLACE_OBJECTS':'1','GIT_NO_LAZY_FETCH':'1'}
                command=['/usr/bin/git','-c','core.hooksPath=/dev/null','-c','core.useReplaceRefs=false','-c','gc.auto=0',
                         '--git-dir='+str(BASE/'publication/.git'),*arguments]
                proc=subprocess.Popen(command,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL,start_new_session=False)
                try:raw,_=proc.communicate(timeout=30)
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                        try:proc.wait(timeout=2)
                        except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=2)
                    if proc.stdout is not None:proc.stdout.close()
                assert proc.returncode==0 and len(raw)<=maximum
                return raw
            metadata={name:git_read(['show',SOURCE_GIT+':'+name])
                      for name in ('unified-market.json','unified.json','quality.json')}
            index=json.loads(metadata['unified-market.json']);refs={r['sha256']:r for e in index['entries'] for r in e['shards']}
            assert len(index['entries'])==3000 and len(refs)==index['total_files']==5987
            listing=git_read(['ls-tree','-r','-z',SOURCE_GIT,'--','unified-market/objects'])
            object_ids=helper.parse_frozen_tree(listing,sorted(refs))
            base=store('fixture-real-frozen');counted=Counted(base);phases={}
            with helper.public_objects_directory() as directory:
                deadline=time.monotonic()+600
                def reader(name):
                    if name in metadata:return metadata[name]
                    digest=name.rsplit('/',1)[1][:-5]
                    assert name=='unified-market/objects/'+digest+'.json'
                    return helper.read_frozen_shard(directory,digest,refs[digest]['bytes'],object_ids[digest],deadline)
                t=time.monotonic()
                prepared,objects=op.prepare_market(reader,parent=None,source_git_commit=SOURCE_GIT,now=datetime.now(timezone.utc))
                phases['prepare_seconds']=time.monotonic()-t
                assert sum(r['bytes'] for r in refs.values())==167397950
                del objects
                t=time.monotonic();pub=op.Publisher(counted,run/'frozen-receipts')
                result=pub.publish(prepared,now=datetime.now(timezone.utc));phases['cold_publish_seconds']=time.monotonic()-t
                assert result.state=='COMMITTED_RECONCILED'
                cold_calls=dict(counted.calls)
                assert op.read_parent(base).publication_id==prepared.publication_id
                t=time.monotonic();second,objects=op.prepare_market(reader,parent=op.read_parent(base),source_git_commit=SOURCE_GIT,now=datetime.now(timezone.utc))
                phases['unchanged_prepare_seconds']=time.monotonic()-t;del objects
                t=time.monotonic();next_result=pub.publish(second,now=datetime.now(timezone.utc));phases['unchanged_publish_seconds']=time.monotonic()-t
                assert next_result.state=='COMMITTED_RECONCILED'
                assert counted.calls['PUT_IMMUTABLE']-cold_calls['PUT_IMMUTABLE']==1
                assert counted.calls['PUT_LATEST']-cold_calls['PUT_LATEST']==1
                dump(run/'frozen-manifest.json',json.loads(prepared.manifest))
                dump(run/'second-manifest.json',json.loads(second.manifest))
            return {'source_git_commit':SOURCE_GIT,'index_entries':3000,'distinct_shards':5987,
                'shard_bytes':167397950,'cold_calls':cold_calls,'total_calls':dict(counted.calls),'phases':phases,
                'first_publication_id':prepared.publication_id,'second_publication_id':second.publication_id,
                'unchanged_data_reuploads':0,'source_clock_preserved':True,'freshness_accepted':False,
                'production_budget_acceptance':False,'private_raw_replay_repeated':False,
                'source_published_at':index['published_at']}
        check('actual_frozen_3000_entry_5987_shard_two_generations',full_load)
    finally:
        close_errors=0
        for c in clients:
            try:c.close()
            except Exception:close_errors+=1
        cleanup_error=None
        try:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill();child.wait(timeout=2)
        except Exception as error:cleanup_error=type(error).__name__
        details['owned_server_stopped']=child.poll() is not None
        details['client_close_errors']=close_errors
        details['server_cleanup_error_class']=cleanup_error
        report={'schema_version':'qtrade.local-minio-protocol-check.v1',
            'at':datetime.now(timezone.utc).isoformat(),'fixture_kind':'LOCAL_MINIO_ONLY',
            'minio_binary_sha256':build['binary_sha256'],'source_commit':build['minio_source_commit'],
            'boto3_version':boto3.__version__,'botocore_version':__import__('botocore').__version__,
            'cases':rows,'details':details,'seconds':time.monotonic()-started,
            'max_child_rss_kib':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
            'python_max_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'state':'PASSED' if len(rows)==8 and all(r['state']=='PASSED' for r in rows)
                and details['owned_server_stopped'] and cleanup_error is None and close_errors==0 else 'FAILED',
            'cloud_account_used':False,'r2_accepted':False,'public_api_accepted':False,
            'production_changed':False,'credentials_persisted':False,
            'source_sha256':sha256(Path(__file__).read_bytes()).hexdigest()}
        dump(run/'result.json',report)
        print(json.dumps({'run':str(run),'state':report['state'],'cases':rows,'server_stopped':details['owned_server_stopped']}),flush=True)
    return 0 if report['state']=='PASSED' else 1


if __name__=='__main__':
    raise SystemExit(main())
