"""Capture safe RO stdout via the pre-authorized connection; never inspect auth."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
root = Path('E:/QTrade-temp/cluster-evidence') / ('publisher-post-handover-readonly-' + stamp)
root.mkdir(exist_ok=False)
helper = Path('E:/QTrade-temp/publisher_post_handover_readonly_20261028.py').read_bytes()
(root / 'helper.py').write_bytes(helper)
sys.path.insert(0, 'E:/QTrade-temp')
from authorized_cluster_read import connect

client = connect()
try:
    stdin, stdout, stderr = client.exec_command(
        'env LIVE_TRADING_ENABLED=false PYTHONDONTWRITEBYTECODE=1 '
        '/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python -B -', timeout=120)
    stdin.write(helper.decode()); stdin.channel.shutdown_write()
    output = stdout.read(); errors = stderr.read(); code = stdout.channel.recv_exit_status()
    (root / 'result.stdout').write_bytes(output); (root / 'result.stderr').write_bytes(errors)
finally:
    client.close()
record = {'helper_sha256': hashlib.sha256(helper).hexdigest(), 'exit_code': code,
    'stdout_sha256': hashlib.sha256(output).hexdigest(), 'stderr_sha256': hashlib.sha256(errors).hexdigest(),
    'stdout_bytes': len(output), 'stderr_bytes': len(errors), 'production_mutations': False}
(root / 'capture.json').write_text(json.dumps(record, sort_keys=True, indent=2) + '\n')
print(root)
print(json.dumps(record))
if code == 0:
    value = json.loads(output)
    print(json.dumps({key: value[key] for key in ('started_at', 'completed_at', 'native',
        'market_export_attempt', 'publication_state', 'git', 'early_process_scan', 'process_scan')}, sort_keys=True))
else:
    print('remote_error_class', errors.decode(errors='replace').splitlines()[-1].split(':', 1)[0])
