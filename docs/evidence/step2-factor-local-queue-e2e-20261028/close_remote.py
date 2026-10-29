
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
