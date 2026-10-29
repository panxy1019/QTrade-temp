import importlib.util,json,shlex
from pathlib import Path
r=Path('E:/QTrade-temp/factor-api-queue-e2e-20261028T132322Z-3bcecd4a');remote='/root/qtrade-local/operator-evidence/'+r.name
spec=importlib.util.spec_from_file_location('authorized_latecas','E:/QTrade-temp/authorized_cluster_read.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);c=m.connect()
try:
 s=c.open_sftp();s.put('E:/QTrade-temp/late_cas_factor_queue_probe_20261028.py',remote+'/late_cas_factor_queue_probe_20261028.py')
 cmd='/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python -P -B '+shlex.quote(remote+'/late_cas_factor_queue_probe_20261028.py')+' '+shlex.quote(remote)
 _,out,err=c.exec_command(cmd,timeout=30);stdout,stderr=out.read().decode(),err.read().decode();status=out.channel.recv_exit_status();print(status,stdout,stderr)
 (r/'late-cas-stdout.log').write_text(stdout);(r/'late-cas-stderr.log').write_text(stderr)
 assert status==0
 for name in ['result.json','integration.xml']:
  target=r/'late-cas-negative'/name;target.parent.mkdir(exist_ok=True);s.get(remote+'/late-cas-negative/'+name,str(target))
 s.close()
finally:c.close()
