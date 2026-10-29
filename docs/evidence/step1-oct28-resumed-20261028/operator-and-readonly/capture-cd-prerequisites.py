from pathlib import Path
from datetime import UTC,datetime
import sys,json,shlex
sys.path.insert(0,'E:/QTrade-temp')
from authorized_cluster_read import connect
root=Path(__file__).resolve().parent
base='/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade';source=base+'/releases/9846a7f30774d6ae4b4d41f02b493c5246be87d9'
client=connect()
try:
 stdin,stdout,stderr=client.exec_command('cd '+shlex.quote(source)+' && env LIVE_TRADING_ENABLED=false PYTHONPATH=src:. '+shlex.quote(base+'/venv/bin/python')+' -B -',timeout=90)
 stdin.write((root/'read-cd-prerequisites.py').read_text(encoding='utf-8'));stdin.flush();stdin.channel.shutdown_write()
 raw,errors=stdout.read(),stderr.read();rc=stdout.channel.recv_exit_status()
finally:client.close()
path=root/('cd-prerequisites-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')+('.json' if rc==0 else '.error'))
with path.open('xb') as f:f.write(raw if rc==0 else errors)
if rc:print(json.dumps({'exit':rc,'failure':str(path)}));raise SystemExit(rc)
v=json.loads(raw)
print(json.dumps({'evidence':str(path),'checked_at':v['checked_at'],'context_unchanged':v['context_before']==v['context_after'],'head':v['data']['head'],'versions':[{'version_id':x['version_id'],'actual_end':x['actual_end']} for x in v['data']['versions']],'files':{k:({'exists':True,'state':x['document'].get('state'),'updated_at':x['document'].get('updated_at'),'required_day':x['document'].get('required_day'),'preparation':x['document'].get('preparation')} if x['exists'] else {'exists':False}) for k,x in v['files'].items()},'pending':v['workflow']['pending'],'validation_jobs':[{k:x.get(k) for k in ['id','state','updated','error','model_id','data_version_id']} for x in v['workflow']['validation_jobs']],'models':v['models']['models'],'predictions':[{'prediction_id':x['prediction_id'],'state':x['state'],'updated_at':x['updated_at'],'error_code':x['error_code']} for x in v['models']['predictions']]},ensure_ascii=False))
