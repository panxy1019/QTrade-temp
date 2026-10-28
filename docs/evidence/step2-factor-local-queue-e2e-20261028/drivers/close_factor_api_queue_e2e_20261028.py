import importlib.util,json,shlex
from pathlib import Path
r=Path('E:/QTrade-temp/factor-api-queue-e2e-20261028T132322Z-3bcecd4a')
remote='/root/qtrade-local/operator-evidence/'+r.name
code=r"""
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1])
def h(p):
 d=hashlib.sha256()
 with p.open('rb') as stream:
  for value in iter(lambda:stream.read(1024*1024),b''):d.update(value)
 return d.hexdigest()
def inventory(folder):return {p.relative_to(folder).as_posix():{'sha256':h(p),'bytes':p.stat().st_size} for p in folder.rglob('*') if p.is_file()}
base=Path(json.loads((root/'staging.json').read_bytes())['nfs_base'])
mirror=base/'unified-data/qlib-versions/versions/09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407'
values=inventory(mirror)
assert values==json.loads((root/'ready-original-before.json').read_bytes())
assert inventory(root/'source')==json.loads((root/'source-manifest.json').read_bytes())['expected_files']
(root/'ready-mirror-after.json').write_text(json.dumps(values,indent=2)+'\n')
(root/'closure.json').write_text(json.dumps({'state':'MIRROR_AND_EXACT_GIT_SOURCE_STILL_IMMUTABLE_AFTER_COLLECTION',
 'source_files':3001,'ready_mirror_files':len(values),'ready_mirror_bytes':sum(v['bytes'] for v in values.values()),
 'source_unchanged':True,'ready_mirror_unchanged':True},indent=2)+'\n')
print((root/'closure.json').read_text(),flush=True)
"""
(r/'close_remote.py').write_text(code)
spec=importlib.util.spec_from_file_location('authorized_closure','E:/QTrade-temp/authorized_cluster_read.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);c=m.connect()
try:
 s=c.open_sftp();s.put(str(r/'close_remote.py'),remote+'/close_remote.py')
 _,out,err=c.exec_command('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/venv/bin/python -P -B '+shlex.quote(remote+'/close_remote.py')+' '+shlex.quote(remote),timeout=180)
 stdout,stderr=out.read().decode(),err.read().decode();status=out.channel.recv_exit_status()
 (r/'closure-stdout.log').write_text(stdout);(r/'closure-stderr.log').write_text(stderr)
 print(status,stdout,stderr,flush=True);assert status==0
 for n in ['ready-mirror-after.json','closure.json']:s.get(remote+'/'+n,str(r/n))
 s.close()
finally:c.close()
