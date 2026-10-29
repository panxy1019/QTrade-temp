import importlib.util,json
from pathlib import Path
root=Path('E:/QTrade-temp/factor-api-queue-e2e-20261028T132322Z-3bcecd4a')
data=json.loads((root/'e2e-result.json').read_bytes())
base=json.loads((root/'staging.json').read_bytes())['nfs_base']
remote=base+'/workflow/jobs/'+data['job_id']
spec=importlib.util.spec_from_file_location('authorized_job','E:/QTrade-temp/authorized_cluster_read.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);c=m.connect()
try:
 s=c.open_sftp()
 for name in ['request.json','child.json','runner.log','output/registration.json','output/strategy-definition.json']:
  target=root/'job-artifacts'/name;target.parent.mkdir(parents=True,exist_ok=True);s.get(remote+'/'+name,str(target))
 s.close()
finally:c.close()
assert json.loads((root/'job-artifacts/output/registration.json').read_bytes())==json.loads((root/'completion.json').read_bytes())
assert json.loads((root/'job-artifacts/output/strategy-definition.json').read_bytes())==json.loads((root/'definition.json').read_bytes())
print('JOB_ARTIFACTS_MATCH',data['job_id'])
