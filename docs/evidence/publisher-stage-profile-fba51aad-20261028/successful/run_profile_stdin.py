"""New isolated attempt, stdin transfer only; no SFTP or production controls."""
from pathlib import Path
from datetime import UTC, datetime
from hashlib import sha256
import base64
import json
import shlex
import sys
sys.path.insert(0, 'E:/QTrade-temp')
from authorized_cluster_read import connect

local = Path(__file__).resolve().parent
remote = '/root/qtrade-local/operator-evidence/'+local.name
source = '/root/qtrade-local/operator-evidence/market-checkpoint-exact-git-20261028T105253Z/source'
python = '/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python'
names = ['benchmark.py', 'profile_runtime.py', 'profile_driver.py', 'profile_worker.py']
payload = {name: base64.b64encode((local/name).read_bytes()).decode() for name in names}
hashes = {name: sha256((local/name).read_bytes()).hexdigest() for name in names}
prepare = """from pathlib import Path
import base64,json,hashlib,shutil
r=Path(REMOTE);assert r.parent==Path('/root/qtrade-local/operator-evidence')
assert shutil.disk_usage(r.parent).free>2*1024**3
r.mkdir(mode=0o700,exist_ok=False)
payload=PAYLOAD;expected=HASHES
for name,value in payload.items():
 assert '/' not in name and name.endswith('.py')
 raw=base64.b64decode(value);assert hashlib.sha256(raw).hexdigest()==expected[name]
 with (r/name).open('xb') as f:f.write(raw)
 assert hashlib.sha256((r/name).read_bytes()).hexdigest()==expected[name]
print(json.dumps({'prepared':str(r),'helper_hashes':expected}))
""".replace('REMOTE',repr(remote)).replace('PAYLOAD',repr(payload)).replace('HASHES',repr(hashes))
client = connect()
try:
    print(json.dumps({'phase':'STDIN_TRANSFER_START','remote':remote}),flush=True)
    stdin,stdout,stderr = client.exec_command(shlex.quote(python)+' -B -',timeout=45)
    stdin.write(prepare);stdin.flush();stdin.channel.shutdown_write()
    out,err=stdout.read(),stderr.read();rc=stdout.channel.recv_exit_status()
    with (local/'transfer-stdout.log').open('xb') as handle:handle.write(out)
    with (local/'transfer-stderr.log').open('xb') as handle:handle.write(err)
    assert rc==0,err.decode()
    print(json.dumps({'phase':'STDIN_TRANSFER_COMPLETE','helpers':hashes}),flush=True)
    command='cd '+shlex.quote(source)+' && env LIVE_TRADING_ENABLED=false PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. '+shlex.quote(python)+' -B '+shlex.quote(remote+'/benchmark.py')
    _,stdout,stderr=client.exec_command(command,timeout=420)
    collected=bytearray()
    for line in stdout:
        collected.extend(line.encode());print(line.rstrip(),flush=True)
    errors=stderr.read();rc=stdout.channel.recv_exit_status()
    with (local/'stdout.log').open('xb') as handle:handle.write(collected)
    with (local/'stderr.log').open('xb') as handle:handle.write(errors)
    fetch="""from pathlib import Path
import base64,json
r=Path(REMOTE);rows={}
for p in r.iterdir():
 if p.is_file() and p.suffix in {'.json','.log'}:
  raw=p.read_bytes();assert len(raw)<8000000
  rows[p.name]=base64.b64encode(raw).decode()
print(json.dumps(rows))
""".replace('REMOTE',repr(remote))
    stdin,out,err=client.exec_command(shlex.quote(python)+' -B -',timeout=45)
    stdin.write(fetch);stdin.flush();stdin.channel.shutdown_write()
    raw,fetch_errors=out.read(),err.read();fetch_rc=out.channel.recv_exit_status()
    assert fetch_rc==0,fetch_errors.decode()
    for name,encoded in json.loads(raw).items():
        assert '/' not in name
        with (local/name).open('xb') as handle:handle.write(base64.b64decode(encoded))
    result={'remote':remote,'recorded_at':datetime.now(UTC).isoformat(),'exit_code':rc,
        'helpers_sha256':hashes,'production_mutations':False,'transfer':'SSH_STDIN_HASH_VERIFIED_NEW_FILES_ONLY',
        'private_mount_raw_not_downloaded_or_committed':True,
        'artifacts':{p.name:sha256(p.read_bytes()).hexdigest() for p in local.iterdir() if p.is_file()}}
    with (local/'execution.json').open('x') as handle:json.dump(result,handle,sort_keys=True,indent=2)
    print(json.dumps({'phase':'FINISHED','exit_code':rc,'local':str(local),'stderr_tail':errors.decode()[-1500:]}),flush=True)
    raise SystemExit(rc)
finally:
    client.close()
