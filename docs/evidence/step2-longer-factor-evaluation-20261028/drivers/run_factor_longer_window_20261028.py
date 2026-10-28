import hashlib
import importlib.util
import json
import shlex
from pathlib import Path

local = Path('E:/QTrade-temp/factor-longer-s2b-20261028T112943Z-86cd84da')
locations = json.loads((local / 'run-locations.json').read_bytes())
remote, source = locations['remote'], locations['source']
preflight = json.loads((local / 'preflight.json').read_bytes())
assert preflight['state'] == 'READY_FOR_16_SESSION_NONPRODUCTION_DIAGNOSTIC'
assert preflight['dependency_cells'] == 983664
assert preflight['request_sha256'] == hashlib.sha256((local / 'request.json').read_bytes()).hexdigest()
spec = importlib.util.spec_from_file_location('authorized_s2b_run', 'E:/QTrade-temp/authorized_cluster_read.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
client = module.connect()
try:
    sftp = client.open_sftp()
    try:
        with sftp.open(remote + '/run_longer_factor_observation.py', 'rb') as handle:
            assert handle.read() == (local / 'run_longer_factor_observation.py').read_bytes()
        command = 'nice -n 10 /usr/bin/timeout --signal=TERM --kill-after=5 300s env LIVE_TRADING_ENABLED=false PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=' + shlex.quote(source + '/src:' + source) + ' /root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python -P -B ' + shlex.quote(remote + '/run_longer_factor_observation.py') + ' ' + shlex.quote(remote) + ' ' + shlex.quote(source) + ' evaluate'
        _, out, err = client.exec_command(command, timeout=420)
        lines = []
        for line in out:
            lines.append(line)
            print(line.rstrip(), flush=True)
        stderr = err.read().decode()
        status = out.channel.recv_exit_status()
        (local / 'evaluation-stdout.log').write_text(''.join(lines))
        (local / 'evaluation-stderr.log').write_text(stderr)
        (local / 'evaluation-operator-status.json').write_text(json.dumps({'returncode': status,
            'command': command, 'scope': 'single owned nonproduction child only'}, indent=2))
        print('OPERATOR_STATUS', status, flush=True)
        if stderr:
            print(stderr, flush=True)
        for name in ['resources-after.json', 'source-after.json', 'original-experiment-after.json',
                     'receipt.json', 'report.json.gz', 'operator-failure.json']:
            try:
                sftp.get(remote + '/' + name, str(local / name))
            except FileNotFoundError:
                pass
    finally:
        sftp.close()
finally:
    client.close()
