import gzip,hashlib,json,shutil,xml.etree.ElementTree as ET
from datetime import UTC,datetime
from pathlib import Path
repo=Path('E:/QTrade-git')
root=Path('E:/QTrade-temp/factor-api-queue-e2e-20261028T132322Z-3bcecd4a')
target=repo/'docs/evidence/step2-factor-local-queue-e2e-20261028'
if target.exists():
 assert not (target/'manifest.json').exists() and (target/'auth-safety-proof.json').is_file()
else:target.mkdir(parents=True)
def h(raw):return hashlib.sha256(raw).hexdigest()
private_raw=(root/'http-exchanges.json').read_bytes()
exchanges=json.loads(private_raw)
for item in exchanges:
 if item['path'].endswith('/login'):
  item.pop('request',None)
  item['auth_request_projection']={'credential_values_omitted':True,'cookie_session_values_omitted':True}
 assert not any(key.lower() in {'authorization','cookie','set-cookie'} for key in item.get('headers',{}))
(target/'http-safe-projection.json').write_text(json.dumps(exchanges,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
auth_proof={'private_trace_sha256':h(private_raw),'private_trace_bytes':len(private_raw),
 'private_trace_path':str(root/'http-exchanges.json'),'http_exchanges':len(exchanges),
 'harness_already_omitted_password_values':True,'cookie_session_values_recorded':False,
 'auth_material_exported':False,'Git_representation':'SAFE_PROJECTION_NOT_PRIVATE_TRACE',
 'HMAC_in_public_queue_envelope_is_checksum_not_secret':True}
(target/'auth-safety-proof.json').write_text(json.dumps(auth_proof,indent=2)+'\n')
raw_artifacts=[]
allowed_subdirs={'linux-protocol-negative','late-cas-negative','job-artifacts'}
for p in sorted(root.rglob('*')):
 if not p.is_file():continue
 relative=p.relative_to(root)
 if len(relative.parts)>1 and relative.parts[0] not in allowed_subdirs:continue
 if p.name in {'source.tar','http-exchanges.json'}:continue
 if p.suffix in {'.sqlite','.pyc'}:continue
 raw=p.read_bytes()
 name=relative.as_posix()
 if p.suffix=='.json' and len(raw)>250_000:
  name+='.gz';stored=gzip.compress(raw,mtime=0);encoding='GZIP_OF_EXACT_ORIGINAL_BYTES'
 else:
  stored=raw;encoding='IDENTITY'
 out=target/name;out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(stored)
 raw_artifacts.append({'original':relative.as_posix(),'original_bytes':len(raw),'original_sha256':h(raw),
  'stored':name,'encoding':encoding})
drivers=target/'drivers';drivers.mkdir(exist_ok=True)
for name in ['dispatch_factor_api_queue_e2e_20261028.py','collect_factor_api_queue_e2e_20261028.py',
 'collect_factor_api_queue_job_20261028.py','review_factor_api_queue_e2e_20261028.py',
 'late_cas_factor_queue_probe_20261028.py','dispatch_late_cas_factor_queue_probe_20261028.py',
 'close_factor_api_queue_e2e_20261028.py','seal_factor_api_queue_e2e_20261028.py']:
 shutil.copyfile(Path('E:/QTrade-temp')/name,drivers/name)
windows=Path('E:/QTrade-temp/factor-queue-protocol-negative-final-5e0f76f2')
for name in ['protocol-negative.json','integration.xml']:
 p=target/'windows-synthetic-protocol'/name;p.parent.mkdir(exist_ok=True);shutil.copyfile(windows/name,p)
doc=repo/'docs/STEP2_FACTOR_LOCAL_QUEUE_E2E_20261028.md'
shutil.copyfile(doc,target/doc.name)
result=json.loads((root/'e2e-result.json').read_bytes())
source=json.loads((root/'source-manifest.json').read_bytes())
closure=json.loads((root/'closure.json').read_bytes())
assert closure['source_unchanged'] and closure['ready_mirror_unchanged']
assert (root/'factor_api_queue_e2e_20261028.py').read_bytes()==(Path('E:/QTrade-temp/factor_api_queue_e2e_20261028.py')).read_bytes()
xml=[]
for path in [root/'integration.xml',root/'linux-protocol-negative/integration.xml',root/'late-cas-negative/integration.xml']:
 suite=ET.parse(path).getroot()
 assert suite.attrib['failures'] in {'False','0'} and suite.attrib['errors']=='0'
 assert not suite.findall('.//failure') and not suite.findall('.//error')
 normalized=path.with_name('integration-normalized.xml')
 assert normalized.is_file()
 standard=ET.parse(normalized).getroot()
 assert standard.attrib['failures']=='0' and standard.attrib['errors']=='0'
 xml.append({'original_file':path.relative_to(root).as_posix(),'original_sha256':h(path.read_bytes()),
  'normalized_file':normalized.relative_to(root).as_posix(),'normalized_sha256':h(normalized.read_bytes()),
  **standard.attrib})
manifest={'schema_version':'qtrade.factor-local-queue-e2e-acceptance.v1',
 'sealed_at':datetime.now(UTC).isoformat(),'state':'PASS_ISOLATED_ASGI_LOCAL_GIT_OWNED_WORKER',
 'source_revision':source['baseline_commit'],'source_files':len(source['expected_files']),
 'source_tar_sha256':source['source_tar_sha256'],'source_overlays':{},
 'remote_operator_root':'/root/qtrade-local/operator-evidence/'+root.name,
 'nfs_base':json.loads((root/'staging.json').read_bytes())['nfs_base'],
 'ready_mirror_files':18829,'ready_mirror_bytes':786448967,
 'end_to_end':result,'junit':xml,'auth_safety':auth_proof,
 'closure':closure,'independent_review':json.loads((root/'independent-review.json').read_bytes()),
 'production_queue_accessed':False,'production_deployed':False,'live_trading_enabled':False,
 'github_tls_browser_verified':False,'portfolio_backtest_implemented':False,
 'raw_artifacts':raw_artifacts}
manifest['files']=[{'file':p.relative_to(target).as_posix(),'bytes':p.stat().st_size,'sha256':h(p.read_bytes())}
 for p in sorted(target.rglob('*')) if p.is_file()]
(target/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
print(target/'manifest.json',h((target/'manifest.json').read_bytes()),len(manifest['files']),flush=True)
