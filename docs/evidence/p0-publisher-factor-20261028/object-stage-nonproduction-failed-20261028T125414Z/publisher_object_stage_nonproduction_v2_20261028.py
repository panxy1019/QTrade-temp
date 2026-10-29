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
import sys
import tempfile
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
    allowed = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'SystemRoot', 'WINDIR',
        'COMSPEC', 'TEMP', 'TMP', 'LANG', 'LC_ALL') if key in os.environ}
    return {**allowed, 'LIVE_TRADING_ENABLED': 'false', 'PYTHONDONTWRITEBYTECODE': '1',
            'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_OPTIONAL_LOCKS': '0',
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
    command = ['git', '--no-replace-objects', '-c', 'core.hooksPath=/dev/null',
               '-c', 'core.fsmonitor=false', '-C', str(repo), *args]
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
        self.endpoint, self.fixture_root = endpoint, fixture_root

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
        state = target.repo / 'object-stage-intents'
        basename = plan.ref.rsplit('/', 1)[1]
        intent = state / (basename + '.intent.json')
        require(not intent.exists(), 'UPLOAD_INTENT_ALREADY_EXISTS_NO_RETRY')
        expected = dict(plan.remote_refs)
        require(self.refs() == expected and plan.ref not in expected, 'ADVERTISEMENT_CHANGED_NO_RETRY')
        body = {'phase': 'UPLOAD_INTENT', 'ref': plan.ref, 'commit': plan.commit,
            'pack_sha256': plan.pack_sha256, 'pack_bytes': len(plan.pack),
            'certificate_sha256': plan.certificate_sha256, 'source_revision': plan.source_revision,
            'target_commit': plan.target_commit, 'remote_refs': expected}
        immutable_json(intent, body)
        pack_path = state / (basename + '.pack')
        immutable_bytes(pack_path, plan.pack)
        request = {'schema_version': 'qtrade.local-dulwich-stage-request.v2',
            'endpoint': str(self.endpoint), 'fixture_root': str(self.fixture_root),
            'ref': plan.ref, 'commit': plan.commit, 'pack_path': str(pack_path),
            'pack_sha256': plan.pack_sha256, 'pack_bytes': len(plan.pack), 'objects': list(plan.objects),
            'remote_refs': expected, 'intent_path': str(intent), 'intent_sha256': sha(canonical(body)),
            'helper_sha256': sha(Path(__file__).read_bytes()),
            'truncate_pack_at': truncate_pack_at, 'suppress_ack': suppress_ack}
        request_path = state / (basename + '.request.json')
        immutable_json(request_path, request)
        process = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--local-worker', str(request_path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment(), start_new_session=True)
        try:
            output, error = process.communicate(timeout=90)
            require(process.returncode == 0 and len(output) <= 65536, 'ACK_NOT_CONFIRMED_NO_RETRY')
            answer = checked_json(output)
            require(answer == {'state': 'ACK_CONFIRMED_LOCAL_ONLY', 'ref': plan.ref,
                'commit': plan.commit, 'pack_sha256': plan.pack_sha256, 'pack_bytes': len(plan.pack)},
                'ACK_BINDING_CHANGED')
            after = self.refs()
            require(after == {**expected, plan.ref: plan.commit}, 'AFTER_REFS_CHANGED')
            receipt = {'schema_version': 'qtrade.local-object-stage-receipt.v2', 'state': 'STAGED_LOCAL_FIXTURE_ONLY',
                'ref': plan.ref, 'commit': plan.commit, 'target_commit': plan.target_commit,
                'source_revision': plan.source_revision, 'certificate_sha256': plan.certificate_sha256,
                'pack_bytes': len(plan.pack), 'pack_sha256': plan.pack_sha256, 'object_count': len(plan.objects),
                'before_refs_sha256': sha(canonical(expected)), 'after_refs_sha256': sha(canonical(after)),
                'main_unchanged': after.get('refs/heads/main') == expected.get('refs/heads/main'),
                'ack_sha256': sha(output), 'stderr_sha256': sha(error), 'production_apply': False}
            immutable_json(state / (basename + '.result.json'), receipt)
            return receipt
        except BaseException as error:
            immutable_json(state / (basename + '.result.json'), {'state': 'UNCERTAIN_NO_RETRY',
                'error_class': type(error).__name__, 'ref': plan.ref, 'pack_sha256': plan.pack_sha256})
            raise
        finally:
            cleanup(process)


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def immutable_bytes(path, raw):
    require(os.name == 'posix' and not any(p.is_symlink() for p in (path, *path.parents)), 'DURABLE_POSIX_PATH_REQUIRED')
    if not path.parent.exists():
        path.parent.mkdir()
        fsync_directory(path.parent.parent)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(raw); handle.flush(); os.fsync(handle.fileno())
    fsync_directory(path.parent)


def immutable_json(path, value):
    immutable_bytes(path, canonical(value))


def source_snapshot(source, public_files):
    """Hash only; never archive source configuration or object bytes."""
    source = Path(source)
    control = [source / '.git' / name for name in ('HEAD', 'index', 'config', 'packed-refs')]
    refs = source / '.git/refs'
    if refs.is_dir():
        control.extend(p for p in refs.rglob('*') if p.is_file())
    control.extend(source / name for name in public_files)
    return {p.relative_to(source).as_posix(): sha(p.read_bytes()) if p.is_file() else None
            for p in control}


def install_aux_metadata_local(target, plan, source, fixture_root):
    """Local fixture proof: add only a verified commit/tree pack and its index.

    This function is explicitly fixture-gated. No production installation API is
    implemented here. Final links are exclusive and appear only after fsync.
    """
    require(os.name == 'posix', 'POSIX_METADATA_INSTALL_REQUIRED')
    source, fixture_root = Path(source), Path(fixture_root)
    require(source.is_absolute() and source.is_relative_to(fixture_root)
        and (fixture_root / 'metadata-install-fixture.json').is_file(), 'METADATA_FIXTURE_REQUIRED')
    require(checked_json((fixture_root / 'metadata-install-fixture.json').read_bytes()) == {
        'schema_version': 'qtrade.metadata-install-fixture.v1', 'production': False}, 'METADATA_FIXTURE_REQUIRED')
    require(plan == plan_batch(target, plan.selected, dict(plan.remote_refs), plan.sequence,
        plan.maximum_pack_bytes), 'METADATA_PLAN_CHANGED')
    objects = source / '.git/objects'
    require(objects.resolve() == objects and not any(p.is_symlink() for p in (source, *source.parents)), 'METADATA_SOURCE_PATH')
    require((target.repo / 'objects/info/alternates').read_bytes() == (objects.as_posix() + '\n').encode(), 'BOUND_SOURCE_ALTERNATES')
    before = source_snapshot(source, target.certificate['public_files'])
    ids = [plan.commit, plan.tree]
    require([git(target.repo, ['cat-file', '-t', oid]).strip() for oid in ids] == [b'commit', b'tree'], 'METADATA_TYPES_ONLY')
    pack = git(target.repo, ['pack-objects', '--stdout', '--window=0', '--depth=0', '--no-reuse-delta',
        '--no-reuse-object', '--compression=6'], ('\n'.join(ids) + '\n').encode())
    require(len(pack) <= MAX_PACK and hashlib.sha1(pack[:-20]).digest() == pack[-20:], 'METADATA_PACK_BOUND_OR_CHECKSUM')
    from dulwich.pack import PackData
    from io import BytesIO
    with PackData('metadata.pack', file=BytesIO(pack), size=len(pack)) as data:
        records = list(data.iter_unpacked(include_comp=True))
        require({x.sha().decode() for x in records} == set(ids)
            and all(x.pack_type_num in (1, 2) and x.delta_base is None for x in records), 'ONLY_BOUND_AUX_METADATA')
    # Build/strictly verify the index in the isolated repository, never source.
    git(target.repo, ['index-pack', '--stdin', '--strict'], pack)
    name = 'pack-' + pack[-20:].hex()
    generated = target.repo / 'objects/pack' / name
    require(generated.with_suffix('.pack').read_bytes() == pack, 'METADATA_PACK_GENERATION_CHANGED')
    packroot = objects / 'pack'
    require(packroot.is_dir() and not packroot.is_symlink(), 'SOURCE_PACK_DIRECTORY')
    old = {p.name: sha(p.read_bytes()) for p in packroot.iterdir() if p.is_file()}
    destinations = [(packroot / (name + suffix), generated.with_suffix(suffix)) for suffix in ('.pack', '.idx')]
    require(all(not destination.exists() for destination, _ in destinations), 'METADATA_PACK_ALREADY_EXISTS')
    temporaries = []
    try:
        for destination, origin in destinations:
            descriptor, temporary = tempfile.mkstemp(prefix='.qtrade-stage-', dir=packroot)
            temporary = Path(temporary); temporaries.append(temporary)
            raw = origin.read_bytes()
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(raw); handle.flush(); os.fsync(handle.fileno())
            require(sha(temporary.read_bytes()) == sha(raw), 'METADATA_TEMP_BYTES')
            os.link(temporary, destination)  # Exclusive; no replace/overwrite.
            fsync_directory(packroot)
        require(source_snapshot(source, target.certificate['public_files']) == before, 'SOURCE_CONTROLS_OR_WORKTREE_CHANGED')
        require(all((packroot / name).is_file() and sha((packroot / name).read_bytes()) == value for name, value in old.items()),
            'OLD_OBJECT_PACK_CHANGED')
        require(git(source, ['cat-file', '-t', plan.commit]).strip() == b'commit', 'AUX_GRAPH_NOT_VISIBLE')
        return {'state': 'METADATA_INSTALLED_LOCAL_FIXTURE_ONLY', 'pack_sha256': sha(pack), 'pack_bytes': len(pack),
            'object_ids': ids, 'object_sha256': {oid: sha(git(target.repo, ['cat-file', kind, oid]))
                for oid, kind in ((plan.commit, 'commit'), (plan.tree, 'tree'))},
            'source_controls_before_sha256': sha(canonical(before)),
            'source_controls_after_sha256': sha(canonical(source_snapshot(source, target.certificate['public_files']))),
            'production_apply': False}
    finally:
        for path in temporaries:
            path.unlink(missing_ok=True)
        fsync_directory(packroot)


def local_dulwich_worker(request_path):
    """Mature Dulwich handles all wire framing/capabilities and status parsing."""
    from dulwich.client import SubprocessGitClient
    from dulwich.pack import PackData, PackChunkGenerator
    from dulwich.repo import Repo
    request = checked_json(request_path.read_bytes())
    require(request['schema_version'] == 'qtrade.local-dulwich-stage-request.v2'
        and sha(Path(__file__).read_bytes()) == request['helper_sha256'], 'WORKER_SOURCE_BINDING')
    intent_path = Path(request['intent_path'])
    require(sha(intent_path.read_bytes()) == request['intent_sha256'], 'WORKER_INTENT_BINDING')
    endpoint, fixture_root = Path(request['endpoint']), Path(request['fixture_root'])
    require(endpoint.is_absolute() and endpoint.is_relative_to(fixture_root) and endpoint != fixture_root
        and not any(p.is_symlink() for p in (endpoint, *endpoint.parents)), 'WORKER_LOCAL_ENDPOINT')
    require(checked_json((endpoint / 'qtrade-nonproduction-object-stage.json').read_bytes()) == {
        'schema_version': 'qtrade.local-bare-fixture.v1', 'production': False}, 'WORKER_FIXTURE_REQUIRED')
    pack_path = Path(request['pack_path']); pack = pack_path.read_bytes()
    require(pack_path.parent == intent_path.parent and 12 <= len(pack) == request['pack_bytes'] <= MAX_PACK
        and sha(pack) == request['pack_sha256'], 'WORKER_PACK_BINDING')
    with PackData(str(pack_path)) as data:
        records = list(data.iter_unpacked(include_comp=True))
        require(all(item.delta_base is None for item in records)
            and {item.sha().decode() for item in records} == set(request['objects']), 'WORKER_OBJECT_BINDING')
        require(b''.join(PackChunkGenerator(len(records), iter(records))) == pack, 'DULWICH_EXACT_REPLAY_REQUIRED')
        active, sent = [False], [0]
        class Client(SubprocessGitClient):
            git_command = ['git', '--no-replace-objects', '-c', 'core.hooksPath=/dev/null',
                           '-c', 'receive.unpackLimit=0']
            def _connect(self, *args, **kwargs):
                protocol, can_read, stderr = super()._connect(*args, **kwargs)
                original = protocol.write
                def write(raw):
                    cutoff = request['truncate_pack_at']
                    if active[0] and cutoff is not None:
                        remaining = max(0, cutoff - sent[0])
                        original(raw[:remaining]); sent[0] += min(remaining, len(raw))
                        if len(raw) > remaining:
                            raise RuntimeError('SYNTHETIC_LOCAL_TRANSPORT_INTERRUPTION')
                        return len(raw)
                    return original(raw)
                protocol.write = write
                return protocol, can_read, stderr
        expected = {k.encode(): v.encode() for k, v in request['remote_refs'].items()}
        ref, commit = request['ref'].encode(), request['commit'].encode()
        require(re.fullmatch(rb'refs/heads/qtrade-object-stage/[0-9a-f]{24}-[0-9]{4}', ref) and ref not in expected, 'WORKER_REF_BOUND')
        def update(refs):
            require(refs == expected, 'DULWICH_ADVERTISEMENT_CHANGED_NO_RETRY')
            return {ref: commit}
        def generate(have, want, **kwargs):
            require(set(have) == set(expected.values()) and set(want) == {commit}, 'DULWICH_HAVE_WANT_CHANGED')
            active[0] = True
            return len(records), iter(records)
        result = Client().send_pack(str(endpoint).encode(), update, generate, progress=lambda _: None)
        require(not request['suppress_ack'] and result.ref_status == {ref: None}
            and result.refs == {ref: commit}, 'DULWICH_ACK_NOT_CONFIRMED_NO_RETRY')
    with Repo(str(endpoint)) as repo:
        after = {k: v for k, v in repo.get_refs().items() if k.startswith(b'refs/')}
    require(after == {**expected, ref: commit}, 'DULWICH_AFTER_REFS_CHANGED')
    print(canonical({'state': 'ACK_CONFIRMED_LOCAL_ONLY', 'ref': ref.decode(), 'commit': commit.decode(),
        'pack_sha256': sha(pack), 'pack_bytes': len(pack)}).decode())


if __name__ == '__main__':
    require(len(sys.argv) == 3 and sys.argv[1] == '--local-worker' and os.name == 'posix', 'LOCAL_WORKER_ONLY')
    local_dulwich_worker(Path(sys.argv[2]))
