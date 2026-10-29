"""NONPRODUCTION local-bare protocol proof. No SSH or production endpoint API.

Prebuilt bounded packs contain certified public objects only. Auxiliary refs are
new and immutable; main is never a destination. Production authentication and
endpoint adaptation require a separate review and are intentionally absent.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import select
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

MAX_PACK = 2 * 1024 * 1024
MAX_BLOB = 16 * 1024 * 1024
MAX_TARGET_BYTES = 512 * 1024 * 1024
SCHEMA = 'qtrade.certified-public-git-target.nonproduction.v1'
ROOT_JSON = {'latest.json', 'current.json', 'unified.json', 'unified-market.json', 'online.json',
             'quality.json', 'signals.json', 'comparison.json', 'forward.json', 'etf-trend.json',
             'etf-forward.json', 'etf-history.json', 'workflow.json', 'qlib.json'}
FORBIDDEN_KEYS = {'password', 'passwd', 'secret', 'token', 'private_key', 'credentials', 'access_key'}


def require(value, code):
    if not value:
        raise ValueError('OBJECT_STAGE_' + code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def identity(value, size=40):
    return type(value) is str and re.fullmatch('[0-9a-f]{' + str(size) + '}', value) is not None


def checked_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: require(False, 'JSON_CONSTANT'))


def public_path(name):
    return (name in ROOT_JSON or name == 'README.md'
            or re.fullmatch(r'unified-market/objects/[0-9a-f]{64}\.json', name) is not None
            or re.fullmatch(r'datasets/[a-zA-Z0-9_./-]+\.json', name) is not None) and (
                '..' not in name.split('/') and not name.startswith('/') and '\\' not in name)


def public_json(value, depth=0):
    require(depth <= 64, 'JSON_DEPTH')
    if isinstance(value, dict):
        require(not (set(value) & FORBIDDEN_KEYS), 'PRIVATE_JSON_FIELD')
        require(value.get('live_trading_enabled', False) is False
                and value.get('external_order_transmission', False) is False, 'LIVE_AUTHORITY')
        for item in value.values():
            public_json(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            public_json(item, depth + 1)
    else:
        require(value is None or type(value) in (str, int, float, bool), 'JSON_TYPE')


def environment():
    # Local-only Git deliberately ignores all private/global Git configuration.
    return {**os.environ, 'LIVE_TRADING_ENABLED': 'false', 'GIT_OPTIONAL_LOCKS': '0',
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_TERMINAL_PROMPT': '0', 'GIT_AUTHOR_NAME': 'QTrade Object Stage',
            'GIT_AUTHOR_EMAIL': 'object-stage@invalid', 'GIT_COMMITTER_NAME': 'QTrade Object Stage',
            'GIT_COMMITTER_EMAIL': 'object-stage@invalid',
            'GIT_AUTHOR_DATE': '2000-01-01T00:00:00Z', 'GIT_COMMITTER_DATE': '2000-01-01T00:00:00Z'}


def cleanup(process):
    if os.name != 'posix':
        if process.poll() is None:
            process.kill()
        process.wait(timeout=4)
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(.05)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=2)


def git(repo, args, data=None, timeout=90):
    require(0 < timeout <= 90, 'GIT_90_SECOND_BOUND')
    command = ['git', '-C', str(repo), *args]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=environment(), start_new_session=os.name == 'posix')
    try:
        output, error = process.communicate(data, timeout=timeout)
        require(process.returncode == 0, 'GIT_FAILED_' + sha(error)[:16])
        require(len(output) <= MAX_TARGET_BYTES + 16_000_000, 'GIT_OUTPUT_BOUND')
        return output
    finally:
        cleanup(process)


def create_isolated_repository(path, source_objects):
    path, source_objects = Path(path), Path(source_objects)
    require(path.is_absolute() and not path.exists(), 'NEW_ISOLATED_PATH_REQUIRED')
    require(source_objects.is_absolute() and source_objects.is_dir()
            and source_objects.resolve() == source_objects and '\n' not in str(source_objects), 'OBJECT_ALTERNATES_PATH')
    path.mkdir(parents=False)
    git(path, ['init', '--bare', '--template='])
    alternates = path / 'objects/info/alternates'
    alternates.write_bytes((source_objects.as_posix() + '\n').encode('utf-8'))
    return path


class CertifiedTarget:
    """A supplied certification is a trust input, not self-certification of origin.

    All bytes are independently checked; source/inventory bindings must themselves
    be approved externally. Private source paths and credential JSON are denied.
    """
    def __init__(self, repo, certificate):
        require(set(certificate) == {'schema_version', 'source_revision', 'source_inventory_sha256',
                'target_commit', 'public_files'}, 'CERTIFICATE_KEYS')
        require(certificate['schema_version'] == SCHEMA and identity(certificate['source_revision'])
                and identity(certificate['target_commit']) and identity(certificate['source_inventory_sha256'], 64),
                'CERTIFICATE_IDENTITY')
        files = certificate['public_files']
        require(type(files) is dict and 0 < len(files) <= 100_000
                and all(type(k) is str and public_path(k) and identity(v, 64) for k, v in files.items()),
                'PUBLIC_PATH_ALLOWLIST')
        self.repo, self.certificate = Path(repo), checked_json(canonical(certificate))
        self.certificate_sha256 = sha(canonical(certificate))
        self.runid = self.certificate_sha256[:24]
        commit = certificate['target_commit']
        require(git(repo, ['cat-file', '-t', commit]).strip() == b'commit', 'TARGET_COMMIT_REQUIRED')
        listing = git(repo, ['ls-tree', '-rz', commit])
        entries = {}
        total = 0
        self.blobs = set()
        for line in listing.split(b'\0'):
            if not line:
                continue
            metadata, raw_name = line.split(b'\t', 1)
            mode, kind, oid = metadata.decode().split()
            name = raw_name.decode('utf-8')
            require(mode == '100644' and kind == 'blob' and identity(oid) and name in files, 'TARGET_PRIVATE_OR_SPECIAL_ENTRY')
            raw = git(repo, ['cat-file', 'blob', oid])
            total += len(raw)
            require(len(raw) <= MAX_BLOB and total <= MAX_TARGET_BYTES and sha(raw) == files[name], 'PUBLIC_BYTES_MISMATCH')
            if name.endswith('.json'):
                public_json(checked_json(raw))
            if name.startswith('unified-market/objects/'):
                require(name.split('/')[-1][:-5] == sha(raw), 'CONTENT_ADDRESS_MISMATCH')
            entries[name] = oid
            self.blobs.add(oid)
        require(set(entries) == set(files), 'EXACT_TARGET_CERTIFICATION_REQUIRED')
        root = git(repo, ['rev-parse', commit + '^{tree}']).decode().strip()
        trees = {root}
        for line in git(repo, ['ls-tree', '-rt', commit]).splitlines():
            metadata = line.split(b'\t', 1)[0].decode().split()
            if metadata[1] == 'tree':
                trees.add(metadata[2])
        self.trees, self.entries = trees, entries


@dataclass(frozen=True)
class BatchPlan:
    sequence: int
    ref: str
    commit: str
    tree: str
    target_commit: str
    source_revision: str
    certificate_sha256: str
    selected: tuple[str, ...]
    remote_refs: tuple[tuple[str, str], ...]
    pack: bytes
    pack_sha256: str
    objects: tuple[str, ...]
    maximum_pack_bytes: int


def plan_batch(target, selected, remote_refs, sequence, maximum_pack_bytes=MAX_PACK):
    require(type(sequence) is int and 1 <= sequence <= 9999, 'SEQUENCE_BOUND')
    require(type(maximum_pack_bytes) is int and 256 <= maximum_pack_bytes <= MAX_PACK, 'PACK_BOUND')
    require(type(remote_refs) is dict and all(re.fullmatch(r'refs/[A-Za-z0-9_./-]+', k)
            and identity(v) for k, v in remote_refs.items()), 'REMOTE_REFS')
    selected = tuple(sorted(set(selected)))
    require(selected and len(selected) <= 100_000 and all(identity(oid) and oid in target.blobs | target.trees for oid in selected),
            'CERTIFIED_TARGET_OBJECTS_ONLY')
    rows = []
    for oid in selected:
        kind = 'blob' if oid in target.blobs else 'tree'
        rows.append(f"{'100644' if kind == 'blob' else '040000'} {kind} {oid}\tpublic-{oid}{'.json' if kind == 'blob' else ''}\n")
    tree = git(target.repo, ['mktree'], ''.join(rows).encode()).decode().strip()
    message = canonical({'schema_version': 'qtrade.public-object-stage-commit.nonproduction.v1',
        'certificate_sha256': target.certificate_sha256, 'source_revision': target.certificate['source_revision'],
        'target_commit': target.certificate['target_commit'], 'sequence': sequence})
    commit = git(target.repo, ['commit-tree', tree], message + b'\n').decode().strip()
    revisions = (commit + '\n' + ''.join('^' + oid + '\n' for oid in sorted(set(remote_refs.values())))).encode()
    objects = tuple(git(target.repo, ['rev-list', '--objects', '--no-object-names', '--stdin'], revisions).decode().splitlines())
    require(set(objects) <= target.blobs | target.trees | {tree, commit}, 'UNAPPROVED_PACK_OBJECT')
    pack = git(target.repo, ['pack-objects', '--stdout', '--revs', '--delta-base-offset', '--window=0',
        '--depth=0', '--no-reuse-delta', '--no-reuse-object', '--compression=6'], revisions)
    require(pack.startswith(b'PACK') and 12 <= len(pack) <= maximum_pack_bytes, 'EXACT_PACK_BYTES_EXCEEDED')
    ref = f'refs/heads/qtrade-object-stage/{target.runid}-{sequence:04d}'
    require(ref not in remote_refs, 'AUX_REF_EXISTS_NO_RETRY')
    return BatchPlan(sequence, ref, commit, tree, target.certificate['target_commit'],
        target.certificate['source_revision'], target.certificate_sha256, selected,
        tuple(sorted(remote_refs.items())), pack, sha(pack), objects, maximum_pack_bytes)


def pkt(data):
    require(len(data) <= 65516, 'PKT_BOUND')
    return f'{len(data) + 4:04x}'.encode() + data


def report_packets(raw):
    result = []
    while raw:
        require(len(raw) >= 4 and re.fullmatch(b'[0-9a-f]{4}', raw[:4]), 'BAD_REPORT_PACKET')
        length = int(raw[:4], 16)
        raw = raw[4:]
        if length == 0:
            require(not raw, 'REPORT_TRAILING_BYTES')
            return result
        require(4 <= length <= len(raw) + 4, 'BAD_REPORT_LENGTH')
        result.append(raw[:length - 4]); raw = raw[length - 4:]
    require(False, 'REPORT_FLUSH_REQUIRED')


class LocalBareTransport:
    """Actual local receive-pack protocol, POSIX only. Cannot accept a URL.

    The endpoint must have an explicit fixture sentinel under its isolated root.
    This is not the production SSH implementation and grants no production apply.
    """
    def __init__(self, endpoint, fixture_root):
        endpoint, fixture_root = Path(endpoint), Path(fixture_root)
        require(endpoint.is_absolute() and fixture_root.is_absolute() and endpoint.resolve() == endpoint
            and endpoint.is_relative_to(fixture_root) and endpoint != fixture_root
            and not any(p.is_symlink() for p in (endpoint, *endpoint.parents)), 'LOCAL_FIXTURE_ENDPOINT')
        marker = endpoint / 'qtrade-nonproduction-object-stage.json'
        require(marker.is_file() and checked_json(marker.read_bytes()) == {
            'schema_version': 'qtrade.local-bare-fixture.v1', 'production': False}, 'FIXTURE_SENTINEL_REQUIRED')
        require(git(endpoint, ['rev-parse', '--is-bare-repository']).strip() == b'true', 'BARE_FIXTURE_REQUIRED')
        self.endpoint = endpoint

    def refs(self):
        rows = git(self.endpoint, ['for-each-ref', '--format=%(refname) %(objectname)']).decode().splitlines()
        return dict(line.split() for line in rows)

    def send(self, target, plan, *, truncate_pack_at=None, suppress_ack=False):
        require(os.name == 'posix', 'POSIX_OWNED_GROUP_TRANSPORT_REQUIRED')
        require(re.fullmatch(r'refs/heads/qtrade-object-stage/[0-9a-f]{24}-[0-9]{4}', plan.ref)
                and identity(plan.commit) and plan.pack_sha256 == sha(plan.pack)
                and 12 <= len(plan.pack) <= plan.maximum_pack_bytes <= MAX_PACK, 'PLAN_BINDING_CHANGED')
        require(plan == plan_batch(target, plan.selected, dict(plan.remote_refs), plan.sequence,
                                   plan.maximum_pack_bytes), 'PLAN_REDERIVATION_MISMATCH')
        expected = dict(plan.remote_refs)
        require(self.refs() == expected and plan.ref not in expected, 'ADVERTISEMENT_CHANGED_NO_RETRY')
        process = subprocess.Popen(['git', '-c', 'receive.unpackLimit=0', 'receive-pack', str(self.endpoint)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
            env=environment(), start_new_session=True)
        deadline = time.monotonic() + 90
        try:
            def exact(count):
                data = b''
                while len(data) < count:
                    remaining = deadline - time.monotonic()
                    require(remaining > 0 and select.select([process.stdout], [], [], remaining)[0], 'RECEIVE_90_SECOND_BOUND')
                    chunk = os.read(process.stdout.fileno(), count - len(data))
                    require(chunk, 'ADVERTISEMENT_EOF')
                    data += chunk
                return data
            advertisement = b''
            while True:
                header = exact(4)
                require(re.fullmatch(b'[0-9a-f]{4}', header), 'ADVERTISEMENT_PACKET')
                count = int(header, 16)
                if count == 0:
                    break
                require(4 <= count <= 65520 and len(advertisement) + count < 2_000_000, 'ADVERTISEMENT_BOUND')
                advertisement += exact(count - 4)
            require(b'report-status' in advertisement and b'object-format=sha256' not in advertisement, 'RECEIVE_CAPABILITY')
            actual = {}
            for line in advertisement.splitlines():
                line = line.split(b'\0', 1)[0]
                oid, ref = line.decode().split(' ', 1)
                if ref != 'capabilities^{}':
                    actual[ref] = oid
            require(actual == expected, 'LIVE_ADVERTISEMENT_CHANGED_NO_RETRY')
            payload = pkt(('0' * 40 + ' ' + plan.commit + ' ' + plan.ref + '\0report-status\n').encode()) + b'0000'
            wire_pack = plan.pack if truncate_pack_at is None else plan.pack[:truncate_pack_at]
            output, error = process.communicate(payload + wire_pack, timeout=max(.01, deadline - time.monotonic()))
            require(not suppress_ack and process.returncode == 0 and report_packets(output) == [
                b'unpack ok\n', ('ok ' + plan.ref + '\n').encode()], 'ACK_NOT_CONFIRMED_NO_RETRY')
            after = self.refs()
            require(after == {**expected, plan.ref: plan.commit}, 'AFTER_REFS_CHANGED')
            return {'schema_version': 'qtrade.local-object-stage-receipt.v1', 'state': 'STAGED_LOCAL_FIXTURE_ONLY',
                'ref': plan.ref, 'commit': plan.commit, 'target_commit': plan.target_commit,
                'source_revision': plan.source_revision, 'certificate_sha256': plan.certificate_sha256,
                'pack_bytes': len(wire_pack), 'pack_sha256': sha(wire_pack), 'object_count': len(plan.objects),
                'before_refs_sha256': sha(canonical(expected)), 'after_refs_sha256': sha(canonical(after)),
                'main_unchanged': after.get('refs/heads/main') == expected.get('refs/heads/main'),
                'ack_sha256': sha(output), 'stderr_sha256': sha(error), 'production_apply': False}
        finally:
            cleanup(process)
