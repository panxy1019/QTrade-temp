"""Cleanup only the recorded failed svsem fixture; never production processes."""
import hashlib
import http.client
import json
import os
import signal
import socket
import time
import xmlrpc.client
from pathlib import Path

ROOT = Path('/root/qtrade-local/operator-evidence/svsem-20261028T051219Z-17524d')
CASE = ROOT / 'normal'
EXPECTED = {512541: '1019637672', 512553: '1019637680', 512555: '1019637680', 512556: '1019637684'}
PRODUCTION = 294214


def snap(pid):
    path = Path('/proc') / str(pid)
    try:
        parts = (path / 'stat').read_text().rsplit(')', 1)[1].split()
        return {'pid': pid, 'start_ticks': parts[19], 'state': parts[0],
                'cwd': os.readlink(path / 'cwd'),
                'cmdline': (path / 'cmdline').read_bytes().decode().split('\0')[:-1]}
    except FileNotFoundError:
        return None


class UnixConnection(http.client.HTTPConnection):
    def __init__(self):
        super().__init__('fixture-only.invalid', timeout=3)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(3)
        self.sock.connect(str(CASE / 'rpc.sock'))


def rpc(method):
    connection = UnixConnection()
    try:
        body = xmlrpc.client.dumps((), methodname=method)
        connection.request('POST', '/RPC2', body, {'Content-Type': 'text/xml'})
        response = connection.getresponse()
        raw = response.read()
        assert response.status == 200
        return xmlrpc.client.loads(raw)[0][0]
    finally:
        connection.close()


before = {pid: snap(pid) for pid in EXPECTED}
prod_before = snap(PRODUCTION)
assert prod_before and prod_before['start_ticks'] == '1012537254'
for pid, value in before.items():
    assert pid not in {294214, 56} and value and value['start_ticks'] == EXPECTED[pid]
    assert value['cwd'] == str(CASE)
assert before[512541]['cmdline'] == ['/usr/bin/supervisord', '-c', str(CASE / 'supervisord.conf')]
assert before[512556]['cmdline'] == ['/usr/bin/python3', '-c', 'import time;time.sleep(600)']
receipt = {'scope': 'RECORDED_FAILED_FIXTURE_CLEANUP_ONLY', 'before': before,
           'production_before': prod_before, 'actions': []}
(ROOT / 'cleanup-v2-before.json').write_text(json.dumps(receipt, sort_keys=True, indent=2))
receipt['rpc_status_before'] = rpc('supervisor.getAllProcessInfo')
# Shutdown only this new manager through its independently verified private Unix socket.
receipt['actions'].append({'kind': 'ISOLATED_MANAGER_RPC_SHUTDOWN', 'method': 'supervisor.shutdown',
                           'unix_socket': str(CASE / 'rpc.sock'), 'expected_manager_pid': 512541})
receipt['shutdown_result'] = rpc('supervisor.shutdown')
deadline = time.monotonic() + 8
while time.monotonic() < deadline and any(snap(pid) for pid in (512541, 512553, 512555)):
    time.sleep(.1)
# The failed child left its own new-session worker orphaned. This is a named,
# immutable fixture PID/start identity, not a numeric production-signal fallback.
worker = snap(512556)
if worker:
    assert worker['start_ticks'] == EXPECTED[512556] and worker['cwd'] == str(CASE)
    assert worker['cmdline'] == ['/usr/bin/python3', '-c', 'import time;time.sleep(600)']
    again = snap(512556)
    assert again == worker
    receipt['actions'].append({'kind': 'RECORDED_FIXTURE_WORKER_TERM', 'pid': 512556,
                               'start_ticks': EXPECTED[512556], 'production_fallback': False})
    os.kill(512556, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and snap(512556):
        time.sleep(.1)
receipt['after'] = {pid: snap(pid) for pid in EXPECTED}
receipt['remaining'] = [pid for pid, value in receipt['after'].items()
                        if value and value['state'] != 'Z']
receipt['production_after'] = snap(PRODUCTION)
receipt['production_identity_unchanged'] = receipt['production_after'] is not None and all(
    receipt['production_after'][key] == prod_before[key] for key in ('pid','start_ticks','cwd','cmdline'))
receipt['production_mutations'] = False
receipt['state'] = 'CLEANUP_PASS' if not receipt['remaining'] and receipt['production_identity_unchanged'] else 'FAILED'
(ROOT / 'cleanup-v2-receipt.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
print(json.dumps({'state': receipt['state'], 'remaining': receipt['remaining'],
    'production_identity_unchanged': receipt['production_identity_unchanged'],
    'rpc_methods': receipt.get('rpc_methods'), 'rpc_status_before': receipt['rpc_status_before']}))
