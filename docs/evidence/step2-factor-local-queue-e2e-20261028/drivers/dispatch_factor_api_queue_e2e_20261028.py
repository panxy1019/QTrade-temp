import hashlib,importlib.util,json,shlex,subprocess,tarfile,time
from datetime import UTC,datetime
from pathlib import Path
from uuid import uuid4
repo=Path('E:/QTrade-git')
revision='45de5bd2455b9ed6bdaddc1646d69e148846e3da'
name='factor-api-queue-e2e-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
local=Path('E:/QTrade-temp')/name;local.mkdir()
remote='/root/qtrade-local/operator-evidence/'+name
subprocess.run(['git','archive',revision,'-o',str(local/'source.tar')],cwd=repo,check=True)
def h(raw):return hashlib.sha256(raw).hexdigest()
with tarfile.open(local/'source.tar') as archive:
 files={m.name:{'bytes':m.size,'sha256':h(archive.extractfile(m).read())} for m in archive.getmembers() if m.isfile()}
manifest={'baseline_commit':revision,'overlays':{},'expected_files':files,'source_tar_sha256':h((local/'source.tar').read_bytes())}
(local/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
for n in ['factor_api_queue_e2e_20261028.py','stage_factor_api_queue_e2e_20261028.py']:
 (local/n).write_bytes((Path('E:/QTrade-temp')/n).read_bytes())
spec=importlib.util.spec_from_file_location('authorized_factor_api_queue','E:/QTrade-temp/authorized_cluster_read.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);client=m.connect()
print('LOCAL_ROOT',local,'SOURCE_FILES',len(files),flush=True)
try:
 s=client.open_sftp();s.mkdir(remote)
 for n in ['source.tar','source-manifest.json','factor_api_queue_e2e_20261028.py','stage_factor_api_queue_e2e_20261028.py']:
  s.put(str(local/n),remote+'/'+n)
 command='LIVE_TRADING_ENABLED=false PYTHONDONTWRITEBYTECODE=1 /root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python -P -B '+shlex.quote(remote+'/stage_factor_api_queue_e2e_20261028.py')+' '+shlex.quote(remote)
 _,out,err=client.exec_command(command,timeout=900)
 channel=out.channel;stdout=[];stderr=[]
 while not channel.exit_status_ready() or channel.recv_ready() or channel.recv_stderr_ready():
  if channel.recv_ready():
   value=channel.recv(65536).decode();stdout.append(value);print(value,end='',flush=True)
  if channel.recv_stderr_ready():
   value=channel.recv_stderr(65536).decode();stderr.append(value);print(value,end='',flush=True)
  time.sleep(.2)
 status=channel.recv_exit_status()
 (local/'remote-stdout.log').write_text(''.join(stdout));(local/'remote-stderr.log').write_text(''.join(stderr))
 (local/'dispatch-result.json').write_text(json.dumps({'status':status,'command':command,'remote_root':remote},indent=2)+'\n')
 print('OPERATOR_STATUS',status,flush=True)
 for n in ['resources-before.json','resources-after.json','operator-source-before.json','operator-source-after.json',
  'originals-before.json','originals-after.json','admission-before.json','admission-after.json',
  'controls-before.json','controls-after.json','ready-original-before.json','ready-original-after.json',
  'ready-mirror.json','staging.json','origins.json','operator-result.json','integration-stdout.log','integration-stderr.log',
  'http-exchanges.json','completion.json','definition.json','queue-envelope.json','e2e-result.json','source-before.json',
  'source-after.json','integration.xml','failure.json']:
  try:s.get(remote+'/'+n,str(local/n))
  except FileNotFoundError:pass
 s.close()
finally:client.close()
