from pathlib import Path
from datetime import UTC,datetime
import sys,json,shlex
sys.path.insert(0,'E:/QTrade-temp')
from authorized_cluster_read import connect
root=Path(__file__).resolve().parent
base='/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade';source='/root/qtrade-local/operator-evidence/graded-quality-458552d-20261028/source'
client=connect()
try:
 stdin,stdout,stderr=client.exec_command('cd '+shlex.quote(source)+' && env LIVE_TRADING_ENABLED=false PYTHONPATH=src:. '+shlex.quote(base+'/venv/bin/python')+' -B -',timeout=90)
 stdin.write((root/'read-retry-ledger.py').read_text(encoding='utf-8'));stdin.flush();stdin.channel.shutdown_write()
 raw,errors=stdout.read(),stderr.read();rc=stdout.channel.recv_exit_status()
finally:client.close()
path=root/('retry-ledger-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')+('.json' if rc==0 else '.error'))
with path.open('xb') as f:f.write(raw if rc==0 else errors)
if rc:print(json.dumps({'exit':rc,'failure':str(path)}));raise SystemExit(rc)
v=json.loads(raw)
print(json.dumps({'evidence':str(path),'checked_at':v['checked_at'],'retry_entries':v['retry_entries'],'retry_stock_files':v['retry_stock_files'],'matched_failures':[{k:x[k] for k in ['updated_at','request_sha256','failures','source_line_sha256']} for x in v['matched_service_log_failures']],'source_sha256':v['source_sha256']},ensure_ascii=False))
