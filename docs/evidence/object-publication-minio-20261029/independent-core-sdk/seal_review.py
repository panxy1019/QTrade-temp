"""Seal only public source and synthetic/local evidence; no cloud access."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
import importlib
import json
import shutil
import sys
import xml.etree.ElementTree as ET

root=Path(__file__).parent
repo=Path('E:/QTrade-git')
expected={
 'src/qtrade/research/object_publication.py':'d11c801a90142d4b7142b8f67c422c341b30b84201999dd297c186c3582fa7d0',
 'src/qtrade/research/r2_object_store.py':'d26685bf8f95db48ef3db8d0c8d4716aaac56d68963fd2c302fa9b5c62c16202',
 'tests/test_object_publication.py':'f55e4a4bec2f01c193a82904be67ea7468bc615218ac68597f2c336e49a36806',
 'tests/unit/test_r2_object_store.py':'16c2034f0782258d1058906c11adaa8d2175e39458650a573f2903c3f71bb1d0'}
for name,digest in expected.items():
    if sha256((repo/name).read_bytes()).hexdigest()!=digest:raise ValueError('FROZEN_SOURCE_CHANGED')
shutil.copyfile(repo/'tests/test_object_publication.py',root/'test_object_publication_d11_author.py')
shutil.copyfile(repo/'tests/unit/test_r2_object_store.py',root/'test_r2_object_store_d266_author.py')
sys.path.insert(0,'E:/QTrade-temp/r2-sdk-20261029')
sdk={}
for name in ['boto3','boto3.session','botocore','botocore.config','botocore.session',
    'botocore.loaders','botocore.plugin','botocore.endpoint','botocore.auth']:
    module=importlib.import_module(name);path=Path(module.__file__)
    if not path.resolve().is_relative_to(Path('E:/QTrade-temp/r2-sdk-20261029').resolve()):
        raise ValueError('SDK_ORIGIN_CHANGED')
    sdk[name]={'path':str(path),'sha256':sha256(path.read_bytes()).hexdigest(),
        'version':getattr(module,'__version__',None)}
if sdk['boto3']['version']!='1.43.120' or sdk['botocore']['version']!='1.43.120':
    raise ValueError('SDK_VERSION_CHANGED')
(root/'actual-sdk-source-metadata.json').write_text(json.dumps(sdk,sort_keys=True,indent=2)+'\n')
prior=Path('E:/QTrade-temp/r2-sdk-independent-review-20261029T004200Z')
external={name:sha256((prior/name).read_bytes()).hexdigest() for name in (
    'manifest.json','r2_object_store_fixed.py','r2_object_store_original.py',
    'test_r2_sdk_fixed_counterexamples.py','test_r2_sdk_independent_original.py',
    'original-counterexamples-six.xml','README-fixed-author-validation.md',
    'README-original-findings.md')}
(root/'prior-sdk-evidence-references.json').write_text(json.dumps({
    'directory':str(prior),'files':external},sort_keys=True,indent=2)+'\n')
runs={}
for path in sorted(root.glob('*.xml')):
    suite=ET.parse(path).getroot().find('testsuite')
    runs[path.name]={key:suite.attrib[key] for key in ('tests','failures','errors','skipped')}
if runs['final-fixed-core-sdk-combined.xml']!={'tests':'112','failures':'0','errors':'0','skipped':'0'}:
    raise ValueError('FINAL_TEST_RESULT_CHANGED')
files={p.name:{'sha256':sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size}
    for p in sorted(root.iterdir()) if p.is_file() and p.name!='manifest.json'}
manifest={'schema_version':'qtrade.object-core-sdk-independent-review.v1',
    'sealed_at':datetime.now(timezone.utc).isoformat(),'state':'FIXED_CODE_OFFLINE_REVIEW_PASSED_CLOUD_ACCEPTANCE_SEPARATE',
    'reviewed_sources':expected,'original_core_sha256':'d362529ba56f2283a0308c10103abe55b27e8feff9bdcf75513b42cc2f21d8dc',
    'closed_findings':['RECONCILE_PREPARED_MANIFEST_ID_AND_REMOTE_MANIFEST_BINDING'],
    'test_runs':runs,'files':files,'reviewer_modified_core_or_sdk':False,
    'network_or_cloud_access_performed':False,'production_mutation_performed':False,
    'minio_or_r2_accepted_by_this_review':False,'performance_sla_accepted':False}
with (root/'manifest.json').open('x',encoding='utf-8') as f:f.write(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
for name,record in files.items():
    if sha256((root/name).read_bytes()).hexdigest()!=record['sha256']:raise ValueError('EVIDENCE_CHANGED')
print(json.dumps({'evidence':str(root),'files':len(files),
    'manifest_sha256':sha256((root/'manifest.json').read_bytes()).hexdigest(),
    'final_test_run':runs['final-fixed-core-sdk-combined.xml']},sort_keys=True))
