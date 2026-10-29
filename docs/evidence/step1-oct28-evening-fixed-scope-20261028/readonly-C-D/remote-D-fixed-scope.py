
import json,hashlib
from pathlib import Path
from qtrade.monitoring.data_freshness import AUTHORITY,signed
from scripts.audit_qlib_candidate_transition import audit
base=Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade');local=Path('/root/qtrade-local')
path=Path.cwd()/'scripts/audit_qlib_candidate_transition.py'
assert hashlib.sha256(path.read_bytes()).hexdigest()=='0a4d7c85933aa4f6e6a829d5a20265cf93f70d57462b13d42d2e863cadfcd878'
model='98d27455a7cb224ef3ecbd7cf850455f386fbb93baf8137a4d789e142ab91795'
index=json.loads((base/'online-research/control/models'/(model+'.json')).read_bytes())
result=audit(base,local/'online-models.sqlite',local/'workflow.sqlite',local/'qlib-versions.sqlite',
 model,'b82baaac956745f4e02a520a35b2aa277b0037510a4d164d798db803ce77e0d5',
 '29589b4486a09159eb3a07153c54fba609fbc80b52c168dd665cd46660be8487',
 '6f1ec61bda1a28d918dd331ad0e0ae6ea7ebcc39320f8e4dade424bf726bd238')
assert hashlib.sha256(path.read_bytes()).hexdigest()=='0a4d7c85933aa4f6e6a829d5a20265cf93f70d57462b13d42d2e863cadfcd878'
print(json.dumps(signed({**AUTHORITY,'kind':'ACTUAL_D_OCT28_FROZEN_RIDGE_AND_OLD_ACTIVE_SCOPE','audit':result,
 'auditor_revision':'9846a7f30774d6ae4b4d41f02b493c5246be87d9','auditor_source_sha256':'0a4d7c85933aa4f6e6a829d5a20265cf93f70d57462b13d42d2e863cadfcd878',
 'model_index':index,'production_mutations':False,'model_activated':False,'jobs_enqueued':False}),sort_keys=True))
