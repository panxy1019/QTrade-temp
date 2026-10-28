from pathlib import Path
from hashlib import sha256
from datetime import datetime, timezone
import importlib.util,json,shlex,stat,time
LOCAL=Path("E:/QTrade-temp/publication-cold-recovery-final-20261028T145240Z-ad5676c1")
REMOTE="/root/qtrade-local/operator-evidence/publication-cold-offline-final-20261028T145500Z"
BASE="/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade"
RELEASE=BASE+"/releases/fba51aade2ee2410dda087e95cf054e53375ed3a"
EXPECTED="738c295d55d9b1a4081ca2547d2bfdad3ea59553d0102cc152bb34b3916917fa"
spec=importlib.util.spec_from_file_location("authorized_cluster_read","E:/QTrade-temp/authorized_cluster_read.py")
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
assert sha256((LOCAL/"publication_cold_recovery_operator_20261028.py").read_bytes()).hexdigest()==EXPECTED
names=["publication_cold_recovery_operator_20261028.py","publication_native_relay_operator_v3_20261028.py","test_publication_cold_recovery_operator_20261028.py","native_receive_pack_barrier.py","fixtures/PUSH_INTENT.json","fixtures/PUSH_UNCERTAIN.json"]
started=time.monotonic();c=helper.connect();s=c.open_sftp()
try:
    s.mkdir(REMOTE,0o700);s.mkdir(REMOTE+"/fixtures",0o700)
    sources={}
    for name in names:
        raw=(LOCAL/name).read_bytes();sources[name]=sha256(raw).hexdigest()
        with s.open(REMOTE+"/"+name,"wx") as f:f.write(raw);f.flush()
        s.chmod(REMOTE+"/"+name,0o600)
        with s.open(REMOTE+"/"+name,"rb") as f:assert sha256(f.read()).hexdigest()==sources[name]
    s.close()
    argv=["env","PYTEST_DISABLE_PLUGIN_AUTOLOAD=1","PYTHONDONTWRITEBYTECODE=1","PYTHONNOUSERSITE=1","LIVE_TRADING_ENABLED=false","PYTHONPATH="+RELEASE+"/src:"+RELEASE,BASE+"/venv/bin/python","-B","-P","-m","pytest","-p","no:cacheprovider","-q",REMOTE+"/test_publication_cold_recovery_operator_20261028.py","--junitxml="+REMOTE+"/linux-results.xml","--basetemp="+REMOTE+"/pytest-work"]
    _,out,err=c.exec_command(shlex.join(argv),timeout=240)
    raw=out.read();errors=err.read();rc=out.channel.recv_exit_status()
    dest=LOCAL/"linux-actual";dest.mkdir()
    (dest/"stdout.txt").write_bytes(raw);(dest/"stderr.txt").write_bytes(errors)
    s=c.open_sftp();s.get(REMOTE+"/linux-results.xml",str(dest/"linux-results.xml"));s.close()
    result={"captured_at":datetime.now(timezone.utc).isoformat(),"returncode":rc,"elapsed_seconds":time.monotonic()-started,"remote_root":REMOTE,"source_sha256":sources,"ordinary_production_changed":False,"network_git_push_performed":False,"scope":"actual Linux offline fixtures only; no real cold180 performance or approval"}
    (dest/"execution.json").write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
    print(raw.decode("utf-8","replace"));print(json.dumps(result,sort_keys=True))
    raise SystemExit(rc)
finally:c.close()
