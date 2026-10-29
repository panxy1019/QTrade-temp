"""Actual local-bare negative; no URLs, SSH, private fixtures or production writes."""
import importlib.util
import json
import tempfile
from pathlib import Path

test = Path(__file__).parent / 'test_publisher_object_stage_nonproduction_20261028.py'
spec = importlib.util.spec_from_file_location('stage_fixture_test', test)
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
root = Path(tempfile.mkdtemp(prefix='stage-negotiation-local-', dir=str(test.parent)))
source, remote, isolated, old, new, cert, target, transport = module.fixture.__wrapped__(root)
plan = module.h.plan_batch(target, target.blobs, transport.refs(), 1)
module.run(isolated, ['push', str(remote), plan.commit + ':' + plan.ref])
before = set((remote / 'objects/pack').glob('*.idx'))
module.run(source, ['-c', 'pack.useSparse=false', 'push', str(remote), 'HEAD:refs/heads/main'])
sent = set()
for index in set((remote / 'objects/pack').glob('*.idx')) - before:
    for line in module.run(remote, ['verify-pack', '-v', str(index)]).splitlines():
        parts = line.split()
        if len(parts) >= 5 and module.h.identity(parts[0].decode()):
            sent.add(parts[0].decode())
result = {'fixture_root': str(root), 'staged_blob_count': len(target.blobs),
    'actual_main_push_blob_overlap': sorted(sent & target.blobs), 'actual_final_pack_objects': len(sent),
    'old_main': old, 'new_main': new,
    'source_has_aux_commit': module.run(source, ['cat-file', '-e', plan.commit], ok=False).returncode == 0,
    'no_production_actions': True}
(root / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
assert result['actual_main_push_blob_overlap'], 'The independent negative no longer reproduces; review instead of claiming old failure.'
