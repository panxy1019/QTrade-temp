import importlib.util,json,shlex
from pathlib import Path
from datetime import UTC,datetime
name='factor-legacy-s2c13-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
local=Path('E:/QTrade-temp')/name;local.mkdir();remote='/root/qtrade-local/operator-evidence/'+name
spec=importlib.util.spec_from_file_location('authorized_legacy','E:/QTrade-temp/authorized_cluster_read.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);c=m.connect()
try:
 s=c.open_sftp();s.mkdir(remote);s.put('E:/QTrade-temp/build_factor_s2c13_archive_20261028.py',remote+'/build_factor_s2c13_archive_20261028.py')
 cmd='LIVE_TRADING_ENABLED=false /root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python -P -B '+shlex.quote(remote+'/build_factor_s2c13_archive_20261028.py')+' '+shlex.quote(remote)
 _,out,err=c.exec_command(cmd,timeout=120);stdout,stderr=out.read().decode(),err.read().decode();status=out.channel.recv_exit_status()
 (local/'stdout.log').write_text(stdout);(local/'stderr.log').write_text(stderr);(local/'location.json').write_text(json.dumps({'remote':remote,'status':status}))
 print(local,status,stdout,stderr,flush=True)
 for n in ['factor_strategy_s2c13_archived.json','legacy-provenance.json']:
  try:s.get(remote+'/'+n,str(local/n))
  except FileNotFoundError:pass
 assert status==0
 Path('E:/QTrade-git/tests/fixtures/factor_strategy_s2c13_archived.json').write_bytes((local/'factor_strategy_s2c13_archived.json').read_bytes())
 s.close()
finally:c.close()
