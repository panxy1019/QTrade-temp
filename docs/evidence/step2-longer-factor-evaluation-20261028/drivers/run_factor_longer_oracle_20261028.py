import hashlib
import importlib.util
import json
import shlex
from pathlib import Path

local = Path('E:/QTrade-temp/factor-longer-s2b-20261028T112943Z-86cd84da')
locations = json.loads((local / 'run-locations.json').read_bytes())
remote, source = locations['remote'], locations['source']
script = Path('E:/QTrade-temp/audit_factor_longer_prices_20261028.py')
compile(script.read_text(), str(script), 'exec')
spec = importlib.util.spec_from_file_location('authorized_s2b_oracle', 'E:/QTrade-temp/authorized_cluster_read.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
client = module.connect()
try:
    sftp = client.open_sftp()
    try:
        sftp.put(str(script), remote + '/independent_actual_price_oracle.py')
        with sftp.open(remote + '/independent_actual_price_oracle.py', 'rb') as handle:
            assert handle.read() == script.read_bytes()
        command = 'nice -n 10 /usr/bin/timeout --signal=TERM --kill-after=5 180s env LIVE_TRADING_ENABLED=false PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=' + shlex.quote(source + '/src:' + source) + ' /root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python -P -B ' + shlex.quote(remote + '/independent_actual_price_oracle.py') + ' ' + shlex.quote(remote) + ' ' + shlex.quote(source)
        _, out, err = client.exec_command(command, timeout=240)
        lines = []
        for line in out:
            lines.append(line)
            print(line.rstrip(), flush=True)
        stderr = err.read().decode()
        status = out.channel.recv_exit_status()
        (local / 'independent-oracle-stdout.log').write_text(''.join(lines))
        (local / 'independent-oracle-stderr.log').write_text(stderr)
        (local / 'independent-oracle-operator.json').write_text(json.dumps({'returncode': status,
            'command': command, 'oracle_source_sha256': hashlib.sha256(script.read_bytes()).hexdigest()}, indent=2))
        print('OPERATOR_STATUS', status, flush=True)
        if stderr:
            print(stderr, flush=True)
        try:
            sftp.get(remote + '/independent-actual-price-oracle.json', str(local / 'independent-actual-price-oracle.json'))
        except FileNotFoundError:
            pass
    finally:
        sftp.close()
finally:
    client.close()
