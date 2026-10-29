"""Bounded, read-only live Git phase samples; never invokes push or packing."""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
PID = 647839
TICKS = '1022098698'
OLD_ROOT = str(BASE / 'releases/b2386c3e53c766d9e9512e6ca0f5116583aa13c2')


def proc():
    all_processes = {}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
            args = [x for x in (path / 'cmdline').read_bytes().split(b'\0') if x]
            all_processes[int(path.name)] = (path, fields, args)
        except (FileNotFoundError, ProcessLookupError):
            continue
    assert all_processes[PID][1][19] == TICKS
    descendants = {PID}
    for _ in range(32):
        new = {p for p, (_, f, _) in all_processes.items() if int(f[1]) in descendants}
        if new <= descendants:
            break
        descendants |= new
    result = []
    for pid in sorted(descendants):
        path, fields, args = all_processes[pid]
        try:
            io = {k: int(v) for k, v in (line.split(':', 1) for line in (path / 'io').read_text().splitlines())}
            stage = next((x.decode() for x in (b'pack-objects', b'index-pack', b'send-pack', b'push', b'commit', b'add') if x in args), None)
            if b'--private-shard-worker' in args:
                stage = 'market_shard_worker'
            result.append({'pid': pid, 'ppid': int(fields[1]), 'pgrp': int(fields[2]),
                'session': int(fields[3]), 'start_ticks': fields[19], 'state': fields[0],
                'utime_ticks': int(fields[11]), 'stime_ticks': int(fields[12]), 'io': io,
                'executable': Path(os.fsdecode(args[0])).name if args else None, 'stage': stage,
                'wchan': (path / 'wchan').read_text().strip(),
                'cmdline_sha256': hashlib.sha256(b'\0'.join(args)).hexdigest()})
        except (FileNotFoundError, ProcessLookupError):
            continue
    old = [pid for pid, (_, _, args) in all_processes.items() if OLD_ROOT.encode() in b'\0'.join(args)]
    return result, old


def doc(path):
    raw = path.read_bytes()
    assert len(raw) < 4_000_000 and not path.is_symlink()
    return {'raw_sha256': hashlib.sha256(raw).hexdigest(), 'value': json.loads(raw)}


def main():
    assert os.environ.get('LIVE_TRADING_ENABLED') == 'false' and sys.dont_write_bytecode
    args = (Path('/proc') / str(PID) / 'cmdline').read_bytes().split(b'\0')
    root = Path(os.fsdecode(args[args.index(b'--root') + 1]))
    publication = Path(os.fsdecode(args[args.index(b'--publication') + 1]))
    assert root.is_relative_to(BASE) and publication.is_relative_to(BASE)
    for index in range(10):
        processes, old = proc()
        refs = subprocess.run(['git', '-C', str(publication), 'rev-parse', 'HEAD', 'refs/remotes/origin/main'],
            capture_output=True, env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}, timeout=10)
        assert refs.returncode == 0 and re.fullmatch(rb'[0-9a-f]{40}\n[0-9a-f]{40}\n', refs.stdout)
        print(json.dumps({'schema_version': 'qtrade.publisher-git-phase-readonly.v1', 'index': index,
            'at': datetime.now(timezone.utc).isoformat(), 'processes': processes, 'old_release_pids': old,
            'head': refs.stdout.decode().splitlines()[0], 'origin_main': refs.stdout.decode().splitlines()[1],
            'publication_state': doc(root / 'publication-state.json'),
            'export_attempt': doc(BASE / 'unified-data/market-export-attempt.json'),
            'production_mutations': False}), flush=True)
        if index < 9:
            time.sleep(5)


if __name__ == '__main__':
    main()
