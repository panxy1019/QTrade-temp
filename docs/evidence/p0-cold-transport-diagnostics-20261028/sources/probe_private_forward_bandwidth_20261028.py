"""Bounded loopback-forward sink, no GitHub connection or publication."""
from authorized_cluster_read import connect
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,shlex,threading,time

PACK='/root/qtrade-local/operator-evidence/publication-wire-pack-20261028T141408Z/source.pack'
DATA=Path('E:/QTrade-temp/cluster-evidence/public-wire-sftp-speed-20261028T141629Z/source.pack')
COUNT=5*1024*1024

def run(window):
    client=connect();transport=client.get_transport();transport.default_window_size=window
    stats={'channels':0,'bytes':0,'expected_bytes':COUNT,'window_size':window,'errors':[]}
    worker=[];port=None;lock=threading.Lock();start=time.monotonic()
    expected=hashlib.sha256(DATA.read_bytes()[:COUNT]).hexdigest()
    def pump(channel):
        digest=hashlib.sha256()
        try:
            channel.settimeout(5)
            while stats['bytes']<COUNT:
                raw=channel.recv(min(65536,COUNT-stats['bytes']))
                if not raw:raise ValueError('SHORT_PUBLIC_TRANSFER')
                digest.update(raw);stats['bytes']+=len(raw)
            if digest.hexdigest()!=expected:raise ValueError('PREFIX_HASH_MISMATCH')
            channel.sendall(b'OK');stats['prefix_sha256']=digest.hexdigest()
        except Exception as error:stats['errors'].append(type(error).__name__)
        finally:channel.close()
    def accepted(channel,origin,server):
        with lock:
            if stats['channels'] or origin[0]!='127.0.0.1' or server!=('127.0.0.1',port):
                channel.close();stats['errors'].append('UNEXPECTED_CHANNEL');return
            stats['channels']+=1
        thread=threading.Thread(target=pump,args=(channel,),daemon=True);worker.append(thread);thread.start()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    dest=Path('E:/QTrade-temp/cluster-evidence')/('forward-bandwidth-'+stamp+'-'+str(window));dest.mkdir()
    try:
        port=transport.request_port_forward('127.0.0.1',0,handler=accepted)
        script="""import socket,time,json,hashlib
from pathlib import Path
p=Path(PACK)
assert not p.is_symlink() and p.stat().st_size==19444657
raw=p.read_bytes()[:COUNT];start=time.monotonic()
with socket.create_connection(('127.0.0.1',PORT),timeout=5) as c:
 c.settimeout(25);c.sendall(raw);ack=c.recv(2);assert ack==b'OK'
print(json.dumps({'bytes':len(raw),'elapsed_seconds':time.monotonic()-start,'sha256':hashlib.sha256(raw).hexdigest(),'publication_write':False}))
""".replace('PACK',repr(PACK)).replace('COUNT',str(COUNT)).replace('PORT',str(port))
        _,out,err=client.exec_command('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python -B -',timeout=35)
        inp=out.channel.makefile_stdin('w');inp.write(script);inp.channel.shutdown_write()
        raw=out.read();errors=err.read();rc=out.channel.recv_exit_status()
        (dest/'stdout.json').write_bytes(raw);(dest/'stderr.log').write_bytes(errors)
        for thread in worker:thread.join(timeout=3)
        stats.update(returncode=rc,all_threads_joined=all(not t.is_alive() for t in worker),
            actual_result=json.loads(raw) if raw else None,elapsed_seconds=time.monotonic()-start)
        (dest/'result.json').write_text(json.dumps(stats,sort_keys=True,indent=2)+'\n')
        print(json.dumps({'evidence':str(dest),**stats}),flush=True)
    finally:
        if port is not None:transport.cancel_port_forward('127.0.0.1',port)
        client.close()

if __name__=='__main__':
    run(2*1024*1024)
    run(16*1024*1024)
