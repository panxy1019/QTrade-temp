from pathlib import Path
from datetime import datetime,timezone
from hashlib import sha256
import importlib.util
import json
import shlex
import uuid

ROOT=Path('E:/QTrade-temp/minio-bootstrap-20261029')
name='build-mirror-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
local=ROOT/name;local.mkdir()
remote='/root/qtrade-local/operator-evidence/minio-protocol-20261029T163700Z/'+name
script=Path('E:/QTrade-temp/build_minio_protocol_mirror_fixture_20261029.py')
(local/script.name).write_bytes(script.read_bytes())
(local/'dispatch.json').write_text(json.dumps({'remote_root':remote,'builder_sha256':sha256(script.read_bytes()).hexdigest(),'source_commit':'07c3a429bfed433e49018cb0f78a52145d4bedeb','service_started':False},indent=2)+'\n')
s=importlib.util.spec_from_file_location('minio_mirror_build_connect','E:/QTrade-temp/authorized_cluster_read.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m);c=m.connect()
try:
 sftp=c.open_sftp();sftp.mkdir(remote);sftp.put(str(script),remote+'/'+script.name)
 with sftp.open(remote+'/'+script.name,'rb') as handle:assert sha256(handle.read()).digest()==sha256(script.read_bytes()).digest()
 command=shlex.join(['env','LIVE_TRADING_ENABLED=false','/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python','-B','-P',remote+'/'+script.name])
 _,out,err=c.exec_command(command,timeout=700)
 stdout=out.read();stderr=err.read();rc=out.channel.recv_exit_status()
 (local/'builder.stdout.json').write_bytes(stdout);(local/'builder.private.stderr').write_bytes(stderr)
 for entry in sftp.listdir(remote):
  if entry.endswith('.json') and entry!='dispatch.json':sftp.get(remote+'/'+entry,str(local/entry))
 (local/'dispatch-result.json').write_text(json.dumps({'returncode':rc,'stdout_sha256':sha256(stdout).hexdigest(),'stderr_sha256':sha256(stderr).hexdigest(),'stderr_bytes':len(stderr)},indent=2)+'\n')
 print(json.dumps({'local_evidence':str(local),'remote_root':remote,'returncode':rc,'stdout_bytes':len(stdout),'stderr_bytes':len(stderr)}))
finally:c.close()
