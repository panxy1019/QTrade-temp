"""One short RO SQL capture; credentials/helper body never read or printed."""
from pathlib import Path
from datetime import datetime,timezone
from hashlib import sha256
import json
import shlex
import sys

sys.path.insert(0,'E:/QTrade-temp')
from authorized_cluster_read import connect

REMOTE=r'''
from pathlib import Path
from datetime import datetime,timezone
from hashlib import sha256
import json,sqlite3
from contextlib import closing
L=Path('/root/qtrade-local')
def ro(path):
    c=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
    c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('BEGIN');return c
def bounded(c,q,v=()):
    rows=c.execute(q+' LIMIT 1001',v).fetchall()
    if len(rows)>1000:raise ValueError('READONLY_CAPTURE_BOUND')
    return [dict(r) for r in rows]
candidate='98d27455a7cb224ef3ecbd7cf850455f386fbb93baf8137a4d789e142ab91795'
later='b82baaac956745f4e02a520a35b2aa277b0037510a4d164d798db803ce77e0d5'
with closing(ro(L/'online-models.sqlite')) as c:
    active=bounded(c,'SELECT m.model_id,m.state,m.activation_count,m.activated_at FROM active_model a JOIN models m ON a.model_id=m.model_id WHERE a.singleton=1')
    controls=[]
    for r in bounded(c,"SELECT sequence,model_id,at,payload FROM model_events WHERE kind='CONTROL_APPLIED' ORDER BY sequence"):
        event=json.loads(r.pop('payload'));receipt=event['details']
        r.update(approval_sha256=receipt['evidence_sha256'],event_sha256=event['evidence_sha256']);controls.append(r)
    staged=bounded(c,'SELECT model_id,state,activation_count,activated_at FROM models WHERE model_id=?',(candidate,))
    validations=bounded(c,'SELECT validation_id,model_id,data_version_id FROM model_validations WHERE model_id=? ORDER BY validation_id',(candidate,))
    lifecycle=bounded(c,'SELECT sequence,model_id,kind,at FROM model_events WHERE model_id=? ORDER BY sequence',(candidate,))
with closing(ro(L/'workflow.sqlite')) as c:
    jobs=bounded(c,"SELECT id,state,updated,error FROM jobs WHERE json_extract(request,'$.kind')='QLIB_MODEL_VALIDATION' AND json_extract(request,'$.model_id')=? AND json_extract(request,'$.data_version_id')=? ORDER BY id",(candidate,later))
root=L/'forward-research';raw=(root/'roster.json').read_bytes();roster=json.loads(raw)['books'];books=[]
if len(roster)>32:raise ValueError('ROSTER_BOUND')
for identity,config in sorted(roster.items()):
    with closing(ro(root/identity/'forward.sqlite')) as c:
        sessions=bounded(c,'SELECT day,target_id,status FROM sessions WHERE day=?',('2026-10-28',))
        targets=bounded(c,'SELECT id,execution_at,frozen_at FROM targets WHERE execution_at LIKE ? ORDER BY execution_at',('2026-10-28%',))
    admissions=[]
    for target in targets:
        path=root/identity/'admissions'/(target['id']+'.json')
        if path.is_file() and not path.is_symlink():
            admission_raw=path.read_bytes();admission=json.loads(admission_raw)
            admissions.append({'target_id':admission['target_id'],'verified_at':admission['verified_at'],
                'sha256':sha256(admission_raw).hexdigest(),'archive':admission['archive']})
    books.append({'book_id':identity,'strategy_id':config['strategy_id'],'mode':config['mode'],
        'oct28_sessions':sessions,'oct28_targets':targets,'frozen_admissions':admissions})
print(json.dumps({'captured_at':datetime.now(timezone.utc).isoformat(),'active':active,'controls':controls,
    'candidate':staged,'candidate_validations':validations,'candidate_events':lifecycle,
    'oct28_candidate_jobs':jobs,'roster_sha256':sha256(raw).hexdigest(),'books':books,
    'read_only':True,'jobs_enqueued':False,'model_activated':False,'paper_recovered':False},sort_keys=True))
'''

stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
dest=Path('E:/QTrade-temp/cluster-evidence')/('step1-scope-facts-'+stamp)
dest.mkdir(exist_ok=False)
client=connect()
try:
    command=shlex.join(['/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python','-B','-'])
    inp,out,err=client.exec_command(command,timeout=45)
    inp.write(REMOTE);inp.channel.shutdown_write()
    raw=out.read();errors=err.read();rc=out.channel.recv_exit_status()
    (dest/'facts.stdout').write_bytes(raw);(dest/'facts.stderr').write_bytes(errors)
    if rc or errors:raise ValueError('READONLY_CAPTURE_FAILED')
    value=json.loads(raw)
    (dest/'facts.json').write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')
    print(json.dumps({'evidence':str(dest),'facts_sha256':sha256(raw).hexdigest(),
        'captured_at':value['captured_at'],'book_count':len(value['books']),
        'oct28_completed_sessions':sum(r['status']=='COMPLETE' for b in value['books'] for r in b['oct28_sessions']),
        'candidate_later_jobs':len(value['oct28_candidate_jobs']),
        'candidate_proofs':len(value['candidate_validations']),'active':value['active']},sort_keys=True))
finally:client.close()
