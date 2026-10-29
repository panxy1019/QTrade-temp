import sys,json,hashlib,importlib.util,stat
from pathlib import Path
from datetime import UTC,datetime
root=Path(sys.argv[1])
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path); module=importlib.util.module_from_spec(spec)
 sys.modules[name]=module; spec.loader.exec_module(module); return module
op=load('reviewed_publisher_operator',root/'handover.py')
bridge=load('reviewed_publisher_bridge',root/'bridge.py')
inventory=json.loads((root/'git-runtime-inventory.json').read_bytes())
assert inventory['source_revision']==op.OLD_REVISION
proof=bridge.runtime_probe(op,294214,op.OLD_REVISION,inventory['files'])
info=bridge.SOCKET.stat()
identity={'device':info.st_dev,'inode':info.st_ino,'uid':info.st_uid}
assert stat.S_ISSOCK(info.st_mode)
rpc=bridge.UnixPublisherRPC(identity)
native=rpc('supervisor.getProcessInfo',(bridge.PROGRAM,))
assert native['pid']==294214 and native['state']==20
result={'observed_at':datetime.now(UTC).isoformat(),'state':'READONLY_OLD_RUNTIME_VERIFIED',
 'runtime_proof':proof,'native_state':native['statename'],'socket_identity':identity,
 'inventory_raw_sha256':hashlib.sha256((root/'git-runtime-inventory.json').read_bytes()).hexdigest(),
 'signal_calls':0,'wrapper_writes':0,'production_actions':False}
(root/'receipt.json').write_text(json.dumps(result,sort_keys=True)+'\n')
print(json.dumps({'state':result['state'],'observed_at':result['observed_at'],'verified_runtime_files':len(inventory['files']),
 'pid':proof['pid'],'scope':proof['import_proof_scope'],'production_actions':False}))
