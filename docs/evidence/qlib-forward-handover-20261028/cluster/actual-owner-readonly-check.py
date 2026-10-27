"""Read-only actual owner check; native Supervisor status is title-cased."""
import importlib.util
import json
import re
from datetime import UTC, datetime
from pathlib import Path

OP = Path('/root/qtrade-local/operator-evidence/forward-handover-9846a7f-20261028')
spec = importlib.util.spec_from_file_location('directed_forward_handover', OP/'directed_handover.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
pre = h.load(OP/'preflight.json')
instance = h.actual_instance()
assert instance['instance_id'] != pre['old_instance_id']
output = h.run_ctl('status')
assert output.split()[0] == h.PROGRAM and output.split()[1].upper() == 'RUNNING'
newpid = int(re.search(r'pid (\d+)', output).group(1))
process = Path('/proc')/str(newpid)
args = (process/'cmdline').read_bytes().split(b'\0')
assert b'scripts.run_qlib_forward_service' in args
assert b'--initialize-paper' not in args
assert args[args.index(b'--source-revision')+1].decode() == h.REV
release = h.BASE/'releases'/h.REV
assert (process/'cwd').resolve() == release
env = dict(v.split(b'=', 1) for v in (process/'environ').read_bytes().split(b'\0') if b'=' in v)
assert env[b'PYTHONPATH'].decode() == str(release/'src')
assert env.get(b'LIVE_TRADING_ENABLED', b'false').lower() == b'false'
manifest = json.loads((OP/'source-manifest.json').read_bytes())
assert manifest['revision'] == h.REV and len(manifest['files']) == 582
for name, meta in manifest['files'].items():
    raw = (release/name).read_bytes()
    assert len(raw) == meta['bytes'] and h.digest(raw) == meta['sha256'], name
assert h.context() == pre['context'] and h.service_tokens() == pre['services']
assert h.fingerprint() == pre['fingerprints']
publication = h.load(h.BASE/'workflow/public/forward.json')
assert publication['source_revision'] == h.REV and len(publication['books']) == 14
assert not publication['errors']
retired = list(h.BOOKS.glob('*/retirements/*.json'))
assert len(retired) == 2
assert len(list(h.BOOKS.glob('*/rejected-targets/*.json'))) == 12
assert all(not b['running'] for b in publication['books'] if b['mode'] == 'PAPER')
assert not (OP/'recovery-batch-consumed.json').exists()
h.write('new-owner-pre-recovery.json', {
    'kind': 'NEW_OWNER_EXACT_SOURCE_AND_STOPPED_BOOKS_VERIFIED',
    'at': datetime.now(UTC).isoformat(), 'new_pid': newpid, 'new_instance': instance,
    'publication_sha256': publication['evidence_sha256'],
    'fingerprints_unchanged': True,
    'retirement_receipts': [str(p.relative_to(h.BOOKS)) for p in retired],
    'services_unchanged': True, 'context': h.context(),
    'operator_check_sha256': h.digest(Path(__file__).read_bytes()),
    'native_status_stdout': output, 'source_files_verified': 582,
    'actual_process_environment_source_verified': True,
    'initialize_paper_argument': False, 'live_trading_enabled': False,
})
print('NEW_OWNER_VERIFIED', newpid, publication['state'])
