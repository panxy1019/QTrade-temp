"""Single temporary loopback lease to one fixed GitHub SSH endpoint."""
from authorized_cluster_read import connect
from pathlib import Path
from hashlib import sha256
from datetime import UTC,datetime
import argparse,json,select,shlex,socket,stat,threading,time

LOCAL=Path('E:/QTrade-temp')
REMOTE='/root/qtrade-local/operator-evidence/publication-relay-cold-20261028T130000Z'
BASE='/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade'
RELEASE=BASE+'/releases/fba51aade2ee2410dda087e95cf054e53375ed3a'
FILES={'publication_loopback_proxy_20261028.py':'f4146011c52405de87570e95e9b2cf084bb2896072453e01fe175d2c9a11aac2',
       'publication_native_relay_operator_v2_20261028.py':'91bfd970f1def55b01a4935907578206a7d91ece39ae857f3b451a99037c84c1'}

def require(value,code):
    if not value:raise ValueError('DESKTOP_RELAY_'+code)

def remote_directory(s, path):
    parts=path.split('/')[1:]; current=''
    for part in parts:
        current+='/'+part
        info=s.lstat(current)
        require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode),
                'REMOTE_DIRECTORY_NOT_REAL')

def checked_source(s, name, digest):
    target=REMOTE+'/'+name
    try:
        info=s.lstat(target)
    except FileNotFoundError:
        with s.open(target,'wx') as f:
            f.write((LOCAL/name).read_bytes());f.flush()
        s.chmod(target,0o600)
        info=s.lstat(target)
    require(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode),
            'REMOTE_SOURCE_NOT_REGULAR')
    require(info.st_size==(LOCAL/name).stat().st_size,'REMOTE_SOURCE_SIZE')
    with s.open(target,'rb') as f:
        require(sha256(f.read()).hexdigest()==digest,'REMOTE_SOURCE_CHANGED')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['init','ls','prepare','push'])
    parser.add_argument('--expected-plan-sha256');args=parser.parse_args()
    require(args.mode!='push' or args.expected_plan_sha256 is not None,'EXPLICIT_REVIEWED_TARGET_REQUIRED')
    for name,digest in FILES.items():require(sha256((LOCAL/name).read_bytes()).hexdigest()==digest,'SOURCE_CHANGED')
    c=connect();transport=c.get_transport();stop=threading.Event();lock=threading.Lock()
    stats={'accepted_channels':0,'rejected_channels':0,'to_github_bytes':0,'from_github_bytes':0,'pump_error_types':[]}
    threads=[];port=None;started=time.monotonic();lease_started=started
    def pump(channel):
        remote=None
        try:
            remote=socket.create_connection(('ssh.github.com',443),timeout=5)
            remote.settimeout(5);channel.settimeout(5)
            inputs=[channel,remote]
            while inputs and not stop.is_set() and time.monotonic()-lease_started<100:
                ready,_,_=select.select(inputs,[],[],0.5)
                for source in ready:
                    raw=source.recv(32768)
                    if not raw:
                        inputs.remove(source)
                        if source is channel:remote.shutdown(socket.SHUT_WR)
                        else:channel.shutdown_write()
                    elif source is channel:
                        remote.sendall(raw);stats['to_github_bytes']+=len(raw)
                    else:
                        channel.sendall(raw);stats['from_github_bytes']+=len(raw)
        except Exception as exc:
            stats['pump_error_types'].append(type(exc).__name__)
        finally:
            if remote is not None:remote.close()
            channel.close()
    def accepted(channel,origin,server):
        # This callback must return immediately so Paramiko can read channel data.
        with lock:
            if (stats['accepted_channels'] or origin[0]!='127.0.0.1'
                    or server[0]!='127.0.0.1' or server[1]!=port):
                stats['rejected_channels']+=1;channel.close();return
            stats['accepted_channels']+=1
        worker=threading.Thread(target=pump,args=(channel,),daemon=True);threads.append(worker);worker.start()
    stamp=datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    dest=LOCAL/'cluster-evidence'/('publication-relay-'+args.mode+'-'+stamp);dest.mkdir()
    try:
        s=c.open_sftp()
        remote_directory(s,REMOTE.rsplit('/',1)[0])
        try:s.mkdir(REMOTE,0o700)
        except OSError:pass
        remote_directory(s,REMOTE)
        for name,digest in FILES.items():
            checked_source(s,name,digest)
        s.close()
        if args.mode in {'ls','push'}:
            lease_started=time.monotonic()
            port=transport.request_port_forward('127.0.0.1',0,handler=accepted)
            require(1024<=port<=65535,'SERVER_LEASE_PORT')
            check="from pathlib import Path; import json; port="+repr(format(port,'04X'))+"; rows=[x.split()[1] for f in ['/proc/net/tcp','/proc/net/tcp6'] for x in Path(f).read_text().splitlines()[1:] if x.split()[1].endswith(':'+port) and x.split()[3]=='0A']; assert rows and all(x.split(':')[0]=='0100007F' for x in rows);print(json.dumps({'loopback_only':True,'listeners':len(rows)}))"
            inp,out,err=c.exec_command(shlex.quote(BASE+'/venv/bin/python')+' -B -',timeout=10);inp.write(check);inp.channel.shutdown_write()
            raw=out.read();error=err.read();require(out.channel.recv_exit_status()==0 and not error,'ACTUAL_LOOPBACK_LISTENER_REQUIRED')
            (dest/'listener-check.json').write_bytes(raw)
        arguments=[BASE+'/venv/bin/python','-B','-P',REMOTE+'/publication_native_relay_operator_v2_20261028.py',args.mode]
        if port is not None:arguments+=['--port',str(port)]
        if args.expected_plan_sha256 is not None:arguments+=['--expected-plan-sha256',args.expected_plan_sha256]
        environment=['env','LIVE_TRADING_ENABLED=false','PYTHONDONTWRITEBYTECODE=1','PYTHONNOUSERSITE=1','PYTHONSAFEPATH=1','PYTHONPATH='+RELEASE+'/src:'+RELEASE]
        _,out,err=c.exec_command(shlex.join(environment+arguments),timeout=130)
        raw=out.read();errors=err.read();rc=out.channel.recv_exit_status()
        (dest/'result.stdout').write_bytes(raw);(dest/'result.stderr').write_bytes(errors)
        result=json.loads(raw) if raw else None
        for worker in threads:worker.join(timeout=6)
        if args.mode in {'ls','push'}:
            attested=(stats['accepted_channels']==1 and stats['rejected_channels']==0
                and stats['to_github_bytes']>0 and stats['from_github_bytes']>0
                and not stats['pump_error_types'] and all(not w.is_alive() for w in threads))
            (dest/'transport-attestation.json').write_text(json.dumps(
                {'relay_attested':attested,**stats},sort_keys=True,indent=2)+'\n')
            require(attested,'REAL_RELAY_NOT_ATTESTED_NO_RETRY_AFTER_PUSH')
        if args.mode=='prepare' and rc==0:
            s=c.open_sftp();s.get(REMOTE+'/target.json',str(dest/'target.json'));s.close()
        metadata={'mode':args.mode,'returncode':rc,'elapsed_seconds':time.monotonic()-started,
            'stdout_sha256':sha256(raw).hexdigest(),'stderr_sha256':sha256(errors).hexdigest(),
            'result':result,'source_hashes':FILES,'expected_plan_sha256':args.expected_plan_sha256,
            'payload_logged':False,'configuration_or_service_changes':False}
        print(json.dumps({'evidence':str(dest),**metadata},sort_keys=True))
        if rc:raise SystemExit(rc)
    finally:
        if port is not None:
            try:transport.cancel_port_forward('127.0.0.1',port)
            except Exception as exc:stats['cancel_error_type']=type(exc).__name__
        stop.set();c.close()
        for worker in threads:worker.join(timeout=6)
        stats['all_pumps_joined']=all(not worker.is_alive() for worker in threads)
        stats['lease_elapsed_seconds']=time.monotonic()-started
        (dest/'relay-safe-metadata.json').write_text(json.dumps(stats,sort_keys=True,indent=2)+'\n')
        print(json.dumps({'relay':stats},sort_keys=True))

if __name__=='__main__':main()
