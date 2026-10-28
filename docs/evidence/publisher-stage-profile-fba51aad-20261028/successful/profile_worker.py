"""Diagnostic entrypoint for the exact owned shard command only; stdout stays binary protocol."""
from pathlib import Path
import json
import os
import sys
from profile_runtime import Profiler, install

root, phase = Path(sys.argv[1]), sys.argv[2]
profiler = Profiler()
market = install(profiler, sqlite_instrumentation=False)
assert market.REPO == Path('/root/qtrade-local/operator-evidence/market-checkpoint-exact-git-20261028T105253Z/source')
assert Path(market._validated_shard_bytes.__code__.co_filename).resolve() == (
    market.REPO/'src/qtrade/research/unified_market.py').resolve()
# Keep the guarded validator function and its actual code filename intact.
# Time original worker processing after a complete J frame was read and before
# its O/E frame is encoded. Pipe waiting is excluded from this job interval.
original_read = market._read_worker_frame
original_frame = market._worker_frame
job_token = None
def read_frame(*args, **kwargs):
    global job_token
    body = profiler.call('worker.pipe_read', original_read, *args, **kwargs)
    if body is not None and body[:1] == b'J':
        assert job_token is None
        job_token = profiler.begin('shard.original_job_json_decode_validation_encode')
    return body
def write_frame(body, *args, **kwargs):
    global job_token
    if body[:1] in {b'O', b'E'} and job_token is not None:
        profiler.end(job_token)
        job_token = None
    return original_frame(body, *args, **kwargs)
market._read_worker_frame = read_frame
market._worker_frame = write_frame
code = 1
try:
    code = profiler.call('worker.full_protocol', market._shard_worker)
finally:
    with (root/(phase+'-worker-'+str(os.getpid())+'.json')).open('x') as handle:
        json.dump({'kind': 'DIAGNOSTIC_OWNED_SHARD_WORKER_TIMERS', 'pid': os.getpid(),
                   'phase': phase, 'exit_code': code, 'argv': sys.argv,
                   'original_function': 'scripts.publish_unified_market._shard_worker',
                   'guarded_validator_function_unchanged': True,
                   'job_timer_scope': 'After complete original J frame read to before original O/E frame encoding; includes original JSON decode and exact validator/encoder, excludes pipe wait.',
                   'threads': profiler.report(), 'profiling_enabled': True,
                   'production_performance_acceptance': False}, handle, sort_keys=True)
raise SystemExit(code)
