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
code = 1
try:
    code = profiler.call('worker.full_protocol', market._shard_worker)
finally:
    with (root/(phase+'-worker-'+str(os.getpid())+'.json')).open('x') as handle:
        json.dump({'kind': 'DIAGNOSTIC_OWNED_SHARD_WORKER_TIMERS', 'pid': os.getpid(),
                   'phase': phase, 'exit_code': code, 'argv': sys.argv,
                   'original_function': 'scripts.publish_unified_market._shard_worker',
                   'threads': profiler.report(), 'profiling_enabled': True,
                   'production_performance_acceptance': False}, handle, sort_keys=True)
raise SystemExit(code)
