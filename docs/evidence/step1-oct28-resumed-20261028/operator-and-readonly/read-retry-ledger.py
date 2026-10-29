import json,os
from pathlib import Path
from datetime import UTC,datetime
from hashlib import sha256
from qtrade.monitoring.data_freshness import AUTHORITY,signed
base=Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
request_id='fdd7836de313bb9520c1755e162a539ce7b54276a085b971855b3b6a86778bb2';root=base/'unified-data/qlib-preparation'/request_id
assert os.environ.get('LIVE_TRADING_ENABLED','false').lower()=='false'
def doc(p):
 raw=p.read_bytes();assert len(raw)<=2000000
 v=json.loads(raw);assert signed({k:x for k,x in v.items() if k!='evidence_sha256'})==v
 return {'path':str(p),'file_sha256':sha256(raw).hexdigest(),'bytes':len(raw),'document':v}
request=doc(root/'request.json');ledger=doc(root/'symbol-attempts.json');progress=doc(root/'progress.json')
assert request['document']['evidence_sha256']==request_id
binding=request['document']['batches'];entries=ledger['document']['entries']
retries={s:e for s,e in entries.items() if s in binding and not (root/'stocks'/s/'manifest.json').is_file()}
assert len(retries)<=10
stock_files={s:[{'name':p.name,'bytes':p.stat().st_size,'sha256':sha256(p.read_bytes()).hexdigest()} for p in sorted((root/'stocks'/s).glob('*')) if p.is_file() and p.stat().st_size<=2000000] for s in retries}
log=base/'unified-data/qlib-schedule/service.log'
with log.open('rb') as f:
 f.seek(max(0,log.stat().st_size-524288));tail=f.read()
failures=[]
for line in tail.splitlines():
 try:v=json.loads(line)
 except (ValueError,TypeError):continue
 matched=[x for x in v.get('failures',[]) if x.get('symbol') in retries]
 if matched:failures.append({'updated_at':v.get('updated_at'),'request_sha256':v.get('request_sha256'),'failures':matched,'source_line_sha256':sha256(line).hexdigest(),'source_line_utf8':line.decode('utf-8')})
source_hashes={n:sha256((Path.cwd()/n).read_bytes()).hexdigest() for n in ['scripts/prepare_qlib_incremental.py','scripts/pit_collection_queue.py','scripts/run_qlib_data_service.py']}
print(json.dumps(signed({**AUTHORITY,'kind':'READ_ONLY_FIXED_OCT28_PREPARATION_RETRY_LEDGER_DIAGNOSIS','checked_at':datetime.now(UTC).isoformat(),'request':request,'ledger':ledger,'progress':progress,'retry_entries':retries,'retry_stock_files':stock_files,'matched_service_log_failures':failures[-12:],'bounded_service_log_tail_sha256':sha256(tail).hexdigest(),'bounded_service_log_tail_bytes':len(tail),'source_root':str(Path.cwd()),'source_sha256':source_hashes,'production_mutations':False,'network_retrieval_executed':False}),ensure_ascii=False,sort_keys=True))
