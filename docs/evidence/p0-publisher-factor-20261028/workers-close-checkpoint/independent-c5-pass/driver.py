import json,os,subprocess,sys,tarfile,hashlib,shutil
from pathlib import Path
root=Path(__file__).resolve().parent
assert root.parent==Path('/root/qtrade-local/operator-evidence') and root.name.startswith('market-worker-integrated-independent-')
repo=root/'source';repo.mkdir()
with tarfile.open(root/'source.tar') as archive:archive.extractall(repo,filter='data')
for name,digest in [('scripts/publish_unified_market.py', 'c5c787943a720c48f4795fddbf1984d741e43453b7935df4357aa342e86fe9ab'), ('tests/test_unified_market.py', '3b87d24221e092f4cad2b5547cd792c666b04d69cabe046eb3055619aef2a92c')]:
 source=root/Path(name).name
 assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
 shutil.copyfile(source,repo/name)
os.environ['PYTHONPATH']=str(repo/'src')+':'+str(repo)
command=[sys.executable,'-B','-m','pytest',str(root/'test_workers.py'),'-q','-p','no:cacheprovider','-o','addopts=','--basetemp',str(root/'pytest-fixtures'),'--junitxml',str(root/'tests.xml')]
with (root/'tests.log').open('wb') as output:
 child=subprocess.Popen(command,cwd=repo,stdout=output,stderr=subprocess.STDOUT)
 code=child.wait(timeout=180)
result={'exit_code':code,'pytest_pid':child.pid,'own_popen':True,'scope':'ONLY_TEMP_LAKE_FIXTURES_NO_PRODUCTION_ACTIONS',
 'sources':{name:hashlib.sha256((repo/name).read_bytes()).hexdigest() for name,digest in [('scripts/publish_unified_market.py', 'c5c787943a720c48f4795fddbf1984d741e43453b7935df4357aa342e86fe9ab'), ('tests/test_unified_market.py', '3b87d24221e092f4cad2b5547cd792c666b04d69cabe046eb3055619aef2a92c')]}}
(root/'driver-receipt.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'exit_code':code,'root':str(root)}),flush=True)
raise SystemExit(code)
