"""Local real Git proofs; these tests never contact a network destination."""
from __future__ import annotations

import json
import os
import subprocess
from contextlib import nullcontext
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.bootstrap_public_aliases import ALIAS, INDEX, REMOTE, bootstrap


@pytest.fixture(autouse=True)
def windows_report_writer(monkeypatch):
    if os.name == 'nt':
        monkeypatch.setattr('scripts.bootstrap_public_aliases.write_json_atomic',
                            lambda path, value: path.write_text(json.dumps(value)))


def git(root: Path, args: list[str], *, data: bytes | None = None,
        index: Path | None = None, timeout: int = 90) -> bytes:
    env = os.environ.copy()
    if index is not None:
        env['GIT_INDEX_FILE'] = str(index)
    result = subprocess.run(['git', '-C', str(root), *args], input=data, capture_output=True,
                            env=env, timeout=min(timeout, 10), check=False)
    if result.returncode:
        raise RuntimeError('LOCAL_GIT_FAILED')
    return result.stdout


def seed(base: Path, publication: Path, *, seed_existing: bool) -> list[str]:
    assert seed_existing
    doc = json.loads((publication/INDEX).read_bytes())
    names = [INDEX]
    for entry in doc['entries']:
        for ref in entry['shards']:
            source = publication/'unified-market/objects'/(ref['sha256']+'.json')
            if sha256(source.read_bytes()).hexdigest() != ref['sha256']:
                raise ValueError('TEST_SOURCE_CHANGED')
            alias = publication/ALIAS/'day'/('D'+entry['symbol']+'.json')
            alias.parent.mkdir(parents=True, exist_ok=True)
            os.link(source, alias)
            names.append(alias.relative_to(publication).as_posix())
    return names


@pytest.fixture
def repos(tmp_path):
    origin = tmp_path/'origin.git'
    publication = tmp_path/'publication'
    origin.mkdir()
    publication.mkdir()
    git(origin, ['init', '--bare', '--initial-branch=main'])
    git(publication, ['init', '--initial-branch=main'])
    git(publication, ['config', 'user.name', 'Local Test'])
    git(publication, ['config', 'user.email', 'local@example.invalid'])
    git(publication, ['config', 'core.autocrlf', 'false'])
    git(publication, ['remote', 'add', 'origin', str(origin)])

    def frame(value):
        raw = json.dumps({'bars': [value], 'clock': 'retained-'+str(value)}).encode()
        digest = sha256(raw).hexdigest()
        obj = publication/'unified-market/objects'/(digest+'.json')
        obj.parent.mkdir(parents=True, exist_ok=True)
        obj.write_bytes(raw)
        (publication/INDEX).write_text(json.dumps({'clock': 'unchanged-'+str(value),
            'entries': [{'symbol': 'SH600000', 'frequency': 'day',
                         'shards': [{'sha256': digest, 'bytes': len(raw)}]}]}))
        git(publication, ['add', '.'])
        git(publication, ['commit', '-m', 'frame '+str(value)])
        return git(publication, ['rev-parse', 'HEAD']).decode().strip()

    remote = frame(1)
    git(publication, ['push', 'origin', 'main'])
    tip = frame(2)
    (publication/'operator-notes.txt').write_bytes(b'preserve untracked')
    report = tmp_path/'report.json'
    return publication, origin, remote, tip, report


def run(repos, **options):
    publication, origin, _, _, report = repos
    return bootstrap(publication.parent, publication, report, expected_remote=str(origin),
                     _run=git, _stopped=lambda: None, _seed=seed,
                     _validate=lambda value: value, **options)


def test_default_plan_does_not_change_refs_or_worktree(repos):
    publication, origin, remote, tip, _ = repos
    result = run(repos)
    assert result['state'] == 'PLAN'
    assert git(publication, ['rev-parse', 'HEAD']).decode().strip() == tip
    assert git(origin, ['rev-parse', 'main']).decode().strip() == remote
    assert not (publication/ALIAS).exists()
    assert not git(publication, ['for-each-ref', 'refs/bootstrap-backup'])


def test_seed_is_metadata_only_and_final_retains_both_histories(repos):
    publication, origin, remote, tip, report = repos
    original_tree = git(publication, ['ls-tree', '-r', tip])
    original_files = {path: path.read_bytes() for path in publication.rglob('*.json')}
    result = run(repos, apply=True)
    seeded, final, tip2 = result['seed'], result['final'], result['tip_with_aliases']
    changes = git(publication, ['diff', '--name-status', remote, seeded]).decode().splitlines()
    assert changes and all(row.startswith('A\t'+ALIAS) for row in changes)
    assert git(publication, ['show', seeded+':'+INDEX]) == git(
        publication, ['show', remote+':'+INDEX])
    new_objects = git(publication, ['rev-list', '--objects', seeded, '^'+remote])
    kinds = git(publication, ['cat-file', '--batch-check=%(objecttype)'], data=b'\n'.join(
        row.split(b' ', 1)[0] for row in new_objects.splitlines())+b'\n')
    assert b'blob' not in kinds.splitlines()
    git(publication, ['merge-base', '--is-ancestor', remote, final])
    git(publication, ['merge-base', '--is-ancestor', tip, final])
    git(publication, ['merge-base', '--is-ancestor', seeded, final])
    git(publication, ['merge-base', '--is-ancestor', tip2, final])
    assert git(publication, ['rev-parse', tip2+'^{tree}']) == git(
        publication, ['rev-parse', final+'^{tree}'])
    assert git(publication, ['rev-parse', 'HEAD']).decode().strip() == final
    assert git(origin, ['rev-parse', 'main']).decode().strip() == final
    assert git(publication, ['rev-parse', result['backup_ref']]).decode().strip() == tip
    assert not git(publication, ['status', '--porcelain', '--untracked-files=no'])
    assert all(path.read_bytes() == value for path, value in original_files.items())
    assert (publication/'operator-notes.txt').read_bytes() == b'preserve untracked'
    assert b'\n'.join(row for row in git(publication, ['ls-tree', '-r', final]).splitlines()
                      if not row.split(b'\t')[1].startswith(ALIAS.encode()))+b'\n' == original_tree
    assert json.loads(report.read_bytes())['state'] == 'COMPLETE'


@pytest.mark.parametrize('damage', ['dirty', 'staged', 'wrong-remote', 'untracked-alias'])
def test_preconditions_fail_without_ref_changes(repos, damage):
    publication, origin, remote, tip, report = repos
    if damage in ('dirty', 'staged'):
        (publication/INDEX).write_text('{}')
        if damage == 'staged':
            git(publication, ['add', INDEX])
    elif damage == 'wrong-remote':
        git(publication, ['remote', 'set-url', '--push', 'origin', str(origin)+'-wrong'])
    else:
        path = publication/ALIAS/'untracked'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'preserve')
    with pytest.raises(RuntimeError, match='CHECK_REPORT'):
        run(repos, apply=True)
    assert git(publication, ['rev-parse', 'HEAD']).decode().strip() == tip
    assert git(origin, ['rev-parse', 'main']).decode().strip() == remote
    assert json.loads(report.read_bytes())['checkpoint'] == 'PRECONDITIONS'


def test_failure_after_public_seed_retains_seed_backup_and_tip(repos):
    publication, origin, remote, tip, report = repos
    calls = []

    def fail_latest(base, root, **kwargs):
        calls.append(root)
        if root == publication:
            raise ValueError('TEST_STOP_AFTER_SEED')
        return seed(base, root, **kwargs)

    with pytest.raises(RuntimeError):
        bootstrap(publication.parent, publication, report, apply=True,
                  expected_remote=str(origin), _run=git, _stopped=lambda: None,
                  _seed=fail_latest, _validate=lambda value: value)
    evidence = json.loads(report.read_bytes())
    assert evidence['checkpoint'] == 'PUBLIC_SEED_CONFIRMED'
    assert git(origin, ['rev-parse', 'main']).decode().strip() == evidence['seed']
    assert git(publication, ['rev-parse', 'HEAD']).decode().strip() == tip
    assert git(publication, ['rev-parse', evidence['backup_ref']]).decode().strip() == tip
    git(publication, ['merge-base', '--is-ancestor', remote, evidence['seed']])
    assert len(calls) == 2


def test_quiescence_is_required_before_any_mutation(repos):
    publication, origin, remote, tip, report = repos

    def running():
        raise ValueError('TEST_PUBLISHER_RUNNING')

    with pytest.raises(RuntimeError):
        bootstrap(publication.parent, publication, report, apply=True,
                  expected_remote=str(origin), _run=git, _stopped=running,
                  _seed=seed, _validate=lambda value: value)
    assert git(origin, ['rev-parse', 'main']).decode().strip() == remote
    assert git(publication, ['rev-parse', 'HEAD']).decode().strip() == tip


def test_remote_race_is_detected_before_seed_push(repos):
    publication, origin, _, tip, report = repos
    changed = False

    def racing_git(root, args, **kwargs):
        nonlocal changed
        if args[0] == 'ls-remote' and not changed:
            changed = True
            return git(root, args, **kwargs)
        if args[0] == 'ls-remote' and changed:
            git(origin, ['update-ref', 'refs/heads/main', tip])
        assert '--force' not in args and 'reset' not in args and 'clean' not in args
        return git(root, args, **kwargs)

    # The pending TIP already exists in the origin object database only after a
    # local object transfer under a separate ref, without changing public main.
    git(publication, ['push', 'origin', tip+':refs/heads/race-fixture'])
    with pytest.raises(RuntimeError):
        bootstrap(publication.parent, publication, report, apply=True,
                  expected_remote=str(origin), _run=racing_git, _stopped=lambda: None,
                  _seed=seed, _validate=lambda value: value)
    assert git(origin, ['rev-parse', 'main']).decode().strip() == tip
    assert git(publication, ['rev-parse', 'HEAD']).decode().strip() == tip


def test_cli_has_no_destination_override():
    from scripts import bootstrap_public_aliases as module

    assert REMOTE == 'ssh://git@ssh.github.com:443/panxy1019/QTrade-observations.git'
    source = Path(module.__file__).read_text()
    assert "add_argument('--expected-remote'" not in source


def test_source_tamper_between_precheck_and_seed_fails_closed(repos):
    publication, origin, remote, tip, report = repos

    def tampering_git(root, args, **kwargs):
        if args[0] == 'hash-object':
            Path(kwargs['data'].decode().splitlines()[0]).write_bytes(b'tampered immutable object')
        return git(root, args, **kwargs)

    with pytest.raises(RuntimeError):
        bootstrap(publication.parent, publication, report, apply=True,
                  expected_remote=str(origin), _run=tampering_git, _stopped=lambda: None,
                  _seed=seed, _validate=lambda value: value)
    assert git(origin, ['rev-parse', 'main']).decode().strip() == remote
    assert git(publication, ['rev-parse', 'HEAD']).decode().strip() == tip
    assert json.loads(report.read_bytes())['error_code'] == 'BOOTSTRAP_BASE_OBJECT_CHANGED'


def test_push_timeout_after_acceptance_is_reconciled(repos):
    publication, origin, _, _, report = repos

    def accepted_timeout(root, args, **kwargs):
        value = git(root, args, **kwargs)
        if args[0] == 'push':
            assert kwargs['timeout'] == 600
            raise subprocess.TimeoutExpired('git', 600)
        return value

    result = bootstrap(publication.parent, publication, report, apply=True,
                       expected_remote=str(origin), _run=accepted_timeout,
                       _stopped=lambda: None, _seed=seed, _validate=lambda value: value)
    assert result['state'] == 'COMPLETE'
    assert git(origin, ['rev-parse', 'main']).decode().strip() == result['final']


def test_final_push_failure_keeps_local_history_and_public_seed(repos):
    publication, origin, _, tip, report = repos
    pushes = 0

    def failed_final(root, args, **kwargs):
        nonlocal pushes
        if args[0] == 'push':
            pushes += 1
            if pushes == 2:
                raise RuntimeError('LOCAL_EXPECTED_PUSH_FAILURE')
        return git(root, args, **kwargs)

    with pytest.raises(RuntimeError):
        bootstrap(publication.parent, publication, report, apply=True,
                  expected_remote=str(origin), _run=failed_final,
                  _stopped=lambda: None, _seed=seed, _validate=lambda value: value)
    result = json.loads(report.read_bytes())
    assert result['checkpoint'] == 'LOCAL_ALIASES_COMMITTED'
    assert git(origin, ['rev-parse', 'main']).decode().strip() == result['seed']
    assert git(publication, ['rev-parse', 'HEAD']).decode().strip() == result['tip_with_aliases']
    git(publication, ['merge-base', '--is-ancestor', tip, result['tip_with_aliases']])
    assert git(publication, ['rev-parse', result['backup_ref']]).decode().strip() == tip


def test_commit_budget_is_checked_before_backup(repos):
    publication, _, _, _, report = repos
    for _ in range(16):
        git(publication, ['commit', '--allow-empty', '-m', 'pending'])
    with pytest.raises(RuntimeError):
        run(repos, apply=True)
    assert not git(publication, ['for-each-ref', 'refs/bootstrap-backup'])
    assert json.loads(report.read_bytes())['error_code'] == 'BOOTSTRAP_PENDING_HISTORY_BUDGET'


@pytest.mark.parametrize('destination', ['inside', 'existing', 'symlink'])
def test_invalid_report_destination_never_writes_artifacts(repos, destination):
    publication, origin, remote, tip, default_report = repos
    report = publication/'report.json' if destination == 'inside' else default_report
    if destination == 'existing':
        report.write_bytes(b'preserve existing report')
    if destination == 'symlink':
        target = report.with_name('existing-target')
        target.write_bytes(b'preserve symlink target')
        try:
            report.symlink_to(target)
        except OSError:
            pytest.skip('Symlink privilege unavailable')
    with pytest.raises(RuntimeError, match='REPORT_DESTINATION_INVALID'):
        bootstrap(publication.parent, publication, report, apply=True,
                  expected_remote=str(origin), _run=git, _stopped=lambda: None,
                  _seed=seed, _validate=lambda value: value)
    if destination == 'inside':
        assert not report.exists()
    elif destination == 'existing':
        assert report.read_bytes() == b'preserve existing report'
    else:
        assert report.is_symlink() and report.read_bytes() == b'preserve symlink target'
    assert git(origin, ['rev-parse', 'main']).decode().strip() == remote
    assert git(publication, ['rev-parse', 'HEAD']).decode().strip() == tip


def test_same_tip_branch_switch_is_detected_before_push(repos):
    publication, origin, remote, tip, report = repos
    calls = 0

    def stopped_then_switched():
        nonlocal calls
        calls += 1
        if calls == 2:
            git(publication, ['switch', '-c', 'different-branch'])

    with pytest.raises(RuntimeError):
        bootstrap(publication.parent, publication, report, apply=True,
                  expected_remote=str(origin), _run=git, _stopped=stopped_then_switched,
                  _seed=seed, _validate=lambda value: value)
    assert git(origin, ['rev-parse', 'main']).decode().strip() == remote
    assert git(publication, ['rev-parse', 'HEAD']).decode().strip() == tip
    assert json.loads(report.read_bytes())['error_code'] == 'BOOTSTRAP_LOCAL_CHANGED'


@pytest.mark.parametrize('state', ['Exited', 'Stopped', 'EXITED'])
def test_supervisor_quiescence_accepts_verified_mixed_case_state(monkeypatch, state):
    from scripts.bootstrap_public_aliases import _quiescent

    def process(name):
        assert name == 'qtrade-research-publication'
        return {'pid': 0, 'statename': state}

    def proxy(url, transport):
        assert url == 'http://localhost/RPC2'
        return nullcontext(SimpleNamespace(supervisor=SimpleNamespace(getProcessInfo=process)))

    monkeypatch.setattr('scripts.bootstrap_public_aliases.xmlrpc.client.ServerProxy', proxy)
    _quiescent()

