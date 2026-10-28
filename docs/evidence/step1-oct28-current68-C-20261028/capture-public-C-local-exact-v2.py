"""Original public C helper on real local HTTP, bound to remote verified inputs."""
import hashlib,json,subprocess,sys,os,traceback
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,'E:/QTrade-temp')
from authorized_cluster_read import connect
git_repo=Path('E:/QTrade-git');root=Path(__file__).parent;repo=root/'public-source9846'
sys.path[:0]=[str(repo/'src'),str(repo)]
from qtrade.monitoring.data_freshness import AUTHORITY,signed
from scripts import qlib_public_delivery as p
revision='9846a7f30774d6ae4b4d41f02b493c5246be87d9'
source_paths=['scripts/qlib_public_delivery.py','src/qtrade/api/online_research.py',
 'src/qtrade/research/signal_publication.py','src/qtrade/monitoring/data_freshness.py',
 'src/qtrade/research/model_registry.py','src/qtrade/research/data_versions.py']
source_hashes={name:hashlib.sha256(subprocess.check_output(['git','show',revision+':'+name],cwd=git_repo)).hexdigest() for name in source_paths}
assert all(hashlib.sha256((repo/name).read_bytes()).hexdigest()==value for name,value in source_hashes.items())
captured=json.loads((root/'C-current68-20261028T122338Z.json').read_bytes())
transition=captured['private_result'];assert transition['state']=='VERIFIED_VERSION_PREDICTION_SIGNAL_TRANSITION'
assert transition['approved_model_id']=='68d9287efc49c24280ab8c6e4df5c69724c0e6e9876396e7312a6b771405a81c'
argv=captured['argv'];paths={role:argv[argv.index(flag)+1]+('/version.json' if role=='current' else '/manifest.json') for role,flag in [('current','--current-version'),('prediction','--prediction'),('signals','--signals')]}
inputs={};input_hashes={};client=connect()
try:
 sftp=client.open_sftp()
 try:
  for role,path in paths.items():
   with sftp.open(path,'rb') as f:raw=f.read(4000001)
   assert len(raw)<=4000000
   value=json.loads(raw);assert signed({k:x for k,x in value.items() if k!='evidence_sha256'})==value
   if role=='current':assert value['version_id']==transition['version_id'] and value['evidence_sha256']=='287d48c7e9dbfd6d5bbf74f7adfb35cfc2ded346a8fe38842cd6af72a008913e'
   else:assert value['evidence_sha256']==transition[('prediction' if role=='prediction' else 'signals')+'_sha256']
   inputs[role]=value;input_hashes[role]={'path':path,'file_sha256':hashlib.sha256(raw).hexdigest(),'evidence_sha256':value['evidence_sha256']}
 finally:sftp.close()
finally:client.close()
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
for role,value in inputs.items():
 with (root/('C-local-input-'+stamp+'-'+role+'.json')).open('x',encoding='utf-8') as f:json.dump(value,f,sort_keys=True);f.write('\n')
captures=[];original=p._get
def capture(*args,**kwargs):
 value=original(*args,**kwargs);captures.append(value);return value
p._get=capture
os.environ['LIVE_TRADING_ENABLED']='false'
started=datetime.now(timezone.utc).isoformat();error=None;result=None
try:result=p.verify_public_delivery(transition,inputs['current'],inputs['prediction'],inputs['signals'],'ae33a7c8c27ea721eb3be1e4430b562a44b67f91')
except Exception as exc:error={'type':type(exc).__name__,'code':str(exc),'traceback':traceback.format_exc()}
assert all(hashlib.sha256((repo/name).read_bytes()).hexdigest()==value for name,value in source_hashes.items())
value=signed({**AUTHORITY,'kind':'ACTUAL_NEW_CURRENT68_C_LOCAL_ORIGINAL_PUBLIC_HELPER','started_at':started,'checked_at':datetime.now(timezone.utc).isoformat(),'source_revision':revision,'helper_source_files_sha256_before_after':source_hashes,'private_transition':transition,'input_hashes':input_hashes,'responses':captures,'public_result':result,'error':error,'production_mutations':False,'scope':'NEW_CURRENT68_ONLY_NOT_OLD295_E_OR_WHOLE_STEP1','capture_hook_scope':'OBSERVE_UNCHANGED_ORIGINAL_GET_RETURN_VALUES_ONLY'})
path=root/('C-public-local-'+stamp+'.json')
with path.open('x',encoding='utf-8') as f:json.dump(value,f,sort_keys=True);f.write('\n')
print(json.dumps({'evidence':str(path),'state':result['state'] if result else 'BLOCKED','error':error,'http_captures':len(captures),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}))
