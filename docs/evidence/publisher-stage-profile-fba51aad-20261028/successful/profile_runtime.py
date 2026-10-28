"""Low-overhead diagnostic spans around original functions, never validation bypass."""
import functools
import inspect
import os
from pathlib import Path
import sqlite3
import threading
import time


class Profiler:
    def __init__(self):
        self.local = threading.local()
        self.threads = {}
        self.lock = threading.Lock()

    def state(self):
        if not hasattr(self.local, 'state'):
            value = {'name': threading.current_thread().name, 'stack': [], 'labels': {}}
            self.local.state = value
            with self.lock:
                self.threads[threading.get_ident()] = value
        return self.local.state

    def begin(self, label):
        state = self.state()
        frame = [label, time.perf_counter_ns(), time.thread_time_ns(), 0, 0]
        state['stack'].append(frame)
        return state, frame

    def end(self, token):
        wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
        state, frame = token
        assert state['stack'].pop() is frame
        elapsed, used = wall-frame[1], cpu-frame[2]
        row = state['labels'].setdefault(frame[0], [0, 0, 0, 0, 0])
        row[0] += 1
        row[1] += elapsed
        row[2] += max(0, elapsed-frame[3])
        row[3] += used
        row[4] += max(0, used-frame[4])
        if state['stack']:
            state['stack'][-1][3] += elapsed
            state['stack'][-1][4] += used

    def call(self, label, fn, *args, **kwargs):
        token = self.begin(label)
        try:
            return fn(*args, **kwargs)
        finally:
            self.end(token)

    def wrap(self, holder, name, label, generator=False):
        original = getattr(holder, name)
        if generator:
            @functools.wraps(original)
            def wrapped(*args, **kwargs):
                return ActiveIterator(self, label, original(*args, **kwargs))
        else:
            @functools.wraps(original)
            def wrapped(*args, **kwargs):
                return self.call(label, original, *args, **kwargs)
        setattr(holder, name, wrapped)
        return original

    def report(self):
        names = ('calls', 'inclusive_wall_seconds', 'exclusive_wall_seconds',
                 'inclusive_thread_cpu_seconds', 'exclusive_thread_cpu_seconds')
        result = []
        for identity, state in self.threads.items():
            assert not state['stack']
            result.append({'thread_id': identity, 'thread_name': state['name'],
                'stages': {label: dict(zip(names, [values[0], *[v/1e9 for v in values[1:]]]))
                           for label, values in state['labels'].items()}})
        return result


class ActiveIterator:
    """Time only active next/close segments; suspended generator time is excluded."""
    def __init__(self, profiler, label, iterator):
        self.profiler, self.label, self.iterator = profiler, label, iter(iterator)

    def __iter__(self):
        return self

    def __next__(self):
        return self.profiler.call(self.label, next, self.iterator)

    def close(self):
        method = getattr(self.iterator, 'close', None)
        if method is not None:
            return self.profiler.call(self.label+'.close', method)


def calibrate():
    profiler = Profiler()
    def noop():
        return None
    count = 20000
    started = time.perf_counter_ns()
    for _ in range(count):
        noop()
    plain = time.perf_counter_ns()-started
    started = time.perf_counter_ns()
    for _ in range(count):
        profiler.call('noop', noop)
    wrapped = time.perf_counter_ns()-started
    return {'calls': count, 'plain_seconds': plain/1e9, 'wrapped_seconds': wrapped/1e9,
            'approx_extra_nanoseconds_per_span': (wrapped-plain)/count,
            'not_subtracted_from_actual_measurements': True}


def install(profiler, *, sqlite_instrumentation=True):
    from scripts import publish_unified_market as market
    from scripts import unified_store as store
    from scripts import unified_market_checkpoint as checkpoint
    from qtrade.research import market_validation_receipts as receipts
    from qtrade.research import unified_market as schema
    import pyarrow.parquet as pq

    for holder, name, label in [
        (market, '_chunks', 'metadata.selected_chunks_sqlite_and_materialization'),
        (market, '_verify_selected_snapshot', 'metadata.final_snapshot_comparison'),
        (market, '_load', 'source.batch_admission_and_reuse_total'),
        (market, '_replay', 'source.original_raw_semantic_replay'),
        (market, '_persist_objects', 'pipeline.object_dispatch_including_source_and_wait'),
        (market, '_write_object', 'object.immutable_write_link_flush_total'),
        (market, '_worker_inventory', 'source.worker_inventory_hash'),
        (market, 'file_hash', 'physical.file_hash_read_total'),
        (market._SourceReads, 'material', 'source.prefetch_material_total'),
        (store, '_read_bytes', 'physical.source_read_with_accounting'),
        (store, 'sha256', 'physical.source_sha256_constructor'),
        (store.UnifiedBatchStore, 'read_blob', 'physical.blob_read_and_hash_total'),
        (store._BatchMaterial, 'validate', 'decode.material_validation_total'),
        (store._BatchMaterial, '_parquet', 'decode.parquet_metadata_open'),
        (store._BatchMaterial, 'check_export_bounds', 'decode.parquet_bounds'),
        (store, '_decode_captures', 'decode.gzip_raw_capture_json_and_body_hash'),
        (store, '_export_rows', 'decode.arrow_validation_and_numpy_rows'),
        (pq.ParquetFile, 'read', 'decode.parquet_file_read'),
        (receipts.MarketValidationReceiptStore, 'get', 'receipt.get_total'),
        (receipts.MarketValidationReceiptStore, '_records', 'receipt.records_sqlite_nfs_validation_total'),
        (receipts.MarketValidationReceiptStore, 'put_success', 'receipt.put_success_total'),
        (receipts.MarketValidationReceiptStore, '_insert', 'receipt.insert_transaction_total'),
        (receipts.MarketValidationReceiptStore, '_write_sidecar', 'receipt.nfs_sidecar_write_total'),
        (receipts, '_result', 'receipt.result_schema_and_row_hash'),
        (receipts, '_decode', 'receipt.json_decode_and_schema_validation'),
        (receipts, 'canonical_json', 'receipt.canonical_json_tree_validation_total'),
        (receipts, '_encode_json', 'receipt.json_encode'),
        (receipts, 'sha256', 'receipt.payload_sha256_constructor'),
        (checkpoint, 'load', 'generation.checkpoint_read_and_validation'),
        (checkpoint, 'prepare', 'generation.prepare_write_and_durability_total'),
        (checkpoint, 'fingerprints', 'generation.pair_fingerprints'),
        (checkpoint, 'identity', 'generation.canonical_identity'),
        (market._ShardEncoders, '__init__', 'shard.worker_start_and_handshake'),
        (market._ShardEncoders, 'pump', 'shard.worker_pipe_pump_wait_and_response'),
        (market._ShardEncoders, 'response', 'shard.worker_response_decode'),
        (market._ShardEncoders, 'close', 'shard.worker_join_cleanup'),
        (market, '_worker_compact', 'shard.pipe_protocol_json_encode'),
        (schema, 'encoded', 'public.json_encode'),
    ]:
        profiler.wrap(holder, name, label)
    # Preserve aliases, including original child validation functions.
    # This exact validator's code filename is an explicit source-origin guard.
    # Leave both aliases untouched; the child measures job boundaries instead.
    profiler.wrap(schema, 'validate_index', 'generation.index_schema_validation')
    market.validate_index = schema.validate_index
    market.encoded = schema.encoded
    profiler.wrap(market, '_encode_shards', 'pipeline.encode_generator_active_including_source', generator=True)
    atomic = market.write_json_atomic
    @functools.wraps(atomic)
    def atomic_write(path, *args, **kwargs):
        label = 'generation.index_atomic_write' if path.name == 'unified-market.json' else 'generation.state_atomic_write'
        return profiler.call(label, atomic, path, *args, **kwargs)
    market.write_json_atomic = atomic_write

    original_fsync = os.fsync
    @functools.wraps(original_fsync)
    def fsync(fd):
        stack = profiler.state()['stack']
        labels = [frame[0] for frame in stack]
        category = ('receipt' if any(x.startswith('receipt.') for x in labels) else
                    'object' if any(x.startswith('object.') for x in labels) else
                    'generation' if any(x.startswith('generation.') for x in labels) else 'other')
        return profiler.call(category+'.fsync_syscall', original_fsync, fd)
    os.fsync = fsync

    original_open = Path.open
    class ReceiptReader:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            self.handle.__enter__()
            return self
        def __exit__(self, *args):
            return self.handle.__exit__(*args)
        def read(self, *args, **kwargs):
            return profiler.call('receipt.nfs_bounded_read', self.handle.read, *args, **kwargs)
        def __getattr__(self, name):
            return getattr(self.handle, name)
    @functools.wraps(original_open)
    def path_open(path, mode='r', *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        if mode == 'rb' and path.suffix == '.json' and 'receipts' in path.parts:
            return ReceiptReader(handle)
        return handle
    Path.open = path_open

    if sqlite_instrumentation:
        original_connect = sqlite3.connect
        class Cursor(sqlite3.Cursor):
            def execute(self, *args, **kwargs):
                return profiler.call(self.connection.stage+'.execute', super().execute, *args, **kwargs)
            def fetchall(self):
                return profiler.call(self.connection.stage+'.fetchall', super().fetchall)
        class Connection(sqlite3.Connection):
            stage = 'sqlite.unknown'
            def execute(self, *args, **kwargs):
                return self.cursor(factory=Cursor).execute(*args, **kwargs)
            def executescript(self, *args, **kwargs):
                return profiler.call(self.stage+'.executescript', super().executescript, *args, **kwargs)
        @functools.wraps(original_connect)
        def connect(database, *args, **kwargs):
            assert 'factory' not in kwargs
            value = original_connect(database, *args, factory=Connection, **kwargs)
            value.stage = 'sqlite.metadata' if 'frozen.sqlite' in str(database) else 'sqlite.receipts'
            return value
        sqlite3.connect = connect
    return market
