import gzip,hashlib,json,sqlite3,subprocess,tempfile
from pathlib import Path
root=Path('E:/QTrade-temp/factor-api-queue-e2e-20261028T132322Z-3bcecd4a')
source=json.loads((root/'source-manifest.json').read_bytes())
def h(raw):return hashlib.sha256(raw).hexdigest()
def d(value):return h(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode())
assert source['baseline_commit']=='45de5bd2455b9ed6bdaddc1646d69e148846e3da' and source['overlays']=={}
assert len(source['expected_files'])==3001
for name in ['operator-source-before.json','operator-source-after.json','source-before.json','source-after.json']:
 assert json.loads((root/name).read_bytes())==source['expected_files']
for before,after in [('originals-before.json','originals-after.json'),('ready-original-before.json','ready-original-after.json'),('admission-before.json','admission-after.json'),('controls-before.json','controls-after.json')]:
 assert json.loads((root/before).read_bytes())==json.loads((root/after).read_bytes())
mirror=json.loads((root/'ready-mirror.json').read_bytes())
assert mirror['files']==json.loads((root/'ready-original-before.json').read_bytes())
assert mirror['file_count']==18829 and mirror['bytes']==786448967
for name in ['resources-before.json','resources-after.json']:
 free=json.loads((root/name).read_bytes());assert free['nfs']>=100*1024**3 and free['local']>=2*1024**3
receipt=json.loads((root/'completion.json').read_bytes())
assert receipt['evidence_sha256']==d({k:v for k,v in receipt.items() if k!='evidence_sha256'})
definition=json.loads((root/'definition.json').read_bytes())
assert receipt['contract_sha256']==definition['evidence_sha256'] and receipt['strategy_version']==definition['strategy_version']
assert receipt['source_revision']==source['baseline_commit']
proof=json.loads((root/'database-and-Git-proof.json').read_bytes())
review=root/'independent-readonly';review.mkdir()
for name in ['workflow.sqlite','strategies.sqlite','online-models.sqlite','qlib-versions.sqlite']:
 raw=gzip.decompress((root/(name+'.gz')).read_bytes())
 assert h(raw)==proof[name]['sha256'];(review/name).write_bytes(raw)
def c(name):return sqlite3.connect((review/name).resolve().as_uri()+'?mode=ro',uri=True)
with c('workflow.sqlite') as connection:
 rows=connection.execute('SELECT id,request,state,result FROM jobs').fetchall()
 assert len(rows)==1 and rows[0][0]==receipt['job_id'] and rows[0][2]=='COMPLETE'
 request=json.loads(rows[0][1]);result=json.loads(rows[0][3])
 assert request['kind']=='FACTOR_STRATEGY_REGISTER_V1' and request['live_trading_enabled'] is False
 assert result['detail_sha256']==receipt['evidence_sha256']
 assert json.loads(connection.execute('SELECT policy FROM rolling_policy').fetchone()[0])['mode']=='OFF'
with c('strategies.sqlite') as connection:
 counts={table:connection.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ['strategies','strategy_research_contracts','strategy_research_jobs','strategy_transitions']}
 assert list(counts.values())==[1,1,1,1]
 assert connection.execute('SELECT status,role FROM strategies').fetchall()==[('EXPERIMENT','NONE')]
 assert connection.execute('SELECT COUNT(*) FROM strategy_lineage').fetchone()[0]==0
 assert json.loads(connection.execute('SELECT contract_json FROM strategy_research_contracts').fetchone()[0])==definition
with c('online-models.sqlite') as connection:
 assert connection.execute('SELECT COUNT(*) FROM models').fetchone()[0]==0
 assert connection.execute('SELECT COUNT(*) FROM active_model').fetchone()[0]==0
bare=review/'queue.git'
subprocess.run(['git','clone','--bare',str(root/'local-queue.bundle'),str(bare)],check=True,capture_output=True)
head=subprocess.check_output(['git','--git-dir',str(bare),'rev-parse','HEAD'],text=True).strip()
assert head==json.loads((root/'e2e-result.json').read_bytes())['git_head']
paths=subprocess.check_output(['git','--git-dir',str(bare),'ls-tree','-r','--name-only','HEAD'],text=True).splitlines()
assert len([p for p in paths if p.startswith('requests/')])==3
blob=subprocess.check_output(['git','--git-dir',str(bare),'show','HEAD:requests/'+receipt['job_id']+'.json'])
envelope=json.loads(blob)
assert envelope==json.loads((root/'queue-envelope.json').read_bytes())
assert {k:v for k,v in envelope.items() if k!='queue_signature'}==request
assert request['factor_spec_sha256']==d(request['factor_spec'])
raw_http=json.loads((root/'raw-readonly-http-proof.json').read_bytes())
for item in raw_http:
 label='completion' if '/jobs/' in item['path'] else 'catalog'
 raw=(root/('raw-readonly-'+label+'-response.json')).read_bytes()
 assert len(raw)==item['bytes'] and h(raw)==item['sha256']
 if label=='completion':assert json.loads(raw)==receipt
result={'state':'PASS_INDEPENDENT_OFFLINE_BYTES_AND_SQL_REVIEW','source_files':3001,
 'mirror_files':18829,'mirror_bytes':786448967,'registry_counts':counts,'workflow_jobs':1,
 'models':0,'active_models':0,'rolling_mode':'OFF','queue_request_files':3,
 'valid_job_id':receipt['job_id'],'source_revision':receipt['source_revision'],
 'receipt_sha256':receipt['evidence_sha256'],'git_head':head,
 'archive_originals_source_admission_controls_equal':True,
 'HMAC_secret_not_persisted':'HMAC executable checks were recorded during the live harness; no key exported'}
(root/'independent-review.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result),flush=True)
