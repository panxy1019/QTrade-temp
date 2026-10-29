"""Additional proposal faults on temporary stores and a local bare Git remote."""
from __future__ import annotations

import errno
import json
import os
import sqlite3
import subprocess
import time
from datetime import timedelta
from pathlib import Path

import pytest
from test_unified_market import NOW
from test_unified_market import source as source_fixture

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts import publish_research_snapshot as snapshot
from scripts import publish_unified_market as exporter
from scripts import unified_market_checkpoint as checkpoints

source = source_fixture
pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX publication fault contract')


def run(source, **kwargs):
    base, database, *_ = source
    return exporter.export_market(base, database,
        now=kwargs.pop('now', NOW), receipt_database=base/'local/receipts.sqlite',
        checkpoint_root=base/'checkpoints', **kwargs)


def minute_entry(result):
    return next(row for row in result['index']['entries']
                if row['symbol'] == '301001' and row['frequency'] == '5min')


def test_historical_backfill_rebuilds_complete_window_and_keeps_both_sources(source):
    base, *_rest, add = source
    add(counter=1, minute=40)
    first = run(source)
    add(counter=2, minute=35)
    second = run(source, now=NOW+timedelta(seconds=1))
    before, after = minute_entry(first), minute_entry(second)
    assert after['state'] == 'AVAILABLE' and after['shards'] != before['shards']
    ref, = after['shards']
    raw = (base/'unified-data/market-public/unified-market/objects'/
           (ref['sha256']+'.json')).read_bytes()
    shard = json.loads(raw)
    assert [row['timestamp'][-14:-6] for row in shard['bars']] == ['09:35:00', '09:40:00']
    assert {row['batch_id'] for row in shard['source_batches']} == {f'{1:032x}', f'{2:032x}'}
    assert second['statistics']['receipt_hits'] == 1
    assert second['statistics']['replayed_batches'] == 1


def test_quarantine_release_revalidates_restored_source_without_altering_old_generation(source):
    base, database, *_rest, add = source
    add()
    original = run(source)
    generation = base/'checkpoints/generations'/(original['index']['evidence_sha256']+'.json')
    frozen = generation.read_bytes()
    with sqlite3.connect(database) as db:
        db.execute('INSERT INTO excluded_collection_chunks VALUES(?,?)', (f'{1:032x}', '301001'))
    excluded = run(source, now=NOW+timedelta(seconds=1))
    assert minute_entry(excluded)['state'] == 'NO_DATA'
    with sqlite3.connect(database) as db:
        db.execute('DELETE FROM excluded_collection_chunks WHERE batch_id=? AND symbol=?',
                   (f'{1:032x}', '301001'))
    restored = run(source, now=NOW+timedelta(seconds=2))
    assert minute_entry(restored) == minute_entry(original)
    assert generation.read_bytes() == frozen
    assert restored['statistics']['reused_pairs'] == 0


def test_enospc_during_new_object_write_preserves_committed_head(source, monkeypatch):
    base, *_rest, add = source
    add()
    run(source)
    head = base/'unified-data/market-public/unified-market.json'
    old = head.read_bytes()
    add(counter=2, minute=40, close='11')

    def disk_full(*args, **kwargs):
        raise OSError(errno.ENOSPC, 'isolated object-write disk-full injection')

    monkeypatch.setattr(exporter, 'NamedTemporaryFile', disk_full)
    with pytest.raises(OSError) as rejected:
        run(source, now=NOW+timedelta(seconds=1))
    assert rejected.value.errno == errno.ENOSPC
    assert head.read_bytes() == old


def test_slow_generation_io_exceeds_real_deadline_without_moving_head(source, monkeypatch):
    base, *_rest, add = source
    add()
    run(source)
    head = base/'unified-data/market-public/unified-market.json'
    before = head.read_bytes()
    prepare = checkpoints.prepare
    called = []

    def slow(*args, **kwargs):
        value = prepare(*args, **kwargs)
        called.append(value)
        time.sleep(1.15)  # Real elapsed latency, not an altered monotonic clock.
        return value

    monkeypatch.setattr(checkpoints, 'prepare', slow)
    with pytest.raises(ValueError, match='TIME_BUDGET'):
        run(source, now=NOW+timedelta(seconds=1), seconds=1)
    assert called and head.read_bytes() == before


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE,
                                   text=True).strip()


def test_real_non_fast_forward_push_cannot_report_new_publication_success(tmp_path, monkeypatch):
    remote, publication, other = (tmp_path/name for name in ('remote.git', 'publication', 'other'))
    subprocess.run(['git', 'init', '--bare', '--initial-branch=main', str(remote)],
                   check=True, capture_output=True)
    subprocess.run(['git', 'clone', str(remote), str(publication)], check=True,
                   capture_output=True)
    for root in (publication,):
        git(root, 'config', 'user.name', 'isolated-fixture')
        git(root, 'config', 'user.email', 'fixture@example.invalid')
    (publication/'seed.txt').write_text('seed\n')
    git(publication, 'add', 'seed.txt')
    git(publication, 'commit', '-m', 'fixture seed')
    git(publication, 'push', 'origin', 'HEAD:refs/heads/main')
    subprocess.run(['git', 'clone', str(remote), str(other)], check=True, capture_output=True)
    git(other, 'config', 'user.name', 'isolated-fixture')
    git(other, 'config', 'user.email', 'fixture@example.invalid')
    (other/'rival.txt').write_text('different writer\n')
    git(other, 'add', 'rival.txt')
    git(other, 'commit', '-m', 'fixture independent publication')
    git(other, 'push', 'origin', 'HEAD:refs/heads/main')
    actual_remote_head = git(remote, 'rev-parse', 'refs/heads/main')
    original_output = subprocess.check_output

    def checked_output(arguments, **kwargs):
        if arguments == ['git', '-C', str(publication), 'remote', 'get-url', 'origin']:
            # Isolate the destination identity check; all Git writes/push are real
            # operations against the temporary local bare remote, never GitHub.
            return 'ssh://git@ssh.github.com:443/panxy1019/QTrade-observations.git\n'
        return original_output(arguments, **kwargs)

    monkeypatch.setattr(snapshot.subprocess, 'check_output', checked_output)
    monkeypatch.setattr(snapshot, 'build_snapshot', lambda *_: signed({**AUTHORITY,
        'published_at': NOW.isoformat(), 'datasets': {}}))
    root = tmp_path/'data/observer'
    root.mkdir(parents=True)
    previous = b'{"outcome":"SUCCEEDED","published_at":"OLDER_SUCCESS"}\n'
    state = root/'publication-state.json'
    state.write_bytes(previous)
    with pytest.raises(subprocess.CalledProcessError):
        snapshot.publish(root, tmp_path/'experiments', publication)
    assert git(remote, 'rev-parse', 'refs/heads/main') == actual_remote_head
    assert git(publication, 'rev-parse', 'HEAD') != actual_remote_head
    assert state.read_bytes() == previous
