"""Prepare only. No public-write/send command exists in this desktop driver."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime, timezone
import argparse,importlib.util,json,shlex,stat,time
LOCAL=Path("E:/QTrade-temp")
REMOTE="/root/qtrade-local/operator-evidence/publication-cold-recovery-20261028T143000Z"
BASE="/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade"
RELEASE=BASE+"/releases/fba51aade2ee2410dda087e95cf054e53375ed3a"
SOURCE="publication_cold_recovery_operator_20261028.py"
EXPECTED="738c295d55d9b1a4081ca2547d2bfdad3ea59553d0102cc152bb34b3916917fa"

def require(value,code):
    if not value:raise ValueError(code)

def real_directory(s,path):
    current=""
    for part in path.split("/")[1:]:
        current+="/"+part;info=s.lstat(current)
        require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode),"REAL_DIRECTORY_REQUIRED")

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode",choices=["init","ls","prepare"])
    args=parser.parse_args()
    raw=(LOCAL/SOURCE).read_bytes();require(sha256(raw).hexdigest()==EXPECTED,"FINAL_REVIEWED_SOURCE_REQUIRED")
    spec=importlib.util.spec_from_file_location("authorized_cluster_read",str(LOCAL/"authorized_cluster_read.py"))
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    started=time.monotonic();c=helper.connect();stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest=LOCAL/"cluster-evidence"/("publication-cold-"+args.mode+"-"+stamp);dest.mkdir()
    try:
        s=c.open_sftp();real_directory(s,REMOTE.rsplit("/",1)[0])
        try:s.mkdir(REMOTE,0o700)
        except OSError:real_directory(s,REMOTE)
        real_directory(s,REMOTE)
        target=REMOTE+"/"+SOURCE
        try:info=s.lstat(target)
        except FileNotFoundError:
            with s.open(target,"wx") as f:f.write(raw);f.flush()
            s.chmod(target,0o600);info=s.lstat(target)
        require(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode) and info.st_size==len(raw),"REGULAR_OPERATOR_REQUIRED")
        with s.open(target,"rb") as f:require(sha256(f.read()).hexdigest()==EXPECTED,"REMOTE_OPERATOR_CHANGED")
        for forbidden in ["USER_AUTHORIZATION.json","PUSH_INTENT.json","SEND_STARTED.json"]:
            try:s.lstat(REMOTE+"/"+forbidden)
            except FileNotFoundError:continue
            raise ValueError("PREPARATION_ONLY_NO_AUTHORITY_OR_CLAIM_ALLOWED")
        s.close()
        argv=["env","LIVE_TRADING_ENABLED=false","PYTHONDONTWRITEBYTECODE=1","PYTHONNOUSERSITE=1","PYTHONSAFEPATH=1","PYTHONPATH="+RELEASE+"/src:"+RELEASE,BASE+"/venv/bin/python","-B","-P",target,args.mode]
        _,out,err=c.exec_command(shlex.join(argv),timeout=115)
        output=out.read();errors=err.read();rc=out.channel.recv_exit_status()
        (dest/"result.stdout").write_bytes(output);(dest/"result.stderr").write_bytes(errors)
        result=json.loads(output) if output else None
        if args.mode=="prepare" and rc==0:
            s=c.open_sftp();s.get(REMOTE+"/target.json",str(dest/"target.json"));s.close()
            require(json.loads((dest/"target.json").read_bytes())==result,"EXACT_PLAN_RESPONSE_BINDING")
        metadata={"mode":args.mode,"captured_at":datetime.now(timezone.utc).isoformat(),"returncode":rc,"elapsed_seconds":time.monotonic()-started,"operator_sha256":EXPECTED,"remote_attempt":REMOTE,"stdout_sha256":sha256(output).hexdigest(),"stderr_sha256":sha256(errors).hexdigest(),"result":result,"public_write":False,"source_or_production_config_changed":False,"isolated_preparation_files_written":True,"user_authorization_created":False,"push_intent_created":False,"plan_sha256":sha256((dest/"target.json").read_bytes()).hexdigest() if (dest/"target.json").exists() else None}
        (dest/"execution.json").write_text(json.dumps(metadata,sort_keys=True,indent=2)+"\n")
        print(json.dumps({"evidence":str(dest),**metadata},sort_keys=True))
        raise SystemExit(rc)
    finally:c.close()

if __name__=="__main__":main()
