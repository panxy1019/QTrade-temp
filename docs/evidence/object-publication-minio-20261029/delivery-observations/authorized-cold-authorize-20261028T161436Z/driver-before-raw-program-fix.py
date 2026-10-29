"""Execute only the reviewed738c one-shot plan after actual human approval."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime,timezone
import argparse,importlib.util,json,shlex,time
LOCAL=Path("E:/QTrade-temp")
ROOT="/root/qtrade-local/operator-evidence/publication-cold-recovery-20261028T143000Z"
BASE="/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade"
RELEASE=BASE+"/releases/fba51aade2ee2410dda087e95cf054e53375ed3a"
PLAN="9a459c194021e6cadc1cec8dd09ecb8f7e86d8c5cab81d972a1108e7edbf4fb8"
SOURCE="738c295d55d9b1a4081ca2547d2bfdad3ea59553d0102cc152bb34b3916917fa"
REFERENCE="Human user message 2026-10-29 Asia/Shanghai: 审阅完成，同意按照方案执行，请你继续推进; reviewed P0-2 single180 cold delivery plan9a459c19."
def connect():
 spec=importlib.util.spec_from_file_location("authorized_cluster_read",str(LOCAL/"authorized_cluster_read.py"));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m.connect()
def main():
 parser=argparse.ArgumentParser();parser.add_argument("mode",choices=["authorize","send"]);a=parser.parse_args()
 c=connect();started=time.monotonic();dest=LOCAL/"cluster-evidence"/("authorized-cold-"+a.mode+"-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"));dest.mkdir()
 try:
  if a.mode=="authorize":
   program="""from pathlib import Path
from hashlib import sha256
from datetime import datetime,timezone
import json,os,stat
root=Path(ROOT_VALUE)
assert root.is_dir() and all(not p.is_symlink() for p in (root,*root.parents))
assert sha256((root/'publication_cold_recovery_operator_20261028.py').read_bytes()).hexdigest()==SOURCE_VALUE
assert sha256((root/'target.json').read_bytes()).hexdigest()==PLAN_VALUE
assert not any(os.path.lexists(root/n) for n in ('USER_AUTHORIZATION.json','PREFLIGHT.json','PUSH_INTENT.json','SEND_STARTED.json'))
owner=Path('/proc/647839');assert (owner/'stat').read_text().rsplit(')',1)[1].split()[19]=='1022098698'
assert b'LIVE_TRADING_ENABLED=false\0' in (owner/'environ').read_bytes()
assert Path(os.readlink(owner/'cwd')).name=='fba51aade2ee2410dda087e95cf054e53375ed3a'
assert os.statvfs(root).f_bavail*os.statvfs(root).f_frsize>=2*1024**3
store=Path(BASE_VALUE);assert os.statvfs(store).f_bavail*os.statvfs(store).f_frsize>=100*1024**3
approval={'schema_version':'qtrade.one-shot-cold-user-authorization.v1','plan_sha256':PLAN_VALUE,'authorization_reference':REFERENCE_VALUE,'authorized_at':datetime.now(timezone.utc).isoformat(),'authorization_kind':'EXPLICIT_NEW_USER_APPROVAL_OF_EXACT_PREPARED_PLAN','one_shot_cold_push_seconds':180,'ordinary_budgets_unchanged':True,'automatic_retry':False,'force_push':False}
raw=(json.dumps(approval,sort_keys=True,indent=2)+'\n').encode()
with (root/'USER_AUTHORIZATION.json').open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
d=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
try:os.fsync(d)
finally:os.close(d)
print(json.dumps({'state':'NEW_EXPLICIT_HUMAN_AUTHORIZATION_DURABLE','approval_sha256':sha256(raw).hexdigest(),'plan_sha256':PLAN_VALUE,'authorized_at':approval['authorized_at'],'public_write':False,'send_started':False},sort_keys=True))
""".replace("ROOT_VALUE",repr(ROOT)).replace("SOURCE_VALUE",repr(SOURCE)).replace("PLAN_VALUE",repr(PLAN)).replace("REFERENCE_VALUE",repr(REFERENCE)).replace("BASE_VALUE",repr(BASE))
   _,out,err=c.exec_command(shlex.join([BASE+"/venv/bin/python","-B","-P","-"]),timeout=30);out.channel.sendall(program.encode());out.channel.shutdown_write()
  else:
   s=c.open_sftp()
   with s.open(ROOT+"/USER_AUTHORIZATION.json","rb") as f:raw=f.read(16385)
   assert len(raw)<=16384 and json.loads(raw)['plan_sha256']==PLAN
   authority=sha256(raw).hexdigest();s.close()
   argv=["env","LIVE_TRADING_ENABLED=false","PYTHONDONTWRITEBYTECODE=1","PYTHONNOUSERSITE=1","PYTHONSAFEPATH=1","PYTHONPATH="+RELEASE+"/src:"+RELEASE,BASE+"/venv/bin/python","-B","-P",ROOT+"/publication_cold_recovery_operator_20261028.py","send-cold","--expected-plan-sha256",PLAN,"--user-authorization-sha256",authority]
   _,out,err=c.exec_command(shlex.join(argv),timeout=360)
  result_raw=out.read();errors=err.read();rc=out.channel.recv_exit_status()
  (dest/"stdout.log").write_bytes(result_raw);(dest/"stderr.log").write_bytes(errors)
  s=c.open_sftp()
  for name in ['USER_AUTHORIZATION.json','PREFLIGHT.json','PUSH_INTENT.json','SEND_STARTED.json','PUSH_TELEMETRY.json','PUSH_RESULT.json','PUSH_UNCERTAIN.json','ALREADY_TARGET.json']:
   try:s.stat(ROOT+"/"+name)
   except FileNotFoundError:continue
   s.get(ROOT+"/"+name,str(dest/name))
  s.close()
  result=json.loads(result_raw) if result_raw else None
  metadata={'captured_at':datetime.now(timezone.utc).isoformat(),'mode':a.mode,'returncode':rc,'elapsed_seconds':time.monotonic()-started,'plan_sha256':PLAN,'operator_sha256':SOURCE,'result':result,'stdout_sha256':sha256(result_raw).hexdigest(),'stderr_sha256':sha256(errors).hexdigest(),'no_retry':True}
  (dest/'execution.json').write_text(json.dumps(metadata,sort_keys=True,indent=2)+'\n')
  print(json.dumps({'evidence':str(dest),**metadata},sort_keys=True));raise SystemExit(rc)
 finally:c.close()
if __name__=='__main__':main()
