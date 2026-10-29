"""Two isolated diagnostic passes only: exact fba source and unchanged 5640 freeze."""
from pathlib import Path
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
import signal
import subprocess
import sys

root = Path(__file__).resolve().parent
source_evidence = Path('/root/qtrade-local/operator-evidence/market-checkpoint-exact-git-20261028T105253Z')
source = source_evidence/'source'
base_parent = Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
freeze = Path('/root/qtrade-local/operator-evidence/market-current-freeze-20261028T070943Z')
base = base_parent/'operator-checks'/root.name
assert base.is_absolute() and base.parent == base_parent/'operator-checks' and not base.exists()
assert Path.cwd() == source
assert os.environ.get('LIVE_TRADING_ENABLED') == 'false'
manifest_raw = (source_evidence/'manifest.json').read_bytes()
manifest = json.loads(manifest_raw)
assert manifest['source_revision'] == 'fba51aade2ee2410dda087e95cf054e53375ed3a'
assert manifest['source_scope'] == 'EXACT_PUBLIC_GIT_COMMIT' and len(manifest['files']) == 2194
freeze_raw = (freeze/'freeze.json').read_bytes()
frozen = json.loads(freeze_raw)
assert frozen['selected_batches'] == 5640 and frozen['selected_chunks'] == 140136

def verify(label):
    assert {p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file()} == set(manifest['files'])
    for name, expected in manifest['files'].items():
        assert sha256((source/name).read_bytes()).hexdigest() == expected
    assert sha256((freeze/'frozen.sqlite').read_bytes()).hexdigest() == frozen['ledger_sha256']
    assert (freeze/'freeze.json').read_bytes() == freeze_raw
    return {'label': label, 'source_files_verified': 2194,
            'source_manifest_sha256': sha256(manifest_raw).hexdigest(),
            'ledger_sha256': frozen['ledger_sha256'], 'freeze_file_sha256': sha256(freeze_raw).hexdigest()}

def space():
    remote_free = os.statvfs(base.parent).f_bavail*os.statvfs(base.parent).f_frsize
    local_free = os.statvfs(root).f_bavail*os.statvfs(root).f_frsize
    assert remote_free > 100*1024**3 and local_free > 2*1024**3
    return {'nfs_available_bytes': remote_free, 'local_available_bytes': local_free,
            'nfs_reserve_bytes': 100*1024**3, 'local_reserve_bytes': 2*1024**3}

def mount(label):
    raw = Path('/proc/self/mountinfo').read_bytes()
    with (root/(label+'-mountinfo.private.raw')).open('xb') as handle:
        handle.write(raw)
    selected = []
    for line in raw.decode().splitlines():
        fields = line.split(); delimiter = fields.index('-')
        point = Path(fields[4].replace('\\040',' '))
        if base.resolve().is_relative_to(point):
            selected.append((len(str(point)), fields[0], fields[2], fields[delimiter+1]))
    _, identity, device, kind = max(selected)
    actual = base.stat().st_dev
    production = (base_parent/'unified-data/market-public').stat().st_dev
    assert actual == production == 66 and kind in {'nfs','nfs4'}
    return {'mountinfo_raw_sha256': sha256(raw).hexdigest(), 'mount_id': identity,
            'major_minor': device, 'filesystem_type': kind, 'output_st_dev': actual,
            'production_st_dev': production, 'same_production_device': True,
            'raw_mountinfo_retained_privately_outside_git': True}

before_source = verify('before')
before_space = space()
catalog = (freeze/'snapshot/unified-data/public/unified.json').read_bytes()
assert sha256(catalog).hexdigest() == frozen['catalog_raw_sha256']
(base/'unified-data/public').mkdir(parents=True)
with (base/'unified-data/public/unified.json').open('xb') as handle:
    handle.write(catalog)
(base/'unified-data/lake').symlink_to(freeze/'snapshot/unified-data/lake', target_is_directory=True)
(root/'tmp').mkdir()
before_mount = mount('before')
helpers = {p.name: sha256(p.read_bytes()).hexdigest() for p in root.glob('*.py')}
preflight = {'kind':'ISOLATED_PROFILE_PREFLIGHT','recorded_at':datetime.now(UTC).isoformat(),
    'source':before_source,'space':before_space,'mount':before_mount,'output_base':str(base),
    'freeze':str(freeze),'freeze_evidence_sha256':frozen['evidence_sha256'],
    'helpers_sha256':helpers,'profiling_enabled':True,'production_performance_acceptance':False,
    'concurrency_scope':'Shared production-equivalent NFS; other authorized benchmark/services may run concurrently. No exclusive resource claim.'}
with (root/'preflight.json').open('x') as handle:
    json.dump(preflight,handle,sort_keys=True,indent=2)
print(json.dumps({'phase':'PREFLIGHT','source_files':2194,'output':str(base),'space':before_space,'mount':before_mount}),flush=True)
results = []
for phase in ('cold','warm'):
    space()
    command = [sys.executable,'-B',str(root/'profile_driver.py'),str(root),str(base),str(freeze),phase]
    process = subprocess.Popen(command,cwd=source,env={**os.environ,'TMPDIR':str(root/'tmp')},
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    timeout_cleanup = False
    try:
        out,err = process.communicate(timeout=165)
    except subprocess.TimeoutExpired:
        # Only this newly created isolated process group is eligible for cleanup.
        assert process.poll() is None and os.getpgid(process.pid) == process.pid
        assert (Path('/proc')/str(process.pid)/'cmdline').read_bytes().split(b'\0')[:3] == [
            sys.executable.encode(), b'-B', str(root/'profile_driver.py').encode()]
        timeout_cleanup = True
        os.killpg(process.pid,signal.SIGTERM)
        try:
            out,err = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL)
            out,err = process.communicate(timeout=10)
    for suffix,raw in [('stdout.log',out),('stderr.log',err)]:
        with (root/(phase+'-'+suffix)).open('xb') as handle:
            handle.write(raw)
    if out:
        print(out.decode().strip(),flush=True)
    report_path = root/(phase+'-profile.json')
    if process.returncode or not report_path.exists():
        results.append({'pass_name':phase,'state':'HARNESS_FAILED','exit_code':process.returncode,
                        'owned_group_timeout_cleanup':timeout_cleanup})
        break
    item = json.loads(report_path.read_bytes())
    results.append({'pass_name':phase,'state':item['state'],'wall_seconds':item['wall_seconds']})
    if item['state'] != 'SUCCEEDED':
        break
    stats = item['statistics']
    assert stats['selected_batches'] == 5640 and stats['selected_chunks'] == 140136
    assert stats['total_files'] == 5986 and stats['total_bytes'] == 166050382
    assert stats['quarantined_entries'] == 0 and item['refs_count'] == 5986
    assert not item['children_at_completion']
after_source = verify('after')
after_space = space()
after_mount = mount('after')
assert before_mount['mount_id'] == after_mount['mount_id']
assert {p.name:sha256(p.read_bytes()).hexdigest() for p in root.glob('*.py')} == helpers
report = {'kind':'PROFILED_FROZEN_COLD_AND_WARM_DIAGNOSTIC','recorded_at':datetime.now(UTC).isoformat(),
    'source_before':before_source,'source_after':after_source,'space_before':before_space,
    'space_after':after_space,'mount_before':before_mount,'mount_after':after_mount,
    'helpers_sha256':helpers,'source_revision':manifest['source_revision'],'output_base':str(base),
    'freeze_evidence_sha256':frozen['evidence_sha256'],'controlled_snapshot_clock':frozen['captured_at'],
    'results':results,'diagnostic_seconds':120,'production_export_seconds':45,
    'profiling_enabled':True,'production_performance_acceptance':False,
    'production_mutations':False,'raw_mountinfo_not_for_git':True,
    'inclusive_stages_must_not_be_added':True,'NFS_resource_exclusivity_claimed':False,
    'cold_warm_same_refs': len(results)==2 and (root/'cold-output-refs.json').read_bytes()==(root/'warm-output-refs.json').read_bytes()}
with (root/'profile-report.json').open('x') as handle:
    json.dump(report,handle,sort_keys=True,indent=2)
print(json.dumps({'phase':'COMPLETE','results':results,'source_files_verified_after':2194,
                  'same_refs':report['cold_warm_same_refs']}),flush=True)
