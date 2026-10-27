"""Exercise real local process trees; no Git remote, shell or credentials are used."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import publish_research_snapshot as publisher

PROGRAM = r'''
import json, os, signal, subprocess, sys, time
from pathlib import Path
mode, report = sys.argv[1], Path(sys.argv[2])
print('FAKE_GIT_PRIVATE_OUTPUT', flush=True)
print('FAKE_GIT_PRIVATE_ERROR', file=sys.stderr, flush=True)
if mode in ('success', 'nonzero'):
    sys.exit(0 if mode == 'success' else 7)
ready = report.with_suffix('.ready')
child_code = ''' + repr('''
import os, signal, sys, time
from pathlib import Path
if sys.argv[2] == 'ignore':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).write_text(str(os.getpid()))
while True:
    time.sleep(0.05)
''') + r'''
child = subprocess.Popen([sys.executable, '-c', child_code, str(ready),
                          'ignore' if mode == 'ignore' else 'normal'])
while not ready.exists():
    time.sleep(0.01)
report.write_text(json.dumps({'parent': os.getpid(), 'child': child.pid}))
if mode == 'early_success':
    sys.exit(0)
def terminate(signum, frame):
    # A normal child gets reaped before this fake Git parent exits. A deliberately
    # TERM-ignoring child forces the caller to escalate against the entire group.
    child.wait()
    sys.exit(128 + signum)
signal.signal(signal.SIGTERM, terminate)
while True:
    time.sleep(0.05)
'''


def live(pid: int) -> bool:
    """Linux zombies have exited and cannot retain channels or publish anything."""
    stat = Path(f'/proc/{pid}/stat')
    if stat.exists():
        return stat.read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.fixture
def fake_git(tmp_path, monkeypatch):
    program = tmp_path/'fake_git.py'
    program.write_text(PROGRAM)
    original = subprocess.Popen
    processes = []

    def install(mode):
        report = tmp_path/(mode+'.json')

        def spawn(command, **kwargs):
            assert command == ['git', '-C', str(tmp_path), 'push', 'origin',
                               'HEAD:refs/heads/main']
            assert kwargs == {'start_new_session': True, 'stdin': subprocess.DEVNULL,
                              'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
            process = original([sys.executable, str(program), mode, str(report)], **kwargs)
            processes.append(process)
            return process

        monkeypatch.setattr(publisher.subprocess, 'Popen', spawn)
        return report

    yield install
    # A failed assertion must not leave the test's artificial descendants behind.
    for process in processes:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=2)


@pytest.mark.skipif(os.name != 'posix', reason='production process groups require POSIX')
@pytest.mark.parametrize('mode', ['timeout', 'ignore', 'early_success'])
def test_git_timeout_cleans_actual_inherited_channel_descendants(tmp_path, fake_git, mode, capfd):
    report = fake_git(mode)
    started = time.monotonic()
    arguments = ['push', 'origin', 'HEAD:refs/heads/main']
    if mode == 'early_success':
        publisher.run_publication_git(tmp_path, arguments, timeout=1)
    else:
        with pytest.raises(subprocess.TimeoutExpired):
            publisher.run_publication_git(tmp_path, arguments, timeout=1)
    elapsed = time.monotonic() - started
    assert elapsed < 5
    if mode == 'ignore':
        assert elapsed >= 2.5  # The TERM grace was allowed before group KILL.
    pids = json.loads(report.read_text())
    assert not live(pids['parent'])
    assert not live(pids['child'])
    if mode == 'timeout':
        # The cooperative fake parent reaps its grandchild, so its PID truly vanishes.
        with pytest.raises(ProcessLookupError):
            os.kill(pids['child'], 0)
    assert capfd.readouterr() == ('', '')


@pytest.mark.skipif(os.name != 'posix', reason='production process groups require POSIX')
@pytest.mark.parametrize('mode', ['success', 'nonzero'])
def test_git_success_and_nonzero_keep_existing_check_semantics(tmp_path, fake_git, mode, capfd):
    fake_git(mode)
    if mode == 'success':
        assert publisher.run_publication_git(
            tmp_path, ['push', 'origin', 'HEAD:refs/heads/main'], timeout=1,
        ) is None
    else:
        with pytest.raises(subprocess.CalledProcessError) as caught:
            publisher.run_publication_git(
                tmp_path, ['push', 'origin', 'HEAD:refs/heads/main'], timeout=1,
            )
        assert caught.value.returncode == 7
        assert caught.value.stdout is None and caught.value.stderr is None
    assert capfd.readouterr() == ('', '')


@pytest.mark.skipif(os.name == 'posix', reason='POSIX is the supported production platform')
def test_windows_fails_before_launching_an_unowned_process(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('unsupported platform must fail before process creation')

    monkeypatch.setattr(publisher.subprocess, 'Popen', forbidden)
    with pytest.raises(RuntimeError, match='PROCESS_GROUP_REQUIRES_POSIX'):
        publisher.run_publication_git(tmp_path, ['push', 'origin', 'HEAD:refs/heads/main'])


@pytest.mark.skipif(os.name != 'posix', reason='production process groups require POSIX')
@pytest.mark.parametrize('timeout', [0, -1, 91])
def test_command_timeout_cannot_exceed_current_ninety_second_bound(tmp_path, monkeypatch, timeout):
    def forbidden(*args, **kwargs):
        pytest.fail('invalid resource bound must fail before process creation')

    monkeypatch.setattr(publisher.subprocess, 'Popen', forbidden)
    with pytest.raises(ValueError, match='TIMEOUT_BOUND'):
        publisher.run_publication_git(tmp_path, ['push', 'origin', 'HEAD:refs/heads/main'],
                                      timeout=timeout)


@pytest.mark.parametrize('exception', [subprocess.TimeoutExpired(['git'], 90), KeyboardInterrupt()])
def test_timeout_or_interruption_is_preserved_after_owned_group_cleanup(
    tmp_path, monkeypatch, exception,
):
    signals = []

    class Process:
        pid = 123456
        calls = 0

        def wait(self, timeout):
            self.calls += 1
            if self.calls == 1:
                assert timeout == 90
                raise exception
            assert timeout == 2
            return -signal.SIGTERM

        def poll(self):
            return -signal.SIGTERM

    process = Process()

    def killpg(pid, sig):
        assert pid == process.pid
        signals.append(sig)
        if sig == 0:
            raise ProcessLookupError

    monkeypatch.setattr(publisher, 'os', SimpleNamespace(name='posix', killpg=killpg))
    monkeypatch.setattr(publisher.subprocess, 'Popen', lambda *args, **kwargs: process)
    with pytest.raises(type(exception)) as caught:
        publisher.run_publication_git(tmp_path, ['push', 'origin', 'HEAD:refs/heads/main'])
    assert caught.value is exception
    assert signals == [signal.SIGTERM, 0]
    assert process.calls == 2
