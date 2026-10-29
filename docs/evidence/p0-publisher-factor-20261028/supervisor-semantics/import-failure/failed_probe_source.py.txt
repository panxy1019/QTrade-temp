"""Run only from a fresh /root/qtrade-local/operator-evidence/svsem-* root."""
import hashlib
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import time
import traceback
import xmlrpc.client
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = Path('/root/qtrade-local/operator-evidence')
EXPECTED_BINARY = 'c0e12d2e498d00a2285161372cbb0a46b795b94e9a42511351eba7980f408569'
BINARY = Path('/usr/bin/supervisord')
PRODUCTION_PID = 294214
MANAGERS = []
REGISTRY = {}
SIGNALS = []
assert ROOT.parent == BASE and ROOT.name.startswith('svsem-') and not ROOT.is_symlink()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(pid):
    path = Path('/proc') / str(pid)
    try:
        rest = (path / 'stat').read_text().rsplit(')', 1)[1].split()
        return {'pid': pid, 'start_ticks': rest[19], 'state': rest[0], 'ppid': int(rest[1]),
                'cwd': os.readlink(path / 'cwd'),
                'cmdline_sha256': hashlib.sha256((path / 'cmdline').read_bytes()).hexdigest()}
    except FileNotFoundError:
        return None


def emit(event, **fields):
    value = {'at': datetime.now(timezone.utc).isoformat(), 'monotonic': time.monotonic(),
             'event': event, **fields}
    with (ROOT / 'operator.events.jsonl').open('a') as handle:
        handle.write(json.dumps(value, sort_keys=True) + '\n')
    print(json.dumps(value, sort_keys=True), flush=True)


def register(pid, role):
    assert pid not in {PRODUCTION_PID, 56, os.getpid()}
    state = snapshot(pid)
    assert state and Path(state['cwd']).is_relative_to(ROOT), (role, pid)
    if pid in REGISTRY:
        assert REGISTRY[pid]['identity']['start_ticks'] == state['start_ticks']
    else:
        REGISTRY[pid] = {'role': role, 'identity': state}
    return state


def send_registered(pid, sig, role):
    assert pid in REGISTRY and pid not in {PRODUCTION_PID, 56, os.getpid()}
    expected = REGISTRY[pid]['identity']
    current = snapshot(pid)
    assert current and current['start_ticks'] == expected['start_ticks']
    assert current['cmdline_sha256'] == expected['cmdline_sha256']
    assert Path(current['cwd']).is_relative_to(ROOT)
    fd = os.pidfd_open(pid)
    try:
        again = snapshot(pid)
        assert again and again['start_ticks'] == expected['start_ticks']
        intent = {'pid': pid, 'start_ticks': expected['start_ticks'], 'signal': sig,
                  'role': role, 'method': 'pidfd_send_signal', 'at_monotonic': time.monotonic()}
        emit('SIGNAL_INTENT', **intent)
        signal.pidfd_send_signal(fd, sig)
        SIGNALS.append(intent)
        emit('SIGNAL_DELIVERED', **intent)
    finally:
        os.close(fd)


def events(case):
    path = case / 'child.events.jsonl'
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def record_children(case):
    for row in events(case):
        for field in ('pid', 'worker_pid'):
            pid = row.get(field)
            if pid and snapshot(pid):
                register(pid, 'fixture-' + field)


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('isolated.invalid', timeout=2)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(2)
        self.sock.connect(self.path)


def status(case):
    connection = UnixConnection(case / 'rpc.sock')
    try:
        body = xmlrpc.client.dumps((), methodname='supervisor.getAllProcessInfo')
        connection.request('POST', '/RPC2', body, {'Content-Type': 'text/xml'})
        response = connection.getresponse()
        raw = response.read()
        assert response.status == 200
        result = xmlrpc.client.loads(raw)[0][0]
        with (case / 'rpc-status.jsonl').open('a') as handle:
            handle.write(json.dumps({'at': time.monotonic(), 'response': result}) + '\n')
        return {row['name']: row for row in result}
    finally:
        connection.close()


def wait_status(case, predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            states = status(case)
            if predicate(states):
                return states
        except (OSError, xmlrpc.client.Error):
            pass
        time.sleep(.2)
    raise AssertionError('ISOLATED_NATIVE_RUNNING_OBSERVATION_TIMEOUT')


def manager_sockets(pid, expected_socket):
    inodes = set()
    for fd in (Path('/proc') / str(pid) / 'fd').iterdir():
        try:
            name = os.readlink(fd)
        except FileNotFoundError:
            continue
        if name.startswith('socket:['):
            inodes.add(name[8:-1])
    internet = []
    for name in ('tcp', 'tcp6', 'udp', 'udp6'):
        for line in (Path('/proc/net') / name).read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) > 9 and fields[9] in inodes:
                internet.append(name)
    unix = []
    for line in Path('/proc/net/unix').read_text().splitlines()[1:]:
        fields = line.split()
        if len(fields) > 6 and fields[6] in inodes:
            unix.append(fields[7] if len(fields) > 7 else '')
    assert not internet and str(expected_socket) in unix
    assert all(not item or item == str(expected_socket) for item in unix)
    return {'internet_socket_count': len(internet), 'unix_paths': unix}


CHILD = r'''
import json,os,signal,subprocess,sys,threading,time
from pathlib import Path
root=Path(sys.argv[1]); name=sys.argv[2]; release=sys.argv[3]; scenario=sys.argv[4]
stop=threading.Event()
def handle(signum,frame):
    stop.set()
signal.signal(signal.SIGTERM,handle)
def tick(pid):
    return Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()[19]
def record(event,**values):
    value={'event':event,'pid':os.getpid(),'ppid':os.getppid(),'start_ticks':tick(os.getpid()),
           'name':name,'release':release,'monotonic':time.monotonic(),**values}
    with (root/'child.events.jsonl').open('a') as stream:
        stream.write(json.dumps(value,sort_keys=True)+'\n');stream.flush();os.fsync(stream.fileno())
    print(json.dumps(value,sort_keys=True),flush=True)
counter=root/(name+'.counter')
generation=int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(generation))
record('START',generation=generation)
worker=None
try:
    if name!='controlled' or generation>=2:
        if name=='controlled' and generation>2: record('UNEXPECTED_EXTRA_GENERATION',generation=generation)
        while not stop.wait(.1): pass
    else:
        worker=subprocess.Popen(['/usr/bin/python3','-c','import time;time.sleep(600)'],
                                cwd=root,start_new_session=True)
        record('OWNED_WORKER_START',worker_pid=worker.pid,worker_start_ticks=tick(worker.pid),
               worker_session=os.getsid(worker.pid))
        if scenario=='normal':
            deadline=time.monotonic()+2
            while time.monotonic()<deadline: time.sleep(.05)
            record('NORMAL_PHASE_COMPLETE')
        else:
            while not stop.wait(.05): pass
            record('TERM_DRAIN_BEGIN')
            deadline=time.monotonic()+112.0
            while time.monotonic()<deadline: time.sleep(.1)
            record('TERM_DRAIN_COMPLETE')
finally:
    if worker is not None and worker.poll() is None:
        fd=os.pidfd_open(worker.pid)
        try: signal.pidfd_send_signal(fd,signal.SIGTERM)
        finally: os.close(fd)
        code=worker.wait(timeout=5)
        record('OWNED_WORKER_CLEANED',worker_pid=worker.pid,worker_returncode=code)
record('NATURAL_EXIT_ZERO',generation=generation)
sys.exit(0)
'''


def write_wrapper(case, release):
    content = '#!/bin/sh\nexec /usr/bin/python3 ' + str(ROOT / 'child.py') + ' ' + str(case) \
        + ' controlled ' + release + ' ' + case.name + '\n'
    saved = case / ('wrapper-' + release + '.sh')
    saved.write_text(content)
    candidate = case / 'wrapper.candidate'
    with candidate.open('w') as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    candidate.chmod(0o700)
    os.replace(candidate, case / 'wrapper')
    fd = os.open(case, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    emit('ISOLATED_WRAPPER_INSTALLED', case=case.name, release=release,
         sha256=sha(case / 'wrapper'))


def launch(case):
    case.mkdir()
    assert len(str(case / 'rpc.sock').encode()) < 104
    write_wrapper(case, 'v1')
    config = ('[unix_http_server]\nfile=' + str(case / 'rpc.sock') + '\nchmod=0700\n'
        '[supervisord]\nnodaemon=true\nloglevel=debug\nlogfile=' + str(case / 'manager.log')
        + '\nlogfile_maxbytes=1048576\nlogfile_backups=1\npidfile=' + str(case / 'manager.pid')
        + '\nchildlogdir=' + str(case) + '\n'
        '[rpcinterface:supervisor]\nsupervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface\n'
        '[supervisorctl]\nserverurl=unix://' + str(case / 'rpc.sock') + '\n')
    for name in ('controlled', 'sentinel-a', 'sentinel-b'):
        command = str(case / 'wrapper') if name == 'controlled' else (
            '/usr/bin/python3 ' + str(ROOT / 'child.py') + ' ' + str(case) + ' ' + name + ' fixed sentinel')
        config += ('[program:' + name + ']\ncommand=' + command + '\ndirectory=' + str(case)
            + '\nautostart=true\nautorestart=true\nstartsecs=1\nstartretries=0\nexitcodes=0\n'
            'stopsignal=TERM\nstopwaitsecs=110\nstopasgroup=true\nkillasgroup=true\n'
            'stdout_logfile=' + str(case / (name + '.stdout.log')) + '\nstderr_logfile='
            + str(case / (name + '.stderr.log')) + '\n')
    (case / 'supervisord.conf').write_text(config)
    output = (case / 'manager.stdout.log').open('wb')
    process = subprocess.Popen([str(BINARY), '-c', str(case / 'supervisord.conf')],
        cwd=case, stdout=output, stderr=subprocess.STDOUT,
        env={'PATH': '/usr/bin:/bin', 'HOME': str(case), 'LIVE_TRADING_ENABLED': 'false'})
    MANAGERS.append((case, process, output))
    register(process.pid, 'isolated-manager')
    native = wait_status(case, lambda state: len(state) == 3 and all(
        row['statename'].upper() == 'RUNNING' and row['pid'] > 0 for row in state.values()), 15)
    for row in native.values():
        identity = register(row['pid'], row['name'])
        assert identity['ppid'] == process.pid
    sockets = manager_sockets(process.pid, case / 'rpc.sock')
    emit('ISOLATED_MANAGER_RUNNING', case=case.name, manager_pid=process.pid,
         native=native, sockets=sockets)
    return process, native


def run_case(name):
    case = ROOT / name
    manager, initial = launch(case)
    old = initial['controlled']['pid']
    sentinels = {key: register(initial[key]['pid'], key) for key in ('sentinel-a', 'sentinel-b')}
    if name == 'term':
        write_wrapper(case, 'v2')
        send_registered(old, signal.SIGTERM, 'single-external-controlled-main-term')
        signal_at = SIGNALS[-1]['at_monotonic']
        deadline, next_progress, checked_110 = time.monotonic() + 130, time.monotonic(), False
        while time.monotonic() < deadline:
            elapsed = time.monotonic() - signal_at
            current = status(case)
            if elapsed >= 110.5 and not checked_110:
                assert current['controlled']['pid'] == old and snapshot(old)
                assert current['controlled']['statename'].upper() == 'RUNNING'
                checked_110 = True
                emit('OLD_MAIN_STILL_RUNNING_BEYOND_110_SECONDS', case=name, elapsed=elapsed, pid=old)
            if current['controlled']['pid'] != old and current['controlled']['statename'].upper() == 'RUNNING':
                assert checked_110
                break
            if time.monotonic() >= next_progress:
                emit('CONTROLLED_DRAIN_PROGRESS', case=name, elapsed=elapsed, old_pid=old)
                next_progress = time.monotonic() + 20
            time.sleep(.25)
        else:
            raise AssertionError('ISOLATED_DRAIN_RESTART_TIMEOUT')
    final = wait_status(case, lambda state: state['controlled']['pid'] != old
                        and state['controlled']['pid'] > 0
                        and state['controlled']['statename'].upper() == 'RUNNING', 15)
    new = final['controlled']['pid']
    register(new, 'controlled-new-generation')
    time.sleep(2)
    stable = status(case)
    assert stable['controlled']['pid'] == new and stable['controlled']['statename'].upper() == 'RUNNING'
    assert snapshot(old) is None
    for key, identity in sentinels.items():
        assert stable[key]['pid'] == identity['pid'] and stable[key]['statename'].upper() == 'RUNNING'
        assert snapshot(identity['pid'])['start_ticks'] == identity['start_ticks']
    rows = events(case)
    starts = [row for row in rows if row['event'] == 'START' and row['name'] == 'controlled']
    assert len(starts) == 2 and [row['generation'] for row in starts] == [1, 2]
    assert starts[0]['pid'] == old and starts[1]['pid'] == new
    assert starts[1]['release'] == ('v2' if name == 'term' else 'v1')
    exits = [row for row in rows if row['event'] == 'NATURAL_EXIT_ZERO' and row['pid'] == old]
    cleaned = [row for row in rows if row['event'] == 'OWNED_WORKER_CLEANED' and row['pid'] == old]
    assert len(exits) == len(cleaned) == 1 and cleaned[0]['worker_returncode'] == -signal.SIGTERM
    assert snapshot(cleaned[0]['worker_pid']) is None
    if name == 'term':
        begin = next(row for row in rows if row['event'] == 'TERM_DRAIN_BEGIN' and row['pid'] == old)
        complete = next(row for row in rows if row['event'] == 'TERM_DRAIN_COMPLETE' and row['pid'] == old)
        assert complete['monotonic'] - begin['monotonic'] >= 112
        assert sum(row['pid'] == old and row['signal'] == signal.SIGTERM for row in SIGNALS) == 1
    record_children(case)
    result = {'case': name, 'state': 'PASS', 'old_pid': old, 'new_pid': new,
        'normal_exit_zero_event': exits[0], 'owned_worker_cleanup': cleaned[0],
        'exactly_two_generations': True, 'native_new_running': stable,
        'sentinel_identities_unchanged': True, 'wrapper_release_on_restart': starts[1]['release'],
        'external_main_term_count': sum(row['pid'] == old and row['signal'] == signal.SIGTERM for row in SIGNALS),
        'stopProcess_invocations': 0, 'stopwaitsecs_config': 110}
    if name == 'term':
        result['drain_seconds'] = complete['monotonic'] - begin['monotonic']
        result['alive_and_native_running_after_110_seconds'] = True
    (case / 'result.json').write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    emit('CASE_PASS', case=name, old_pid=old, new_pid=new)
    return result


def cleanup():
    for case, manager, output in reversed(MANAGERS):
        record_children(case)
        if manager.poll() is None:
            send_registered(manager.pid, signal.SIGTERM, 'isolated-manager-cleanup')
            try:
                manager.wait(timeout=8)
            except subprocess.TimeoutExpired:
                send_registered(manager.pid, signal.SIGKILL, 'isolated-manager-failure-cleanup')
                manager.wait(timeout=5)
        output.close()
        record_children(case)
    for pid, value in list(REGISTRY.items()):
        current = snapshot(pid)
        if current and current['state'] != 'Z' and current['start_ticks'] == value['identity']['start_ticks']:
            send_registered(pid, signal.SIGKILL, 'registered-fixture-only-failure-cleanup')
    (ROOT / 'cleanup.json').write_text(json.dumps({'registered': REGISTRY, 'signals': SIGNALS,
        'remaining_non_zombie_fixture_pids': [pid for pid, row in REGISTRY.items()
            if snapshot(pid) and snapshot(pid)['state'] != 'Z'
            and snapshot(pid)['start_ticks'] == row['identity']['start_ticks']]}, sort_keys=True, indent=2))


summary = {'state': 'FAILED', 'binary_sha256': sha(BINARY), 'production_before': snapshot(PRODUCTION_PID),
           'isolation_root': str(ROOT), 'production_mutations': False, 'production_ctl_invocations': 0}
assert summary['binary_sha256'] == EXPECTED_BINARY and summary['production_before']
assert hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal')
(ROOT / 'child.py').write_text(CHILD)
try:
    emit('ISOLATION_VERIFIED', root=str(ROOT), binary_sha256=summary['binary_sha256'])
    summary['cases'] = [run_case('normal'), run_case('term')]
    summary['state'] = 'PASS'
except BaseException as exc:
    summary['error_type'] = type(exc).__name__
    summary['error_code'] = str(exc)
    (ROOT / 'failure-traceback.txt').write_text(traceback.format_exc())
    emit('ISOLATED_TEST_FAILED', error_type=type(exc).__name__, error_code=str(exc))
finally:
    cleanup()
    summary['production_after'] = snapshot(PRODUCTION_PID)
    summary['production_identity_unchanged'] = all(
        summary['production_before'][key] == summary['production_after'][key]
        for key in ('pid', 'start_ticks', 'ppid', 'cwd', 'cmdline_sha256')) if summary['production_after'] else False
    summary['binary_after_sha256'] = sha(BINARY)
    summary['cleanup'] = json.loads((ROOT / 'cleanup.json').read_text())
    if not summary['production_identity_unchanged'] or summary['cleanup']['remaining_non_zombie_fixture_pids']:
        summary['state'] = 'FAILED'
    if any(row['signal'] == signal.SIGKILL for row in SIGNALS):
        summary['state'] = 'FAILED'
    emit('FINAL', state=summary['state'], production_identity_unchanged=summary['production_identity_unchanged'])
    summary['artifact_sha256'] = {path.relative_to(ROOT).as_posix(): sha(path)
        for path in ROOT.rglob('*') if path.is_file() and path.name != 'summary.json'}
    (ROOT / 'summary.json').write_text(json.dumps(summary, sort_keys=True, indent=2) + '\n')
sys.exit(0 if summary['state'] == 'PASS' else 1)
