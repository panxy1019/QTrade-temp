"""Read-only publisher layer evidence; no private config or remote URLs emitted."""
import json
import shlex
from datetime import UTC, datetime
from pathlib import Path
from authorized_cluster_read import connect

B = '/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade'
code = r'''
import json, subprocess, hashlib, os
from pathlib import Path
from datetime import UTC, datetime
b=Path(BASE)
def capture(path):
 raw=path.read_bytes(); v=json.loads(raw)
 return {'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'document':v}
owners=[]
for p in Path('/proc').iterdir():
 if not p.name.isdigit(): continue
 try:
  args=(p/'cmdline').read_bytes().split(b'\0')
  if b'scripts.publish_research_snapshot' not in args: continue
  env=dict(x.split(b'=',1) for x in (p/'environ').read_bytes().split(b'\0') if b'=' in x)
  opts={k.decode():args[args.index(k)+1].decode() for k in (b'--root',b'--publication',b'--interval-seconds') if k in args}
  pub=Path(opts['--publication']); root=Path(opts['--root'])
  git=subprocess.run(['git','-C',str(pub),'rev-parse','HEAD'],capture_output=True,text=True,timeout=5)
  assert git.returncode==0
  owners.append({'pid':int(p.name),'cwd':str((p/'cwd').resolve()),'live':env.get(b'LIVE_TRADING_ENABLED',b'').decode(),
   'interval_seconds':opts.get('--interval-seconds','300'),'public_git_head':git.stdout.strip(),
   'outer_publication':capture(root/'publication-state.json'),
   'public_checkout_index':capture(pub/'unified-market.json')})
 except (FileNotFoundError,ProcessLookupError): continue
assert len(owners)==1
out={'observed_at':datetime.now(UTC).isoformat(),'owners':owners,
 'export_attempt':capture(b/'unified-data/market-export-attempt.json'),
 'last_successful_export':capture(b/'unified-data/market-export-state.json'),
 'local_index':capture(b/'unified-data/market-public/unified-market.json'),
 'readonly':True}
print(json.dumps(out))
'''.replace('Path(BASE)', 'Path('+repr(B)+')')
c=connect()
try:
 cmd=shlex.quote(B+'/venv/bin/python')+" -B - <<'PY'\n"+code+'\nPY'
 _,out,err=c.exec_command(cmd,timeout=35)
 raw=out.read(); error=err.read(); rc=out.channel.recv_exit_status()
 dst=Path('E:/QTrade-temp/cluster-evidence')/('publisher-layers-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
 dst.write_bytes(raw if rc==0 else error)
 if rc: print({'exit':rc,'evidence':str(dst)});raise SystemExit(rc)
 v=json.loads(raw); owner=v['owners'][0]
 print(json.dumps({'observed_at':v['observed_at'],'pid':owner['pid'],'source':owner['cwd'],
  'git_head':owner['public_git_head'],'outer_publication':owner['outer_publication']['document'],
  'export_attempt':v['export_attempt']['document'],
  'last_successful_export':v['last_successful_export']['document'],
  'local_index_at':v['local_index']['document']['published_at'],
  'checkout_index_at':owner['public_checkout_index']['document']['published_at'],'evidence':str(dst)}))
finally:c.close()
