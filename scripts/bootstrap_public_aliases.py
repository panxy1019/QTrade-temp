"""One-off, explicitly applied public Git alias bootstrap; never restarts a service.

Default invocation only inspects the dedicated publication repository. A failed
apply retains its backup ref and reports the last checkpoint; there is no resume
or rollback mode. Ordinary publisher command limits are unchanged.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import socket
import subprocess
import tempfile
import uuid
import xmlrpc.client
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import signed
from scripts.atomic_json import write_json_atomic

REMOTE = 'ssh://git@ssh.github.com:443/panxy1019/QTrade-observations.git'
ALIAS = 'unified-market/.delta/'
INDEX = 'unified-market.json'


def _git(root: Path, args: list[str], *, data: bytes | None = None,
         index: Path | None = None, timeout: int = 90) -> bytes:
    from scripts.publish_research_snapshot import _cleanup_git_group

    if os.name != 'posix':
        raise RuntimeError('BOOTSTRAP_REQUIRES_POSIX')
    env = os.environ.copy()
    for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'):
        env.pop(key, None)
    env['GIT_TERMINAL_PROMPT'] = '0'
    if index is not None:
        env['GIT_INDEX_FILE'] = str(index)
    process = subprocess.Popen(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(root),
                                *args], env=env, start_new_session=True,
                               stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        output, _ = process.communicate(data, timeout=timeout)
    finally:
        _cleanup_git_group(process)
    if process.returncode or len(output) > 16_000_000:
        raise RuntimeError('BOOTSTRAP_GIT_FAILED')
    return output


def _quiescent() -> None:
    class UnixTransport(xmlrpc.client.Transport):
        def make_connection(self, host: str | tuple[str, dict[str, str]]
                            ) -> http.client.HTTPConnection:
            connection = http.client.HTTPConnection('localhost', timeout=10)
            connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.sock.settimeout(10)
            connection.sock.connect('/var/run/supervisor.sock')
            return connection

    with xmlrpc.client.ServerProxy('http://localhost/RPC2', transport=UnixTransport()) as server:
        info = server.supervisor.getProcessInfo('qtrade-research-publication')
    if (not isinstance(info, dict) or info['pid'] != 0
            or str(info['statename']).upper() not in ('EXITED', 'STOPPED')):
        raise ValueError('BOOTSTRAP_PUBLISHER_NOT_STOPPED')


def bootstrap(base: Path, publication: Path, report_path: Path, *, apply: bool = False,
              expected_remote: str = REMOTE, _run: Any = _git,
              _stopped: Any = _quiescent, _seed: Any = None, _validate: Any = None
              ) -> dict[str, Any]:
    """Internal local-test seams cannot change the CLI's fixed public destination."""
    if (report_path.exists() or report_path.is_symlink()
            or any(parent.is_symlink() for parent in report_path.parents)
            or report_path.resolve().is_relative_to(publication.resolve())):
        raise RuntimeError('BOOTSTRAP_REPORT_DESTINATION_INVALID')
    from qtrade.research.unified_market import validate_index
    from scripts.publish_unified_market import publish_market_files

    seed = _seed or publish_market_files
    validate = _validate or validate_index
    report: dict[str, Any] = {'schema_version': 'qtrade.public-alias-bootstrap.v1',
                              'apply': apply, 'state': 'FAILED', 'checkpoint': 'PRECONDITIONS'}

    def git(args: list[str], **kwargs: Any) -> bytes:
        value: bytes = _run(publication, args, **kwargs)
        return value

    def oid(args: list[str], **kwargs: Any) -> str:
        return git(args, **kwargs).decode('ascii').strip()

    def remote_head() -> str:
        fields = git(['ls-remote', '--refs', 'origin', 'refs/heads/main']).split()
        if len(fields) != 2 or fields[1] != b'refs/heads/main':
            raise ValueError('BOOTSTRAP_REMOTE_MAIN_INVALID')
        return fields[0].decode('ascii')

    def unchanged(revision: str) -> None:
        _stopped()
        if (oid(['symbolic-ref', '--short', 'HEAD']) != 'main'
                or oid(['rev-parse', 'HEAD']) != revision or git(
                ['status', '--porcelain', '--untracked-files=no'])):
            raise ValueError('BOOTSTRAP_LOCAL_CHANGED')

    def tree(revision: str) -> dict[str, tuple[str, str]]:
        result = {}
        for row in git(['ls-tree', '-r', '-z', revision]).split(b'\0'):
            if row:
                metadata, name = row.split(b'\t', 1)
                mode, kind, digest = metadata.decode('ascii').split()
                if kind != 'blob':
                    raise ValueError('BOOTSTRAP_NON_BLOB_TREE')
                result[name.decode()] = (mode, digest)
        return result

    def alias_tree(revision: str, root: Path, temporary: Path) -> tuple[str, list[str]]:
        original = tree(revision)
        pointer = (root/INDEX).read_bytes()
        if (pointer != git(['show', revision+':'+INDEX])
                or any(name.startswith(ALIAS) for name in original)):
            raise ValueError('BOOTSTRAP_POINTER_CHANGED_OR_ALREADY_SEEDED')
        names = seed(base, root, seed_existing=True)
        aliases = sorted({name for name in names if name.startswith(ALIAS)})
        if not aliases:
            raise ValueError('BOOTSTRAP_ALREADY_SEEDED_OR_EMPTY')
        index = temporary/'index'
        git(['read-tree', revision], index=index)
        for name in aliases:
            path = root/name
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(
                    root.resolve()) or '\n' in name or '\t' in name:
                raise ValueError('BOOTSTRAP_ALIAS_PATH_INVALID')
        digests = git(['hash-object', '--stdin-paths'], data=''.join(
            str(root/name)+'\n' for name in aliases).encode()).decode().splitlines()
        if len(digests) != len(aliases) or set(git(
                ['cat-file', '--batch-check=%(objecttype)'],
                data=('\n'.join(digests)+'\n').encode()).splitlines()) != {b'blob'}:
            raise ValueError('BOOTSTRAP_ALIAS_BLOB_MISSING')
        updates = [f'100644 {digest}\t{name}\n' for name, digest in zip(aliases, digests,
                                                                      strict=True)]
        git(['update-index', '--index-info'], data=''.join(updates).encode(), index=index)
        result = oid(['write-tree'], index=index)
        expected = {**original, **{name: ('100644', digest)
                                  for name, digest in zip(aliases, digests, strict=True)}}
        if tree(result) != expected or (root/INDEX).read_bytes() != pointer:
            raise ValueError('BOOTSTRAP_NON_ALIAS_TREE_CHANGE')
        return result, aliases

    def commit(tree_id: str, parents: list[str], message: str) -> str:
        args = ['commit-tree', tree_id]
        for parent in parents:
            args.extend(['-p', parent])
        return oid(args, data=(message+'\n').encode())

    def push(previous: str, target: str) -> None:
        _stopped()
        if remote_head() != previous:
            raise ValueError('BOOTSTRAP_REMOTE_CHANGED')
        try:
            git(['push', 'origin', target+':refs/heads/main'], timeout=600)
        except (RuntimeError, subprocess.TimeoutExpired):
            # Git may time out after the server accepted the ref update.
            if remote_head() != target:
                raise
        if remote_head() != target:
            raise ValueError('BOOTSTRAP_PUSH_UNCONFIRMED')

    try:
        if (publication.is_symlink() or (publication/'.git').is_symlink()
                or not (publication/'.git').is_dir()
                or Path(oid(['rev-parse', '--show-toplevel'])).resolve() != publication.resolve()
                or report_path.resolve().is_relative_to(publication.resolve())):
            raise ValueError('BOOTSTRAP_DEDICATED_REPOSITORY_REQUIRED')
        for args in (['remote', 'get-url', '--all', 'origin'],
                     ['remote', 'get-url', '--push', '--all', 'origin']):
            if git(args).decode().splitlines() != [expected_remote]:
                raise ValueError('BOOTSTRAP_DESTINATION_MISMATCH')
        if oid(['symbolic-ref', '--short', 'HEAD']) != 'main':
            raise ValueError('BOOTSTRAP_MAIN_REQUIRED')
        tip = oid(['rev-parse', 'HEAD'])
        unchanged(tip)
        old_untracked = git(['ls-files', '--others', '-z']).split(b'\0')
        if any(name.startswith(ALIAS.encode()) for name in old_untracked):
            raise ValueError('BOOTSTRAP_UNTRACKED_ALIAS_COLLISION')
        remote = remote_head()
        git(['merge-base', '--is-ancestor', remote, tip])
        count = int(oid(['rev-list', '--count', remote+'..'+tip]))
        objects = git(['rev-list', '--objects', tip, '^'+remote])
        sizes = git(['cat-file', '--batch-check=%(objecttype) %(objectsize)'],
                    data=b'\n'.join(row.split(b' ', 1)[0] for row in objects.splitlines())+b'\n')
        payload = sum(int(row.split()[1]) for row in sizes.splitlines()
                      if row.startswith(b'blob '))
        if count > 16 or payload > 256_000_000:
            raise ValueError('BOOTSTRAP_PENDING_HISTORY_BUDGET')
        report.update(base=remote, tip=tip, pending_commits=count, pending_blob_bytes=payload)
        if not apply:
            report.update(state='PLAN', checkpoint='PRECONDITIONS_VERIFIED')
            return signed(report)
        backup = 'refs/bootstrap-backup/'+uuid.uuid4().hex
        write_json_atomic(report_path, signed(report))
        git(['update-ref', backup, tip, '0'*40])
        report.update(backup_ref=backup, checkpoint='TIP_BACKED_UP')
        write_json_atomic(report_path, signed(report))
        with tempfile.TemporaryDirectory(prefix='.alias-bootstrap-',
                                         dir=publication.parent) as name:
            temporary = Path(name)
            seed_root = temporary/'seed'
            seed_root.mkdir()
            original = tree(remote)
            index_mode, index_blob = original[INDEX]
            if index_mode != '100644' or int(oid(['cat-file', '-s', index_blob])) > 4_000_000:
                raise ValueError('BOOTSTRAP_INDEX_INVALID')
            raw_index = git(['show', remote+':'+INDEX])
            index_doc = validate(json.loads(raw_index))
            (seed_root/INDEX).write_bytes(raw_index)
            relatives = sorted({'unified-market/objects/'+ref['sha256']+'.json'
                                for entry in index_doc['entries'] for ref in entry['shards']})
            for relative in relatives:
                source = publication/relative
                if (source.is_symlink() or not source.is_file()
                        or any(parent.is_symlink() for parent in source.parents)
                        or '\n' in str(source) or '\t' in str(source)):
                    raise ValueError('BOOTSTRAP_BASE_OBJECT_CHANGED')
            digests = git(['hash-object', '--stdin-paths'], data=''.join(
                str(publication/relative)+'\n' for relative in relatives).encode()
                ).decode().splitlines() if relatives else []
            if len(digests) != len(relatives):
                raise ValueError('BOOTSTRAP_BASE_OBJECT_CHANGED')
            for relative, digest in zip(relatives, digests, strict=True):
                if original.get(relative) != ('100644', digest):
                    raise ValueError('BOOTSTRAP_BASE_OBJECT_CHANGED')
                target = seed_root/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                os.link(publication/relative, target)
            seeded_tree, aliases = alias_tree(remote, seed_root, temporary)
            seeded = commit(seeded_tree, [remote], 'Seed existing public shard delta aliases')
            new_objects = git(['rev-list', '--objects', seeded, '^'+remote])
            kinds = git(['cat-file', '--batch-check=%(objecttype)'], data=b'\n'.join(
                row.split(b' ', 1)[0] for row in new_objects.splitlines())+b'\n')
            if b'blob' in kinds.splitlines() or (seed_root/INDEX).read_bytes() != raw_index:
                raise ValueError('BOOTSTRAP_SEED_INTRODUCED_DATA')
            report.update(seed=seeded, seed_aliases=len(aliases), seed_new_blobs=0,
                          base_index_blob=index_blob, checkpoint='SEED_PREPARED')
            write_json_atomic(report_path, signed(report))
            unchanged(tip)
            push(remote, seeded)
            report['checkpoint'] = 'PUBLIC_SEED_CONFIRMED'
            write_json_atomic(report_path, signed(report))
            unchanged(tip)
            # A separate index avoids accidentally including an unrelated untracked file.
            (temporary/'index').unlink()
            latest_tree, aliases = alias_tree(tip, publication, temporary)
            tip2 = commit(latest_tree, [tip], 'Add current public shard delta aliases')
            latest_entries = tree(latest_tree)
            git(['update-index', '--index-info'], data=''.join(
                f'100644 {latest_entries[alias][1]}\t{alias}\n' for alias in aliases).encode())
            if oid(['write-tree']) != latest_tree:
                raise ValueError('BOOTSTRAP_CURRENT_INDEX_CHANGED')
            git(['update-ref', 'HEAD', tip2, tip])
            report.update(tip_with_aliases=tip2, tip_index_blob=latest_entries[INDEX][1],
                          final_tree=latest_tree, checkpoint='LOCAL_ALIASES_COMMITTED')
            write_json_atomic(report_path, signed(report))
            final = commit(latest_tree, [seeded, tip2],
                           'Join alias seed with retained local history')
            report['final'] = final
            write_json_atomic(report_path, signed(report))
            unchanged(tip2)
            push(seeded, final)
            report['checkpoint'] = 'PUBLIC_FINAL_CONFIRMED'
            write_json_atomic(report_path, signed(report))
            unchanged(tip2)
            git(['merge', '--ff-only', final])
            unchanged(final)
            if oid(['rev-parse', 'HEAD^{tree}']) != latest_tree:
                raise ValueError('BOOTSTRAP_FINAL_TREE_CHANGED')
            report.update(state='COMPLETE', checkpoint='VERIFIED')
            return signed(report)
    except Exception as error:
        report['error_class'] = type(error).__name__
        if str(error).startswith('BOOTSTRAP_'):
            report['error_code'] = str(error)
        raise RuntimeError('BOOTSTRAP_FAILED_CHECK_REPORT') from None
    finally:
        write_json_atomic(report_path, signed(report))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--publication', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        result = bootstrap(args.base, args.publication, args.report, apply=args.apply)
    except Exception:
        parser.exit(1, 'BOOTSTRAP_FAILED_CHECK_REPORT\n')
    print(json.dumps({'state': result['state'], 'checkpoint': result['checkpoint']}))


if __name__ == '__main__':
    main()
