import hashlib,io,json,shutil,tarfile,xml.etree.ElementTree as ET
from datetime import UTC,datetime
from pathlib import Path
repo=Path('E:/QTrade-git')
target=repo/'docs/evidence/step2-factor-queue-s2d-20261028'
assert not target.exists()
target.mkdir(parents=True)
names=['factor-queue-s2d-20261028T123014Z-b12b8041','factor-queue-s2d-final-20261028T124314Z-e0813807']
def h(raw):return hashlib.sha256(raw).hexdigest()
packages=[]
for name in names:
 work=Path('E:/QTrade-temp')/name
 destination=target/name;destination.mkdir()
 for p in work.rglob('*'):
  if not p.is_file() or p.name in {'baseline.tar','isolated-strategies.sqlite'}:continue
  relative=p.relative_to(work);out=destination/relative;out.parent.mkdir(parents=True,exist_ok=True)
  shutil.copyfile(p,out)
 m=json.loads((work/'package-manifest.json').read_bytes())
 with tarfile.open(work/'overlay.tar') as archive:
  for entry in archive.getmembers():
   assert entry.isfile()
   raw=archive.extractfile(entry).read()
   assert h(raw)==m['overlays'][entry.name]['sha256']
   p=destination/'candidate'/entry.name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
 suites=[]
 for xml in ['regression.xml','windows-final-regression.xml']:
  root=ET.parse(work/xml).getroot()
  suites.append({'file':xml,'suites':[s.attrib for s in root.iter('testsuite')]})
 acceptance=json.loads((work/'actual-acceptance.json').read_bytes())
 packages.append({'root':name,'baseline_commit':m['baseline_commit'],
  'baseline_tar_sha256':m['baseline_sha256'],'overlay_sha256':m['overlay_sha256'],
  'file_count':len(m['expected_files']),'overlays':m['overlays'],'regression':suites,
  'actual_acceptance_evidence_sha256':acceptance['evidence_sha256'],
  'registration':acceptance['registration'],'registry_counts':acceptance['registry_counts']})
development=target/'development';development.mkdir()
for p in Path('E:/QTrade-temp').glob('s2d-*.xml'):
 shutil.copyfile(p,development/p.name)
legacy=max(Path('E:/QTrade-temp').glob('factor-legacy-s2c13-*'),key=lambda p:p.name)
shutil.copytree(legacy,target/legacy.name)
drivers=target/'drivers';drivers.mkdir()
for name in ['run_factor_queue_acceptance_20261028.py','run_factor_queue_final_acceptance_20261028.py',
 'run_factor_queue_windows_package_20261028.py','run_factor_queue_actual_20261028.py',
 'run_factor_queue_final_actual_20261028.py','dispatch_factor_queue_actual_20261028.py',
 'dispatch_factor_queue_final_actual_20261028.py','build_factor_s2c13_archive_20261028.py',
 'dispatch_factor_s2c13_archive_20261028.py','retrieve_factor_queue_databases_20261028.py',
 'seal_factor_queue_s2d_20261028.py']:
 shutil.copyfile(Path('E:/QTrade-temp')/name,drivers/name)
shutil.copyfile(repo/'docs/STEP2_FACTOR_QUEUE_BACKEND_ACCEPTANCE_20261028.md',
 target/'STEP2_FACTOR_QUEUE_BACKEND_ACCEPTANCE_20261028.md')
final_manifest=json.loads((Path('E:/QTrade-temp')/names[-1]/'package-manifest.json').read_bytes())
assert all(h((repo/name).read_bytes())==value['sha256'] for name,value in final_manifest['overlays'].items())
manifest={'schema_version':'qtrade.factor-queue-backend-acceptance.v1',
 'sealed_at':datetime.now(UTC).isoformat(),'state':'PASS_NONPRODUCTION_REGISTRATION_ONLY',
 'remote_parent':'/root/qtrade-local/operator-evidence','packages':packages,
 'current_overlay_bytes_match_final':True,'old_report_gzip_sha256':'47bed3add84aff16f901531115c17ac4534cfd667b6716595a1ce098b9af37dd',
 'longer_report_gzip_sha256':'74c246d7822b4e860706dcc2c212763ede3aef62f38f5b94297237acf5457ada',
 'old_c_definition_sha256':'f728b11244661ef3682c80b0bfdbf2b9d88d666716fea3bbded0819a073cad8c',
 'live_trading_enabled':False,'production_queue_accessed':False,'production_deployed':False,
 'backtest_implemented':False,'paper_admission':False,'model_activation':False,
 'limitations':['RETROSPECTIVE_GROSS_LABEL_ONLY','NO_FACTOR_EXECUTION_OR_PORTFOLIO_BACKTEST',
 'NO_PRODUCTION_DELIVERY_ACCEPTANCE','REVISION_LABEL_PLUS_EXPLICIT_OVERLAYS_NOT_GIT_CERTIFICATION',
 'REGISTRY_AND_WORKFLOW_DATABASE_COMMITS_ARE_NOT_ONE_TRANSACTION']}
manifest['files']=[{'file':p.relative_to(target).as_posix(),'bytes':p.stat().st_size,'sha256':h(p.read_bytes())}
 for p in sorted(target.rglob('*')) if p.is_file()]
(target/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
print(target/'manifest.json',h((target/'manifest.json').read_bytes()),len(manifest['files']),flush=True)
