"""Copy only this offline review's public/synthetic files and safe counters."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime, timezone
import json
import shutil

root=Path('E:/QTrade-temp/relay-independent-review-20261028')
destination=Path('E:/QTrade-git/docs/evidence/publication-native-relay-independent-20261028')
expected={
 'publication_loopback_proxy_20261028.py':'f4146011c52405de87570e95e9b2cf084bb2896072453e01fe175d2c9a11aac2',
 'publication_native_relay_operator_v2_20261028.py':'91bfd970f1def55b01a4935907578206a7d91ece39ae857f3b451a99037c84c1',
 'run_publication_desktop_relay_v2_20261028.py':'303c1cc054fe7dd97c6936f69b9f95ba5efd28557c2fd1669d74848c48b5ab08'}
for name,digest in expected.items():
    for parent in [root,Path('E:/QTrade-temp')]:
        if sha256((parent/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('FINAL_SOURCE_CHANGED_'+name)
destination.mkdir(exist_ok=False)
for source in sorted(root.iterdir()):
    if source.is_file():
        shutil.copyfile(source,destination/source.name)
safe=Path('E:/QTrade-temp/cluster-evidence/publication-relay-ls-20261028T130101Z')
for old,new in [('result.stdout','v1-actual-ls-result.stdout'),
                ('relay-safe-metadata.json','v1-actual-ls-relay-safe-metadata.json')]:
    shutil.copyfile(safe/old,destination/new)
(destination/'README.md').write_text(
    '# Offline native relay independent review\n\n'
    'Final exact v4: 71 passed, 0 failed, 0 skipped. No SSH, socket, public push, '
    'credential-helper import or production mutation was performed by this review.\n\n'
    'Eight passing v1 cases reproduce acceptance defects. v2 and desktop guards '
    'are local tests with explicitly described mocks; no transport recovery claim. '
    'The v1 actual read-only ls result has zero relay channels/bytes and is retained '
    'as insufficient route evidence.\n\n'
    'Run the two final test files explicitly. Preserve the failed-fixture snapshot '
    'and XML without including that snapshot in the final suite. Prior a2cf and '
    'e14afe source snapshots are historical evidence. See the linked main review '
    'for boundaries, reproduction, and exact imported public-contract hashes.\n',
    encoding='utf-8')
files={p.name:{'sha256':sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size}
       for p in sorted(destination.iterdir()) if p.is_file()}
manifest={'schema_version':'qtrade.offline-native-relay-independent-review.v1',
 'captured_at':datetime.now(timezone.utc).isoformat(),
 'state':'STATIC_OFFLINE_REVIEW_PASSED_ACTUAL_TRANSPORT_SEPARATE',
 'final_sources':expected,'final_tests':{'passed':71,'failed':0,'skipped':0},
 'review_performed_network':False,'review_performed_production_writes':False,
 'credential_helper_imported_or_read':False,'files':files}
path=destination/'manifest.json'
path.write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n',encoding='utf-8')
for name,record in files.items():
    if sha256((destination/name).read_bytes()).hexdigest()!=record['sha256']:
        raise ValueError('ARCHIVE_CHANGED_'+name)
print(json.dumps({'evidence':str(destination),'files':len(files),
 'manifest_sha256':sha256(path.read_bytes()).hexdigest()},sort_keys=True))
