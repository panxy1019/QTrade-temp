"""One 120-second diagnostic pass in a fresh process, original export and validators."""
from pathlib import Path
from datetime import datetime, UTC
from hashlib import sha256
import json
import os
import resource
import subprocess
import sys
import time
from profile_runtime import Profiler, calibrate, install

root, base, freeze, phase = map(Path, sys.argv[1:])
phase = str(phase)
assert phase in {'cold', 'warm'}
assert os.environ.get('LIVE_TRADING_ENABLED') == 'false'
assert os.statvfs(base).f_bavail*os.statvfs(base).f_frsize > 100*1024**3
assert os.statvfs(root).f_bavail*os.statvfs(root).f_frsize > 2*1024**3
database = root/'profile-validation.sqlite'
checkpoint = base/'unified-data/market-checkpoints'
objects = base/'unified-data/market-public/unified-market/objects'
initial = {'database_exists': database.exists(), 'sidecar_root_exists': (checkpoint/'receipts').exists(),
           'object_count': len(list(objects.glob('*.json'))), 'new_process': True}
assert initial['database_exists'] == (phase == 'warm')
assert initial['sidecar_root_exists'] == (phase == 'warm')
assert initial['object_count'] == (5986 if phase == 'warm' else 0)
calibration = calibrate()
profiler = Profiler()
market = install(profiler)
assert market.REPO == Path('/root/qtrade-local/operator-evidence/market-checkpoint-exact-git-20261028T105253Z/source')
assert len(market._CACHE) == 0
clock = datetime.fromisoformat(json.loads((freeze/'freeze.json').read_bytes())['captured_at'])
original_popen = subprocess.Popen
worker_commands = []
expected_command = [sys.executable, '-B', '-m', 'scripts.publish_unified_market', '--private-shard-worker']
def diagnostic_owned_popen(command, *args, **kwargs):
    if command == expected_command:
        assert Path(kwargs['cwd']) == market.REPO
        assert kwargs['env']['LIVE_TRADING_ENABLED'] == 'false'
        replacement = [sys.executable, '-B', str(root/'profile_worker.py'), str(root), phase]
        process = original_popen(replacement, *args, **kwargs)
        worker_commands.append({'original_argv': command, 'diagnostic_argv': replacement,
            'pid': process.pid, 'helper_sha256': sha256((root/'profile_worker.py').read_bytes()).hexdigest(),
            'only_exact_owned_worker_command_intercepted': True})
        return process
    return original_popen(command, *args, **kwargs)
subprocess.Popen = diagnostic_owned_popen
start = datetime.now(UTC).isoformat()
usage = resource.getrusage(resource.RUSAGE_SELF)
started = time.perf_counter()
item = {}
try:
    result = profiler.call('export.full_call', market.export_market, base, freeze/'frozen.sqlite',
        now=clock, seconds=120, receipt_database=database, checkpoint_root=checkpoint)
    item.update(state='SUCCEEDED', statistics=result['statistics'])
except BaseException as error:
    item.update(state='FAILED', error_class=type(error).__name__, error_code=str(error))
elapsed = time.perf_counter()-started
after = resource.getrusage(resource.RUSAGE_SELF)
child = resource.getrusage(resource.RUSAGE_CHILDREN)
item.update(kind='PROFILED_FROZEN_DIAGNOSTIC_NOT_45_SECOND_GATE', pass_name=phase,
    started_at=start, finished_at=datetime.now(UTC).isoformat(), wall_seconds=elapsed,
    process_cpu_seconds=(after.ru_utime+after.ru_stime)-(usage.ru_utime+usage.ru_stime),
    reaped_children_cpu_seconds=child.ru_utime+child.ru_stime, peak_rss_kib=after.ru_maxrss,
    pid=os.getpid(), start_ticks=Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19],
    initial=initial, calibration=calibration, threads=profiler.report(), owned_workers=worker_commands,
    children_at_completion=Path('/proc/self/task/'+str(os.getpid())+'/children').read_text().strip(),
    source_bytes_budget_unchanged=market.SOURCE_BYTE_BUDGET,
    cache_estimated_byte_budget_unchanged=market.MAX_CACHE_ESTIMATED_BYTES,
    diagnostic_seconds=120, production_export_seconds=45, profiling_enabled=True,
    production_performance_acceptance=False,
    overlap_warning='Inclusive spans nest; threads and worker processes overlap. Do not add inclusive stages to total wall.',
    exclusive_scope='Exclusive subtraction is per thread and only among instrumented spans; waits and uninstrumented work stay in enclosing stage.',
    native_cpu_scope='thread_time omits uninstrumented native-library threads; process CPU and reaped child CPU are reported separately.')
if item['state'] == 'SUCCEEDED':
    raw = (base/'unified-data/market-public/unified-market.json').read_bytes()
    index = json.loads(raw)
    refs = sorted([{'symbol': entry['symbol'], 'frequency': entry['frequency'], **ref}
        for entry in index['entries'] for ref in entry['shards']],
        key=lambda row: (row['symbol'], row['frequency'], row['start'], row['sha256']))
    refraw = json.dumps(refs, sort_keys=True, separators=(',',':'), ensure_ascii=False).encode()
    with (root/(phase+'-output-refs.json')).open('xb') as handle:
        handle.write(refraw)
    item.update(index_raw_sha256=sha256(raw).hexdigest(),
                refs_sha256=sha256(refraw).hexdigest(), refs_count=len(refs))
with (root/(phase+'-profile.json')).open('x') as handle:
    json.dump(item, handle, sort_keys=True, indent=2)
print(json.dumps({k: item[k] for k in ['pass_name','state','wall_seconds','process_cpu_seconds',
    'reaped_children_cpu_seconds','initial','children_at_completion']}), flush=True)
