import json,os,subprocess,sys,tarfile,hashlib,shutil
from pathlib import Path
root=Path(__file__).resolve().parent
assert root.parent==Path('/root/qtrade-local/operator-evidence') and root.name.startswith('market-worker-integrated-independent-')
repo=root/'source';repo.mkdir()
with tarfile.open(root/'source.tar') as archive:archive.extractall(repo,filter='data')
for name,digest in [('scripts/publish_unified_market.py', 'ccba53072e0dbafa24a4621173c7d2b0747674cbc12d3c3f89cb3079f8bf1b79'), ('tests/test_unified_market.py', 'b83947ed2e0d7cf5f3d0de02853b3f1ff789ddfedfcd3acd2acc3fdc0fcef68f')]:
 source=root/Path(name).name
 assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
 shutil.copyfile(source,repo/name)
os.environ['PYTHONPATH']=str(repo/'src')+':'+str(repo)
command=[sys.executable,'-B','-m','pytest',str(root/'test_workers.py'),'-q','-p','no:cacheprovider','-o','addopts=','--basetemp',str(root/'pytest-fixtures'),'--junitxml',str(root/'tests.xml')]
with (root/'tests.log').open('wb') as output:
 child=subprocess.Popen(command,cwd=repo,stdout=output,stderr=subprocess.STDOUT)
 code=child.wait(timeout=180)
result={'exit_code':code,'pytest_pid':child.pid,'own_popen':True,'scope':'ONLY_TEMP_LAKE_FIXTURES_NO_PRODUCTION_ACTIONS',
 'sources':{name:hashlib.sha256((repo/name).read_bytes()).hexdigest() for name,digest in [('scripts/publish_unified_market.py', 'ccba53072e0dbafa24a4621173c7d2b0747674cbc12d3c3f89cb3079f8bf1b79'), ('tests/test_unified_market.py', 'b83947ed2e0d7cf5f3d0de02853b3f1ff789ddfedfcd3acd2acc3fdc0fcef68f')]}}
(root/'driver-receipt.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'exit_code':code,'root':str(root)}),flush=True)
raise SystemExit(code)
