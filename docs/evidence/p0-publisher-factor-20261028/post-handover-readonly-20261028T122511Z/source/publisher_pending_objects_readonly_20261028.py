"""Inspect pending public Git objects without packing, committing, or pushing."""
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

PID = 647839
BASE = Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')


def run(repo, args, data=None):
    result = subprocess.run(['git', '-C', str(repo), *args], input=data, capture_output=True,
        env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'}, timeout=45)
    assert result.returncode == 0 and len(result.stdout) < 20_000_000
    return result.stdout


def main():
    assert os.environ.get('LIVE_TRADING_ENABLED') == 'false'
    args = (Path('/proc') / str(PID) / 'cmdline').read_bytes().split(b'\0')
    repo = Path(os.fsdecode(args[args.index(b'--publication') + 1]))
    assert repo.is_relative_to(BASE)
    head = run(repo, ['rev-parse', 'HEAD']).decode().strip()
    origin = run(repo, ['rev-parse', 'refs/remotes/origin/main']).decode().strip()
    url = run(repo, ['remote', 'get-url', 'origin']).strip()
    advertised = run(repo, ['ls-remote', '--refs', 'origin'])
    refs = []
    for line in advertised.splitlines():
        assert re.fullmatch(rb'[0-9a-f]{40}\trefs/[A-Za-z0-9_./-]+', line)
        revision, ref = line.decode().split('\t')
        refs.append({'revision': revision, 'ref': ref})
    pending = run(repo, ['rev-list', '--objects', '--no-object-names', 'HEAD', '--not', '--remotes'])
    ids = pending.splitlines()
    assert len(ids) < 100_000 and all(re.fullmatch(rb'[0-9a-f]{40}', x) for x in ids)
    sizes = run(repo, ['cat-file', '--batch-check=%(objectname) %(objecttype) %(objectsize) %(objectsize:disk)'], pending)
    by_type = {}
    identities = []
    for line in sizes.splitlines():
        identity, kind, raw_size, disk_size = line.decode().split()
        assert re.fullmatch(r'[0-9a-f]{40}', identity) and kind in ('blob', 'tree', 'commit', 'tag')
        item = by_type.setdefault(kind, {'count': 0, 'uncompressed_bytes': 0, 'existing_object_disk_bytes': 0})
        item['count'] += 1; item['uncompressed_bytes'] += int(raw_size); item['existing_object_disk_bytes'] += int(disk_size)
        identities.append({'sha1': identity, 'type': kind, 'bytes': int(raw_size), 'disk_bytes': int(disk_size)})
    assert len(identities) == len(ids)
    print(json.dumps({'schema_version': 'qtrade.publisher-pending-objects-readonly.v1',
        'observed_at': datetime.now(timezone.utc).isoformat(), 'publication_repository': str(repo),
        'publication_repository_path_sha256': hashlib.sha256(str(repo).encode()).hexdigest(),
        'remote_url_sha256': hashlib.sha256(url).hexdigest(), 'remote_url_bytes': len(url),
        'head': head, 'local_origin_main': origin, 'advertised_refs': refs,
        'advertisement_sha256': hashlib.sha256(advertised).hexdigest(),
        'pending_objects_sha256': hashlib.sha256(pending).hexdigest(), 'pending_count': len(ids),
        'by_type': by_type, 'objects': identities,
        'head_after': run(repo, ['rev-parse', 'HEAD']).decode().strip(),
        'size_scope': 'EXISTING_COMPRESSED_OBJECT_STORAGE_NOT_A_NEW_NEGOTIATED_WIRE_PACK',
        'production_mutations': False}, sort_keys=True))


if __name__ == '__main__':
    main()
