"""Immutable materialized-view checkpoints; the public index is the commit pointer."""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from qtrade.data.storage import _fsync_directory
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.unified_market import INDEX_LIMIT
from scripts.atomic_json import write_json_immutable

SCHEMA = 'qtrade.unified-market-checkpoint.v1'
MAX_BYTES = INDEX_LIMIT*3


def _require_local_database(path: Path) -> None:
    target = path.resolve()
    points = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split()
        divider = fields.index('-')
        mount = Path(fields[4].replace('\\040', ' '))
        if target.is_relative_to(mount):
            points.append((len(str(mount)), fields[divider+1]))
    if not points or max(points)[1] in {'nfs', 'nfs4', 'cifs', 'smb3', '9p', 'ceph',
                                      'fuse.sshfs', 'fuse.glusterfs'}:
        raise ValueError('UNIFIED_MARKET_CHECKPOINT_LOCAL_DATABASE_REQUIRED')


def identity(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@contextmanager
def publication_lease(database: Path) -> Iterator[None]:
    """Hold a same-host lease throughout source selection and public-head replacement."""
    if os.name != 'posix':
        raise ValueError('UNIFIED_MARKET_CHECKPOINT_POSIX_REQUIRED')
    import fcntl
    _require_local_database(database)
    if database.is_symlink() or database.parent.is_symlink():
        raise ValueError('UNIFIED_MARKET_CHECKPOINT_LOCAL_PATH_INVALID')
    database.parent.mkdir(parents=True, exist_ok=True)
    path = database.with_suffix('.publication.lock')
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('UNIFIED_MARKET_CHECKPOINT_OWNER_BUSY') from error
        yield
    finally:
        os.close(descriptor)


def fingerprints(chunks: list[dict[str, Any]], targets: list[dict[str, Any]],
                 context: str, windows: dict[str, list[str]]) -> dict[str, str]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in chunks:
        grouped.setdefault(row['symbol']+':'+row['frequency'], []).append(row)
    return {target['symbol']+':'+freq: identity({
        'context': context, 'target': target, 'window': windows[freq],
        'chunks': sorted(grouped.get(target['symbol']+':'+freq, []),
                         key=lambda row: (row['batch_id'], row['symbol']))})
        for target in targets for freq in ('day', '5min')}


def load(root: Path, current: Path, context: str) -> dict[str, Any] | None:
    """Only reuse the checkpoint bound to the actual durable public index."""
    try:
        if root.is_symlink() or (root/'generations').is_symlink():
            return None
        if current.is_symlink() or current.stat().st_size > INDEX_LIMIT:
            return None
        index = json.loads(current.read_bytes())
        index_id = index['evidence_sha256']
        if not isinstance(index_id, str) or len(index_id) != 64 or any(
                c not in '0123456789abcdef' for c in index_id):
            return None
        path = root/'generations'/(index_id+'.json')
        if path.is_symlink() or path.stat().st_size > MAX_BYTES:
            return None
        document = json.loads(path.read_bytes())
        if (set(document) != set(AUTHORITY) | {'schema_version', 'context_sha256',
                'index', 'fingerprints', 'parent_index_id', 'created_at', 'evidence_sha256'}
                or document['schema_version'] != SCHEMA
                or any(document.get(k) != v for k, v in AUTHORITY.items())
                or document['context_sha256'] != context
                or document['index'] != index
                or signed({k: v for k, v in document.items() if k != 'evidence_sha256'})
                   != document
                or signed({k: v for k, v in index.items() if k != 'evidence_sha256'}) != index):
            return None
        if not isinstance(document['fingerprints'], dict) or any(
                not isinstance(k, str) or not isinstance(v, str) or len(v) != 64
                or any(c not in '0123456789abcdef' for c in v)
                for k, v in document['fingerprints'].items()):
            return None
        return cast(dict[str, Any], document)
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return None


def prepare(root: Path, index: dict[str, Any], context: str, pairs: dict[str, str],
            parent_index_id: str | None, now: datetime) -> str:
    """Persist the complete immutable candidate before its public pointer can move."""
    if root.is_symlink() or (root/'generations').is_symlink():
        raise ValueError('UNIFIED_MARKET_CHECKPOINT_PATH_INVALID')
    document = signed({**AUTHORITY, 'schema_version': SCHEMA,
        'context_sha256': context, 'index': index, 'fingerprints': pairs,
        'parent_index_id': parent_index_id, 'created_at': now.isoformat()})
    path = root/'generations'/(index['evidence_sha256']+'.json')
    if path.exists():
        if path.is_symlink() or path.stat().st_size > MAX_BYTES:
            raise ValueError('UNIFIED_MARKET_CHECKPOINT_CONFLICT')
        existing = json.loads(path.read_bytes())
        if existing != document and not (parent_index_id == index['evidence_sha256']
                and all(existing.get(k) == document[k] for k in document if k not in {
                    'evidence_sha256', 'parent_index_id'})):
            raise ValueError('UNIFIED_MARKET_CHECKPOINT_CONFLICT')
        document = existing
    else:
        write_json_immutable(path, document)
    # A prior attempt may have linked the object but failed a directory fsync.
    for directory in (path.parent, root, root.parent):
        _fsync_directory(directory)
    return str(document['evidence_sha256'])
