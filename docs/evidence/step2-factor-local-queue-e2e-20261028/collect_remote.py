
import gzip,hashlib,json,subprocess,sys,xml.etree.ElementTree as ET
from pathlib import Path
owned=Path(sys.argv[1]);source=owned/'source'
protocol=owned/'linux-protocol-negative';protocol.mkdir()
python=sys.executable
command=[python,'-P','-B',str(owned/'factor_api_queue_e2e_20261028.py'),'--mode','negative','--source',str(source),'--owned',str(protocol)]
run=subprocess.run(command,capture_output=True,text=True,timeout=30)
(protocol/'stdout.log').write_text(run.stdout);(protocol/'stderr.log').write_text(run.stderr)
assert run.returncode==0
sys.path[:0]=[str(source/'src'),str(source)]
import importlib.util
spec=importlib.util.spec_from_file_location('isolated_http_runner',str(owned/'factor_api_queue_e2e_20261028.py'))
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qtrade.api import research_workflow as api
from qtrade.monitoring.data_freshness import signed
from unittest.mock import patch
base=Path(json.loads((owned/'staging.json').read_bytes())['nfs_base'])
job=json.loads((owned/'e2e-result.json').read_bytes())['job_id']
records=[]
with runner.static_server(base/'workflow/public') as url:
 with patch.object(api,'PUBLIC_ROOT',url),patch.object(api.GitQueue,'from_environment',side_effect=AssertionError('READONLY_REPLAY_MUST_NOT_USE_QUEUE')):
  app=FastAPI();app.include_router(api.router)
  with TestClient(app,base_url='https://factor-e2e.invalid') as http:
   for label,path in [('completion','/research/factor-strategies/jobs/'+job),('catalog','/research/factor-strategies/catalog')]:
    response=http.get(path)
    assert response.status_code==200
    value=response.json()
    assert signed({k:v for k,v in value.items() if k!='evidence_sha256'})==value
    raw=response.content
    (owned/('raw-readonly-'+label+'-response.json')).write_bytes(raw)
    records.append({'scope':'READONLY_ASGI_REPLAY_NO_AUTH_OR_QUEUE','method':'GET','path':path,'status':200,
     'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'evidence_sha256':value['evidence_sha256'],
     'headers':{k:response.headers[k] for k in ['cache-control','content-type'] if k in response.headers}})
(owned/'raw-readonly-http-proof.json').write_text(json.dumps(records,indent=2)+'\n')
proof={}
for name in ['workflow.sqlite','strategies.sqlite','online-models.sqlite','qlib-versions.sqlite']:
 p=owned/'db'/name
 raw=p.read_bytes();assert not any(p.with_name(p.name+x).exists() and p.with_name(p.name+x).stat().st_size for x in ['-wal','-journal'])
 (owned/(name+'.gz')).write_bytes(gzip.compress(raw,mtime=0))
 proof[name]={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
queue=owned/'http-queue/queue.git'
bundle=owned/'local-queue.bundle'
subprocess.run(['git','-C',str(queue),'bundle','create',str(bundle),'--all'],check=True,capture_output=True,timeout=30)
proof['local-queue.bundle']={'bytes':bundle.stat().st_size,'sha256':hashlib.sha256(bundle.read_bytes()).hexdigest()}
(owned/'database-and-Git-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
print(json.dumps({'protocol':json.loads((protocol/'protocol-negative.json').read_bytes()),'raw_http':records,'snapshots':proof}),flush=True)
