"""Linux overlay-only real file leaves; never load a production manifest/transport."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import platform
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True
ROOT = Path(sys.argv[1]).resolve()
assert ROOT.parent == Path('/root/qtrade-local/operator-evidence')
assert ROOT.name.startswith('publisher-leaf-') and ROOT.is_dir() and not ROOT.is_symlink()
filesystem_type = subprocess.check_output(['findmnt', '-T', str(ROOT), '-n', '-o', 'FSTYPE'], text=True).strip()
assert filesystem_type == 'overlay', 'overlay-only fixture required'
SOURCE = ROOT / 'publisher_handover_draft_20261028.py'
spec = importlib.util.spec_from_file_location('fixture_handover_only', SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
production_before = m.PublisherOnlyAdapter.owner(294214)  # hashes/selected LIVE only; readonly /proc.
original_base = str(m.BASE)
m.BASE = ROOT / 'fixture-base'
m.WRAPPER = m.BASE / 'private/run-publisher-workflow.py'
m.EVIDENCE_PARENT = ROOT / 'fixture-evidence'
assert m.BASE.is_relative_to(ROOT) and m.WRAPPER.is_relative_to(ROOT)
assert m.EVIDENCE_PARENT.is_relative_to(ROOT) and str(m.BASE) != original_base
m.BASE.mkdir(mode=0o700)
m.EVIDENCE_PARENT.mkdir(mode=0o700)
events, cases, rpc_calls, children, cleanup = [], [], [], [], []
real_fsync, real_sync_directory, real_replace, real_file_sha = (
    os.fsync, m.fsync_directory, os.replace, m.file_sha)


def record(kind, **fields):
    events.append({'order': len(events), 'kind': kind, **fields})


def tracked_fsync(descriptor):
    path = os.readlink('/proc/self/fd/' + str(descriptor))
    kind = 'directory_fsync' if stat.S_ISDIR(os.fstat(descriptor).st_mode) else 'file_fsync'
    real_fsync(descriptor)
    record(kind, path=path)


def tracked_replace(source, destination):
    assert Path(source).resolve().is_relative_to(ROOT) and Path(destination).resolve().is_relative_to(ROOT)
    record('replace', source=str(source), target=str(destination))
    real_replace(source, destination)


os.fsync = tracked_fsync
os.replace = tracked_replace
owner_source = ROOT / 'fixture_owner.py'
owner_source.write_text("import os,signal,sys,threading\nfrom pathlib import Path\n"
    "stop=threading.Event()\nsignal.signal(signal.SIGTERM,lambda *args:stop.set())\n"
    "Path(sys.argv[1]).write_text(str(os.getpid()))\nstop.wait(180)\n")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture_case(name):
    root = ROOT / name
    root.mkdir(mode=0o700)
    # Each case gets a separate fixed module BASE/WRAPPER and independent source/backup.
    m.BASE = root / 'fixture-base'
    m.WRAPPER = m.BASE / 'private/run-publisher-workflow.py'
    m.EVIDENCE_PARENT = root / 'fixture-evidence'
    m.WRAPPER.parent.mkdir(mode=0o700, parents=True)
    m.EVIDENCE_PARENT.mkdir(mode=0o700)
    revision = 'a' * 40
    old_release = m.BASE / 'releases' / m.OLD_REVISION
    release = m.BASE / 'releases' / revision
    old_release.mkdir(mode=0o700, parents=True)
    release.mkdir(mode=0o700)
    (release / 'fixture_module.py').write_text('FIXTURE_ONLY = True\n')
    attempt = m.EVIDENCE_PARENT / ('publisher-handover-' + name)
    journal = m.JsonPhaseJournal(attempt)
    old_raw = ("import os\nROOT = '" + str(old_release) + "'\n"
               "os.execv('/fixture/python', ['python', '-m', 'scripts.publish_research_snapshot'])\n").encode()
    new_raw = old_raw.replace(str(old_release).encode(), str(release).encode(), 1)
    m.WRAPPER.write_bytes(old_raw)
    m.WRAPPER.chmod(0o700)
    candidate = attempt / 'wrapper.candidate.py'
    candidate.write_bytes(new_raw)
    candidate.chmod(0o600)
    config = root / 'fixture-only.conf'
    config.write_text('[program:FIXTURE_ONLY]\nautorestart=true\n')
    environment = dict(os.environ, LIVE_TRADING_ENABLED='false', PYTHONPATH=str(old_release))
    ready = root / 'owner.ready'
    process = subprocess.Popen([sys.executable, str(owner_source), str(ready)], cwd=root,
                               env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    children.append(process)
    deadline = time.monotonic() + 5
    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.exists() and process.poll() is None
    owner = m.PublisherOnlyAdapter.owner(process.pid)
    assert owner is not None and owner['live_trading_enabled'] is False
    before = {'owners': {'publisher': {'pid': process.pid, 'start_ticks': owner['start_ticks']},
                         'sentinel': {'pid': -1, 'start_ticks': 'SYNTHETIC_ONLY'}},
              'context': {'active': 'FIXTURE_HALTED'},
              'frozen_controls': {'books': {}, 'rolling': 'OFF'}}
    reader = SimpleNamespace(controls=lambda: copy.deepcopy(before['owners']),
                             frozen_controls=lambda: copy.deepcopy(before['frozen_controls']),
                             h=SimpleNamespace(context=lambda: copy.deepcopy(before['context'])))
    def fake_rpc(method, arguments):
        assert method == 'supervisor.getProcessInfo' and arguments == (m.PROGRAM,)
        assert process.poll() is None
        rpc_calls.append({'method': method, 'arguments': list(arguments), 'fixture_pid': process.pid})
        return {'name': m.PROGRAM, 'pid': process.pid, 'state': 20, 'statename': 'RUNNING'}
    manifest = {'fixture_only': True, 'old_revision': m.OLD_REVISION, 'new_revision': revision,
                'old_owner': owner, 'attempt_root': str(attempt), 'wrapper_target': str(m.WRAPPER),
                'wrapper_candidate': str(candidate), 'wrapper_old_sha256': sha(old_raw),
                'wrapper_new_sha256': sha(new_raw), 'release_root': str(release),
                'runtime_files': {'fixture_module.py': real_file_sha(release / 'fixture_module.py')},
                'supervisor_config_files': {str(config): real_file_sha(config)}}
    assert all(Path(manifest[key]).is_relative_to(ROOT) for key in
               ('attempt_root', 'wrapper_target', 'wrapper_candidate', 'release_root'))
    (root / 'fixture-manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
    adapter = m.PublisherOnlyAdapter(manifest, reader, fake_rpc, mutation_stage_approved=True)
    journal.append('PREPARED', {'fixture_only': True, 'before': before})
    return SimpleNamespace(root=root, adapter=adapter, before=before, journal=journal,
                           manifest=manifest, process=process, old=old_raw, new=new_raw,
                           backup=attempt / 'private/wrapper.old.py', candidate=candidate)


def expected_failure(case, label, action):
    try:
        action()
    except (OSError, ValueError) as exc:
        assert m.WRAPPER.read_bytes() == case.old
        assert not any(row['kind'] == 'replace' and row['target'] == str(m.WRAPPER) for row in events)
        cases.append({'name': label, 'state': 'PASS_EXPECTED_REJECTION', 'error_type': type(exc).__name__,
                      'error_code': str(exc) if isinstance(exc, ValueError) else 'INJECTED_DIRECTORY_FAILURE',
                      'wrapper_unchanged': True})
    else:
        raise AssertionError('expected rejection: ' + label)


failure = None
try:
    case = fixture_case('success')
    installed = case.adapter.install_wrapper(case.before, case.backup)
    assert m.WRAPPER.read_bytes() == case.new and case.backup.read_bytes() == case.old
    assert stat.S_IMODE(case.backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(case.backup.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(m.WRAPPER.stat().st_mode) == 0o700
    assert m.WRAPPER.stat().st_uid == case.backup.stat().st_uid == os.getuid()
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in case.journal.root.glob('*.json'))
    assert case.journal.read()[0]['phase'] == 'PREPARED'
    replace_event = next(row for row in events if row['kind'] == 'replace')
    assert any(row['kind'] == 'file_fsync' and row['path'] == str(case.backup)
               and row['order'] < replace_event['order'] for row in events)
    assert any(row['kind'] == 'directory_fsync' and row['path'] == str(case.backup.parent)
               and row['order'] < replace_event['order'] for row in events)
    for ancestor in (case.journal.root, *case.journal.root.parents):
        assert any(row['kind'] == 'directory_fsync' and row['path'] == str(ancestor)
                   and row['order'] < replace_event['order'] for row in events)
    assert any(row['kind'] == 'directory_fsync' and row['path'] == str(m.WRAPPER.parent)
               and row['order'] > replace_event['order'] for row in events)
    try:
        case.journal.append('PREPARED', {})
    except ValueError as exc:
        assert 'PHASE_IMMUTABLE' in str(exc)
    else:
        raise AssertionError('duplicate phase accepted')
    cases.append({'name': 'real_journal_backup_replace', 'state': 'PASS', 'receipt': installed,
                  'all_journal_ancestor_fsyncs_precede_replace': True, 'backup_fsyncs_precede_replace': True,
                  'target_parent_fsync_follows_replace': True, 'exact_bytes': True,
                  'journal_mode': '0600', 'backup_mode': '0600', 'private_directory_mode': '0700'})

    case = fixture_case('candidate-fault')
    case.candidate.write_bytes(case.new + b'# unrelated change\n')
    case.manifest['wrapper_new_sha256'] = real_file_sha(case.candidate)
    expected_failure(case, 'rehashed_unrelated_wrapper_change',
                     lambda: case.adapter.install_wrapper(case.before, case.backup))

    case = fixture_case('backup-fsync-fault')
    def fail_backup_directory(path):
        if path == case.backup.parent and case.backup.exists():
            raise OSError('INJECTED_BACKUP_DIRECTORY_FSYNC_FAILURE')
        real_sync_directory(path)
    m.fsync_directory = fail_backup_directory
    expected_failure(case, 'backup_directory_fsync_failure',
                     lambda: case.adapter.install_wrapper(case.before, case.backup))
    m.fsync_directory = real_sync_directory

    case = fixture_case('written-bytes-fault')
    def wrong_temporary_hash(path):
        if Path(path).name.startswith('.publisher-wrapper-'):
            return '0' * 64
        return real_file_sha(path)
    m.file_sha = wrong_temporary_hash
    expected_failure(case, 'actual_written_hash_mismatch',
                     lambda: case.adapter.install_wrapper(case.before, case.backup))
    m.file_sha = real_file_sha
    assert not list(m.WRAPPER.parent.glob('.publisher-wrapper-*'))

    case = fixture_case('journal-parent-fault')
    fresh = case.journal.root.parent / 'publisher-handover-fsync-reject'
    def fail_parent(path):
        if path == fresh.parent:
            raise OSError('INJECTED_JOURNAL_PARENT_FSYNC_FAILURE')
        real_sync_directory(path)
    m.fsync_directory = fail_parent
    expected_failure(case, 'journal_parent_fsync_failure', lambda: m.JsonPhaseJournal(fresh))
    m.fsync_directory = real_sync_directory
    assert all(row['method'] == 'supervisor.getProcessInfo' for row in rpc_calls)
except BaseException as exc:
    failure = {'error_type': type(exc).__name__, 'message': str(exc)}
finally:
    os.fsync, os.replace, m.fsync_directory, m.file_sha = real_fsync, real_replace, real_sync_directory, real_file_sha
    for process in children:
        try:
            if process.poll() is None:
                process.terminate()  # Only the Popen handle we created; never arbitrary PID/group.
            code = process.wait(timeout=5)
            cleanup.append({'pid': process.pid, 'exit_code': code, 'own_popen_only': True, 'forced_kill': False})
            if code != 0:
                failure = failure or {'error_type': 'FixtureCleanupNonzero', 'message': str(code)}
        except BaseException as exc:
            cleanup.append({'pid': process.pid, 'own_popen_only': True, 'error_type': type(exc).__name__,
                            'forced_kill': False})
            failure = failure or {'error_type': type(exc).__name__, 'message': 'Fixture cleanup failed'}
    production_after = m.PublisherOnlyAdapter.owner(294214)
    if production_before != production_after:
        failure = failure or {'error_type': 'ProductionIdentityChanged', 'message': 'readonly identity comparison'}
    (ROOT / 'fsync-events.json').write_text(json.dumps(events, sort_keys=True, indent=2) + '\n')
    (ROOT / 'fake-rpc-calls.json').write_text(json.dumps(rpc_calls, sort_keys=True, indent=2) + '\n')
    summary = {'state': 'PASS' if failure is None else 'FAIL', 'fixture_only': True,
               'production_manifest_loaded': False, 'production_mutation': False, 'actual_supervisor_rpc': False,
               'source_sha256': real_file_sha(SOURCE), 'testcase_sha256': real_file_sha(Path(__file__)),
               'runtime': {'python': sys.version, 'executable': sys.executable, 'system': platform.system(),
                           'kernel': platform.release(), 'machine': platform.machine(),
                           'fixture_filesystem': filesystem_type,
                           'binary_readonly_sha256': real_file_sha(Path('/usr/bin/supervisord'))},
               'production_before': production_before, 'production_after': production_after,
               'production_identity_equal': production_before == production_after,
               'cases': cases, 'cleanup': cleanup, 'failure': failure,
               'remaining_fixture_pids': [p.pid for p in children if p.poll() is None],
               'semantic_window_signal_rpcs': 0, 'process_groups_signaled': 0, 'sigkill_count': 0}
    summary['artifacts'] = {path.relative_to(ROOT).as_posix(): {'sha256': real_file_sha(path), 'bytes': path.stat().st_size}
                            for path in sorted(ROOT.rglob('*')) if path.is_file()
                            and path.name != 'summary.json' and '__pycache__' not in path.parts}
    (ROOT / 'summary.json').write_text(json.dumps(summary, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'state': summary['state'], 'cases': len(cases), 'cleanup': cleanup,
                      'remaining_fixture_pids': summary['remaining_fixture_pids'],
                      'production_identity_equal': summary['production_identity_equal']}, sort_keys=True))
sys.exit(0 if failure is None else 1)
