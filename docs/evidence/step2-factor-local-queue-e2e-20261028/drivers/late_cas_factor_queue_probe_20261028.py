import importlib.util,json,secrets,sys
from pathlib import Path
owned=Path(sys.argv[1]);source=owned/'source'
spec=importlib.util.spec_from_file_location('fixed_integration_runner',str(owned/'factor_api_queue_e2e_20261028.py'))
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
root=owned/'late-cas-negative';root.mkdir()
queue,transport,head=runner.local_queue(root/'queue',source)
*_,seal,unseal=runner.load_modules(source)
key=secrets.token_hex(32)
pending,competitor='d'*32,'e'*32
original=transport.send_pack
trigger=True
def send(remote,update_refs,**kwargs):
 def late(refs):
  global trigger
  new_refs=update_refs(refs)
  if trigger:
   trigger=False
   queue.append('requests',competitor,seal({'job_id':competitor,'kind':'LATE_CAS_FIXTURE'},key))
  return new_refs
 return original(remote,late,**kwargs)
transport.send_pack=send
try:
 queue.append('requests',pending,seal({'job_id':pending,'kind':'LATE_CAS_FIXTURE'},key))
except ValueError as error:
 assert str(error)=='QUEUE_PUSH_NOT_ACCEPTED'
else:raise AssertionError('late ref race must reject')
records=queue.read()
assert 'requests/'+competitor in records and 'requests/'+pending not in records
result={'state':'PASS_LATE_CAS_AFTER_REF_ADVERTISEMENT','fixture_payloads_only':True,
 'error':'QUEUE_PUSH_NOT_ACCEPTED','competitor_ref_preserved':True,'pending_ref_not_published':True,
 'unreachable_imported_objects_possible':True,'production_queue_accessed':False,'bare_head':head()}
runner.save(root/'result.json',result)
runner.junit(root/'integration.xml','late-cas',['native_receiver_compare_and_swap_rejects_late_ref_change'])
print(json.dumps(result),flush=True)
