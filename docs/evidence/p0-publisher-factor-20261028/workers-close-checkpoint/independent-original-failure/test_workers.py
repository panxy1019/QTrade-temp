"""Independent POSIX CPU-only worker protocol tests; no lake/index/production controls."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import publish_unified_market as p
from unittest.mock import patch
PROTOTYPE = Path(p.__file__)
class ReviewEncoders(p._ShardEncoders):
    def __init__(self, calendar, clock, deadline, command=None):
        if command is None:
            super().__init__(calendar, clock, deadline)
        else:
            launch = p.subprocess.Popen
            def fixture_launch(_command, *args, **kwargs):
                return launch(command, *args, **kwargs)
            with patch.object(p.subprocess, 'Popen', fixture_launch):
                super().__init__(calendar, clock, deadline)
    def encode(self, shards):
        yield from (raw for value, raw in super().encode(shards))
p.Encoders = ReviewEncoders
pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Actual POSIX pipe/select/owned-process test')
REAL_POPEN = subprocess.Popen


def old_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def inputs():
    baseline = Path(os.environ['QTRADE_REVIEW_BASELINE'])
    old_parser = old_module('review_original_worker_parser', baseline/'src/qtrade/market_data/sina_incremental.py')
    original = old_module('review_original_worker_contract', baseline/'src/qtrade/research/unified_market.py')
    original.completed_klines = old_parser.completed_klines
    calendar = p.Calendar(p.REPO/'configs/observer_calendar_2026.json')
    clock = datetime(2026, 10, 28, 6, tzinfo=UTC)
    bar = {'symbol':'301001','exchange':'SZSE','frequency':'5min',
           'timestamp':'2026-10-26T09:35:00+08:00','trading_day':'2026-10-26',
           'open':'10','high':'12','low':'9','close':'10','volume':'100',
           'amount':None,'vwap':None,'amount_basis':'VENDOR_CLAIM_UNVERIFIED',
           'price_basis':'SINA_NATIVE_ADJUSTMENT_UNCERTIFIED','historical_available_at':None}
    shard = p.signed({**p.AUTHORITY,'schema_version':'qtrade.unified-market-shard.v1',
        'universe_version':'1'*64,'symbol':'301001','security_type':'STOCK',
        'instrument_id':'SZSE.301001','frequency':'5min','price_basis':bar['price_basis'],
        'bars':[bar], 'source_batches':[{'batch_id':'2'*32,'manifest_sha256':'3'*64,
                                        'raw_sha256':'4'*64,'normalized_sha256':'5'*64}]})
    return calendar, clock, shard, original


@pytest.fixture
def pools():
    retained = []
    yield retained
    # Safety cleanup uses only handles created/retained by these fixture tests.
    for pool in retained:
        try:
            pool.close(False)
        finally:
            for state in pool.workers:
                proc = state['process']
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        proc.kill(); proc.wait(timeout=1)


def opened(inputs, pools, command=None):
    calendar, clock, *_ = inputs
    pool = p.Encoders(calendar, clock, time.monotonic()+15, command=command)
    pools.append(pool)
    assert len(pool.workers) == 2
    return pool


def reaped(pool):
    assert pool.finished
    assert all(state['process'].poll() is not None for state in pool.workers)
    assert all(state['process'].stdin.closed and state['process'].stdout.closed for state in pool.workers)


def test_real_workers_match_original458_bytes_and_insertion_order(inputs, pools):
    calendar, clock, shard, original = inputs
    reverse = {key:value for key,value in reversed(list(shard.items()))}
    reverse['bars'] = [{key:value for key,value in reversed(list(shard['bars'][0].items()))}]
    expected = [original.encoded(original.validate_shard(value, calendar, clock)) for value in [shard, reverse]]
    pool = opened(inputs, pools)
    assert list(pool.encode(iter([shard, reverse]*5))) == expected*5
    reaped(pool)
    assert all(state['process'].returncode == 0 for state in pool.workers)


@pytest.mark.parametrize('damage', ['ohlc', 'source', 'duplicate'])
def test_real_validation_error_matches_original458_and_reaps(inputs, pools, damage):
    calendar, clock, shard, original = inputs
    shard = copy.deepcopy(shard)
    if damage == 'ohlc':
        shard['bars'][0]['high'] = '8'
    elif damage == 'source':
        shard['source_batches'][0]['raw_sha256'] = 'bad'
    else:
        shard['bars'].append(copy.deepcopy(shard['bars'][0]))
    shard = p.signed({key:value for key,value in shard.items() if key != 'evidence_sha256'})
    with pytest.raises(ValueError) as old:
        original.validate_shard(copy.deepcopy(shard), calendar, clock)
    pool = opened(inputs, pools)
    with pytest.raises(ValueError) as actual:
        list(pool.encode(iter([shard])))
    assert str(actual.value) == str(old.value)
    reaped(pool)


PEER = r'''import importlib.util,json,os,signal,struct,sys,time
from pathlib import Path
path,mode,marker=sys.argv[1:]
s=importlib.util.spec_from_file_location('actual_frozen_worker_fixture',path)
m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)
out=sys.stdout.buffer
class Buffer:
 def __init__(self):self.data=bytearray();self.seen=False
 def write(self,data):
  self.data.extend(data)
  while len(self.data)>=4:
   n=struct.unpack('!I',self.data[:4])[0]
   if len(self.data)<4+n:break
   body=bytes(self.data[4:4+n]);del self.data[:4+n]
   if body[:1]==b'R' and mode=='handshake':body=b'R{}'
   if body[:1]==b'O' and not self.seen:
    self.seen=True
    if mode=='death':os._exit(7)
    if mode=='hang':
     signal.signal(signal.SIGTERM,signal.SIG_IGN)
     Path(marker+'-'+str(os.getpid())).write_text('TERM_IGNORED_FIXTURE')
     time.sleep(30)
    if mode=='oversize':out.write(struct.pack('!I',0xffffffff)+b'O');out.flush();time.sleep(30)
    if mode=='short':out.write(struct.pack('!I',n)+body[:10]);out.flush();os._exit(8)
    if mode=='wrongid':body=b'O'+struct.pack('!I',struct.unpack('!I',body[1:5])[0]+1)+body[5:]
    if mode=='evidence':
     value=json.loads(body[5:]);value['evidence_sha256']='f'*64
     body=body[:5]+json.dumps(value,indent=2).encode()+bytes([10])
    if mode=='unknown':body=b'X'+body[1:]
    if mode=='duplicate':
     packet=struct.pack('!I',len(body))+body
     out.write(packet+packet);out.flush();continue
   out.write(struct.pack('!I',len(body))+body);out.flush()
  return len(data)
 def flush(self):out.flush()
sys.stdout=type('FixtureStdout',(),{'buffer':Buffer()})()
raise SystemExit(m._shard_worker())
'''


def peer(tmp_path, mode):
    path = tmp_path/'fixture_peer.py'
    path.write_text(PEER, encoding='utf-8')
    return [sys.executable, '-B', str(path), str(PROTOTYPE), mode, str(tmp_path/'hang-ready')]


@pytest.mark.parametrize('mode', ['oversize', 'short', 'wrongid', 'evidence', 'unknown', 'duplicate', 'death'])
def test_bad_peer_response_aborts_and_reaps_owned_only(inputs, pools, tmp_path, mode):
    sentinel = REAL_POPEN([sys.executable, '-B', '-c', 'import time; time.sleep(30)'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        pool = opened(inputs, pools, peer(tmp_path, mode))
        with pytest.raises((ValueError, OSError)):
            list(pool.encode(iter([inputs[2]]*4)))
        reaped(pool)
        assert sentinel.poll() is None
    finally:
        sentinel.terminate(); sentinel.wait(timeout=2)


def test_bad_handshake_never_runs_jobs_and_reaps_started_children(inputs, tmp_path, monkeypatch):
    children = []
    def popen(*args, **kwargs):
        child = REAL_POPEN(*args, **kwargs); children.append(child); return child
    monkeypatch.setattr(p.subprocess, 'Popen', popen)
    calendar, clock, *_ = inputs
    with pytest.raises(ValueError):
        p.Encoders(calendar, clock, time.monotonic()+15, command=peer(tmp_path, 'handshake'))
    assert children and all(child.poll() is not None for child in children)


def test_second_spawn_failure_reaps_first_owned_child(inputs, monkeypatch):
    children = []
    def popen(*args, **kwargs):
        if children:raise OSError('FIXTURE_SECOND_SPAWN_FAILED')
        child = REAL_POPEN(*args, **kwargs); children.append(child); return child
    monkeypatch.setattr(p.subprocess, 'Popen', popen)
    calendar, clock, *_ = inputs
    with pytest.raises(OSError, match='FIXTURE_SECOND_SPAWN_FAILED'):
        p.Encoders(calendar, clock, time.monotonic()+15)
    assert len(children) == 1 and children[0].poll() is not None


def test_deadline_kills_term_ignoring_owned_workers_and_preserves_unrelated(inputs, pools, tmp_path):
    sentinel = REAL_POPEN([sys.executable, '-B', '-c', 'import time; time.sleep(30)'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        pool = opened(inputs, pools, peer(tmp_path, 'hang'))
        pool.deadline = time.monotonic()+.3
        started = time.monotonic()
        with pytest.raises(ValueError, match='TIME_BUDGET'):
            list(pool.encode(iter([inputs[2]]*2)))
        assert time.monotonic()-started < 2
        assert list(tmp_path.glob('hang-ready-*'))
        reaped(pool)
        assert any(state['process'].returncode == -9 for state in pool.workers)
        assert sentinel.poll() is None
    finally:
        sentinel.terminate(); sentinel.wait(timeout=2)


def test_input_generator_close_fault_still_reaps_workers(inputs, pools):
    pool = opened(inputs, pools)
    def shards():
        try:
            while True:yield inputs[2]
        finally:
            raise OSError('FIXTURE_INPUT_CLOSE_FAILED')
    output = pool.encode(shards())
    next(output)
    with pytest.raises(OSError, match='FIXTURE_INPUT_CLOSE_FAILED'):
        output.close()
    reaped(pool)


def test_mutation_after_submission_is_bound_to_serialized_snapshot_and_decimal_flags(inputs, pools):
    from decimal import Inexact, ROUND_DOWN, localcontext
    calendar, clock, shard, original = inputs
    original_snapshot = copy.deepcopy(shard)
    changed = copy.deepcopy(shard)
    changed['bars'][0]['close'] = '11'
    changed = p.signed({key:value for key,value in changed.items() if key != 'evidence_sha256'})
    def causal_source():
        yield shard
        shard.clear()
        shard.update(copy.deepcopy(changed))
        yield shard
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        context.flags[Inexact] = True
        flags = context.flags.copy()
        expected = [original.encoded(original.validate_shard(value, calendar, clock))
                    for value in [original_snapshot, changed]]
        pool = opened(inputs, pools)
        assert list(pool.encode(causal_source())) == expected
        assert context.prec == 3 and context.rounding == ROUND_DOWN
        assert context.traps[Inexact] and context.flags == flags
    reaped(pool)


# Import the original two-target lake fixture. The wrappers below force the
# production export entry point to use actual owned child processes.
sys.path.insert(0, str(p.REPO/'tests'))
import test_unified_market as repository_tests
source = repository_tests.source


@pytest.fixture
def integrated(source, monkeypatch):
    owned = []
    monkeypatch.setattr(p, '_SHARD_WORKER_MIN_TARGETS', 0)
    def launch(*args, **kwargs):
        assert args[0] == [sys.executable, '-B', '-m', 'scripts.publish_unified_market',
                           '--private-shard-worker']
        assert kwargs['cwd'] == p.REPO
        assert kwargs['env']['LIVE_TRADING_ENABLED'] == 'false'
        child = REAL_POPEN(*args, **kwargs)
        owned.append(child)
        return child
    monkeypatch.setattr(p.subprocess, 'Popen', launch)
    yield owned
    assert owned and len(owned) % 2 == 0, 'MUST_EXERCISE_REAL_TWO_CHILD_EXPORT'
    assert all(child.poll() is not None for child in owned)
    assert not p._UNREAPED_SHARD_WORKERS
    import threading
    assert not any(t.name.startswith(('market-source', 'market-object'))
                   for t in threading.enumerate())


def test_integrated_export_original458_bytes_all_targets_and_join_before_pointer(
        source, integrated, monkeypatch, inputs):
    base, database, calendar, master, store, add = source
    add(); add('day', counter=2); add(symbol='600036', counter=3)
    monkeypatch.setattr(p, '_SHARD_WORKER_MIN_TARGETS', 99)
    expected = repository_tests.export(source)
    assert integrated == []
    directory = base/'unified-data/market-public/unified-market/objects'
    objects = {f.name: f.read_bytes() for f in directory.glob('*.json')}
    monkeypatch.setattr(p, '_SHARD_WORKER_MIN_TARGETS', 0)
    atomic = p.write_json_atomic
    def write(path, value, **kwargs):
        if path.name == 'unified-market.json':
            assert len(integrated) == 2 and all(c.poll() == 0 for c in integrated)
        return atomic(path, value, **kwargs)
    monkeypatch.setattr(p, 'write_json_atomic', write)
    actual = repository_tests.export(source)
    assert actual['index'] == expected['index']
    assert actual['statistics']['cached_batches'] == 3
    assert {f.name:f.read_bytes() for f in directory.glob('*.json')} == objects
    original = inputs[3]
    for entry in actual['index']['entries']:
        for ref in entry['shards']:
            raw = objects[ref['sha256']+'.json']
            shard = json.loads(raw)
            assert original.encoded(original.validate_shard(copy.deepcopy(shard), calendar,
                                    repository_tests.NOW)) == raw
            assert ref == {'sha256': p.sha256(raw).hexdigest(), 'bytes':len(raw),
                'evidence_sha256':shard['evidence_sha256'], 'count':len(shard['bars']),
                'start':shard['bars'][0]['timestamp'], 'end':shard['bars'][-1]['timestamp']}


@pytest.mark.parametrize('mode', ['oversize', 'short', 'wrongid', 'evidence', 'unknown',
                                  'duplicate', 'death', 'handshake'])
def test_integrated_bad_peer_retains_pointer_state_and_reaps_owned_only(
        source, integrated, monkeypatch, tmp_path, mode):
    base, _, _, _, _, add = source
    add()
    monkeypatch.setattr(p, '_SHARD_WORKER_MIN_TARGETS', 99)
    repository_tests.export(source)
    pointer = base/'unified-data/market-public/unified-market.json'
    state = base/'unified-data/market-export-state.json'
    before = pointer.read_bytes(), state.read_bytes(), pointer.stat().st_mtime_ns
    add('day', counter=2); add(symbol='600036', counter=3)
    command = peer(tmp_path, mode)
    sentinel = REAL_POPEN([sys.executable, '-B', '-c', 'import time;time.sleep(30)'],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    def launch(_command, *args, **kwargs):
        child = REAL_POPEN(command, *args, **kwargs); integrated.append(child); return child
    monkeypatch.setattr(p.subprocess, 'Popen', launch)
    monkeypatch.setattr(p, '_SHARD_WORKER_MIN_TARGETS', 0)
    start = time.monotonic()
    try:
        with pytest.raises((ValueError, OSError)):
            repository_tests.export(source)
        assert time.monotonic()-start < 5
        assert len(integrated) == 2 and all(c.poll() is not None for c in integrated)
        assert (pointer.read_bytes(), state.read_bytes(), pointer.stat().st_mtime_ns) == before
        assert sentinel.poll() is None
    finally:
        sentinel.terminate(); sentinel.wait(timeout=2)


@pytest.mark.parametrize('damage', ['late_raw_failure', 'late_conflict'])
def test_integrated_complete_pair_failure_gate(source, integrated, monkeypatch, damage):
    repository_tests.test_pair_is_not_written_before_its_last_frozen_batch_is_qualified(
        source, monkeypatch, damage)


def test_integrated_whole_batch_gate(source, integrated, monkeypatch):
    repository_tests.test_entire_batch_failure_is_marked_before_any_selected_pair_can_seal(
        source, monkeypatch)


def test_integrated_repeated_chunks_are_counted(source, integrated):
    repository_tests.test_pair_completion_counts_every_repeated_selected_chunk_without_deduplication(source)


@pytest.mark.parametrize('damage', ['conflict', 'excluded', 'not_complete', 'raw', 'ledger'])
def test_integrated_selected_metadata_and_raw_gate(source, integrated, damage):
    repository_tests.test_unqualified_chunks_never_create_bars_and_conflicts_are_quarantined(
        source, damage)


@pytest.mark.parametrize('field', ['close', 'amount', 'source_hashes', 'raw_pack_sha256'])
def test_integrated_raw_replay_gate(source, integrated, field):
    repository_tests.test_hash_valid_normalized_forgery_is_rejected_by_raw_replay(source, field)


@pytest.mark.parametrize(('field', 'invalid'), [('high', '8'), ('close', 'NaN'),
                                              ('volume', '-1'), ('vwap', '99')])
def test_integrated_cached_quality_gate(source, integrated, field, invalid):
    repository_tests.test_invalid_retained_cached_row_fails_entire_export_and_retains_last_success(
        source, field, invalid)
