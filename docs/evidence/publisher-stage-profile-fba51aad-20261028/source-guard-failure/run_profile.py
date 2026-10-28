"""Deploy only new isolated profiling helpers; use the already authorized connection."""
from pathlib import Path
from datetime import UTC,datetime
from hashlib import sha256
import json
import shlex
import sys
sys.path.insert(0,'E:/QTrade-temp')
from authorized_cluster_read import connect

local = Path(__file__).resolve().parent
remote = '/root/qtrade-local/operator-evidence/'+local.name
source = '/root/qtrade-local/operator-evidence/market-checkpoint-exact-git-20261028T105253Z/source'
python = '/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python'
helpers = ['benchmark.py','profile_runtime.py','profile_driver.py','profile_worker.py']
client = connect()
try:
    with client.open_sftp() as sftp:
        sftp.mkdir(remote,mode=0o700)
        for name in helpers:
            sftp.put(str(local/name),remote+'/'+name)
    command = 'cd '+shlex.quote(source)+' && env LIVE_TRADING_ENABLED=false PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. '+shlex.quote(python)+' -B '+shlex.quote(remote+'/benchmark.py')
    print(json.dumps({'phase':'START','remote':remote,'helpers':{n:sha256((local/n).read_bytes()).hexdigest() for n in helpers}}),flush=True)
    _,stdout,stderr = client.exec_command(command,timeout=420)
    collected = bytearray()
    for line in stdout:
        collected.extend(line.encode());print(line.rstrip(),flush=True)
    errors = stderr.read();code = stdout.channel.recv_exit_status()
    for name,raw in [('stdout.log',bytes(collected)),('stderr.log',errors)]:
        with (local/name).open('xb') as handle:handle.write(raw)
    with client.open_sftp() as sftp:
        for name in sftp.listdir(remote):
            assert '/' not in name
            if name.endswith(('.json','.log')):
                sftp.get(remote+'/'+name,str(local/name))
    execution = {'remote':remote,'exit_code':code,'recorded_at':datetime.now(UTC).isoformat(),
        'production_mutations':False,'private_mount_raw_not_downloaded_or_committed':True,
        'artifacts':{p.name:sha256(p.read_bytes()).hexdigest() for p in local.iterdir() if p.is_file()}}
    with (local/'execution.json').open('x') as handle:json.dump(execution,handle,sort_keys=True,indent=2)
    print(json.dumps({'phase':'FINISHED','exit_code':code,'local':str(local),'stderr_tail':errors.decode()[-1500:]}),flush=True)
    raise SystemExit(code)
finally:
    client.close()
