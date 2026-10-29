import gzip,hashlib,importlib.util,json,shlex
from pathlib import Path
names=['factor-queue-s2d-20261028T123014Z-b12b8041','factor-queue-s2d-final-20261028T124314Z-e0813807']
spec=importlib.util.spec_from_file_location('authorized_databases','E:/QTrade-temp/authorized_cluster_read.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);c=m.connect()
try:
 s=c.open_sftp()
 for name in names:
  remote='/root/qtrade-local/operator-evidence/'+name
  local=Path('E:/QTrade-temp')/name
  code="import hashlib,json;from pathlib import Path;p=Path("+repr(remote+"/isolated-strategies.sqlite")+");print(json.dumps({'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'nonempty_journals':[s for s in ['-wal','-journal'] if p.with_name(p.name+s).exists() and p.with_name(p.name+s).stat().st_size]}))"
  _,out,err=c.exec_command('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python -P -B -c '+shlex.quote(code),timeout=30)
  proof=json.loads(out.read().decode());assert not err.read() and out.channel.recv_exit_status()==0
  assert proof['nonempty_journals']==[]
  s.get(remote+'/isolated-strategies.sqlite',str(local/'isolated-strategies.sqlite'))
  raw=(local/'isolated-strategies.sqlite').read_bytes()
  assert len(raw)==proof['bytes'] and hashlib.sha256(raw).hexdigest()==proof['sha256']
  (local/'isolated-strategies.sqlite.gz').write_bytes(gzip.compress(raw,mtime=0))
  (local/'database-transfer-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
  print(name,proof,flush=True)
 s.close()
finally:c.close()
