import importlib.util,json,shlex
from pathlib import Path
local=Path('E:/QTrade-temp/factor-queue-s2d-20261028T123014Z-b12b8041')
remote='/root/qtrade-local/operator-evidence/'+local.name
spec=importlib.util.spec_from_file_location('authorized_factor_queue','E:/QTrade-temp/authorized_cluster_read.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
c=m.connect()
try:
 s=c.open_sftp()
 s.put('E:/QTrade-temp/run_factor_queue_actual_20261028.py',remote+'/run_factor_queue_actual_20261028.py')
 command='LIVE_TRADING_ENABLED=false PYTHONDONTWRITEBYTECODE=1 /root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python -P -B '+shlex.quote(remote+'/run_factor_queue_actual_20261028.py')+' '+shlex.quote(remote)
 print('ACTUAL_REGISTER_ROOT',remote,flush=True)
 _,out,err=c.exec_command(command,timeout=480)
 stdout,stderr=out.read().decode(),err.read().decode();status=out.channel.recv_exit_status()
 (local/'actual-operator-stdout.log').write_text(stdout)
 (local/'actual-operator-stderr.log').write_text(stderr)
 (local/'actual-operator-status.json').write_text(json.dumps({'status':status,'command':command},indent=2))
 print('STATUS',status,flush=True);print(stdout[-12000:],flush=True);print(stderr,flush=True)
 names=['run_factor_queue_actual_20261028.py','actual_guarded_runner.py','actual-acceptance.json','actual-catalog.json','actual-preview.json','actual-request.json','actual-controls-before.json','actual-controls-after.json','actual-resources-before.json','actual-resources-after.json','actual-originals-before.json','actual-originals-after.json','actual-source-before.json','actual-source-after.json','actual-child-stdout.log','actual-child-stderr.log','actual-child-write-guard.json','approved-factor-archive/index.json','actual-child-output/registration.json','actual-child-output/strategy-definition.json']
 for name in names:
  target=local/name;target.parent.mkdir(parents=True,exist_ok=True)
  try:s.get(remote+'/'+name,str(target))
  except FileNotFoundError:pass
 s.close()
finally:c.close()
