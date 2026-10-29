"""Offline extraction tests: no MinIO, SSH, Git, network, or repository writes."""
import ast
import hashlib
import io
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path('E:/QTrade-temp/minio_s3_fixture_check_20261029.py')
EXPECTED = '97a93ec0b0f2bdde9e89e4538ffe5ec1d3857a6d586292ad2613c035cd09c453'


class Child:
    def __init__(self, *, raw=b'ok', rc=0, timeout=False, resist=False):
        self.raw, self.returncode = raw, None if timeout else rc
        self.timeout, self.resist = timeout, resist
        self.stdout = io.BytesIO()
        self.events = []

    def communicate(self, *, timeout):
        self.events.append(('communicate', timeout))
        if self.timeout:
            raise subprocess.TimeoutExpired('offline-git', timeout)
        return self.raw, None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.events.append(('terminate',))

    def wait(self, *, timeout):
        self.events.append(('wait', timeout))
        if self.resist:
            self.resist = False
            raise subprocess.TimeoutExpired('offline-git', timeout)
        self.returncode = -15

    def kill(self):
        self.events.append(('kill',))


def extracted(child):
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == EXPECTED
    tree = ast.parse(SOURCE.read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'git_read')
    calls = []

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return child

    namespace = {
        'BASE': Path('/fixed/base'), 'run': Path('/isolated/run'),
        'subprocess': SimpleNamespace(Popen=popen, DEVNULL=-3, PIPE=-1,
                                      TimeoutExpired=subprocess.TimeoutExpired),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace['git_read'], calls


def test_exact_command_environment_and_finished_child():
    child = Child()
    read, calls = extracted(child)
    assert read(['show', 'abe2986cd6e3f3faeb4984454498e4c67f1646d3:quality.json']) == b'ok'
    command, kwargs = calls[0]
    assert command == ['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c',
                       'core.useReplaceRefs=false', '--git-dir=/fixed/base/publication/.git',
                       'show', 'abe2986cd6e3f3faeb4984454498e4c67f1646d3:quality.json']
    assert kwargs['env'] == {'PATH': '/usr/bin:/bin', 'HOME': '/isolated/run/home',
                            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                            'GIT_NO_REPLACE_OBJECTS': '1'}
    assert kwargs['start_new_session'] is False
    assert child.events == [('communicate', 30)]
    assert child.stdout.closed


@pytest.mark.parametrize('command', ['push', 'fetch', 'config', 'checkout'])
def test_mutating_commands_rejected_before_popen(command):
    read, calls = extracted(Child())
    with pytest.raises(AssertionError):
        read([command])
    assert calls == []


@pytest.mark.parametrize('resist', [False, True])
def test_timeout_owned_child_cleanup_and_original_failure(resist):
    child = Child(timeout=True, resist=resist)
    read, _ = extracted(child)
    with pytest.raises(subprocess.TimeoutExpired):
        read(['ls-tree', '-r', '-z', 'fixed-commit'])
    expected = [('communicate', 30), ('terminate',), ('wait', 2)]
    if resist:
        expected += [('kill',), ('wait', 2)]
    assert child.events == expected
    assert child.stdout.closed


@pytest.mark.parametrize('child', [Child(rc=1), Child(raw=b'12345')])
def test_nonzero_or_over_limit_is_rejected_after_pipe_close(child):
    read, _ = extracted(child)
    with pytest.raises(AssertionError):
        read(['show', 'fixed-commit:file'], maximum=4)
    assert child.stdout.closed


def test_all_metadata_and_listing_use_literal_frozen_commit():
    tree = ast.parse(SOURCE.read_text())
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'SOURCE_GIT' for t in n.targets))
    assert ast.literal_eval(assignment.value) == 'abe2986cd6e3f3faeb4984454498e4c67f1646d3'
    full = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'full_load')
    commands = [n.args[0] for n in ast.walk(full) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == 'git_read']
    assert len(commands) == 2
    assert all(any(isinstance(n, ast.Name) and n.id == 'SOURCE_GIT' for n in ast.walk(c)) for c in commands)
    assert not any(isinstance(n, ast.Constant) and n.value == 'HEAD' for n in ast.walk(full))


def test_open_readonly_gap_missing_no_lazy_fetch_is_recorded():
    read, calls = extracted(Child())
    read(['ls-tree', '-r', '-z', 'fixed-commit'])
    # This is a documented limitation, not an assertion that the source was mutated.
    assert 'GIT_NO_LAZY_FETCH' not in calls[0][1]['env']
