from __future__ import annotations
import ast
from contextlib import suppress
from datetime import datetime,timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import subprocess
import time

ROOT=Path(__file__).resolve().parent
BASE=Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
REPO=BASE/'publication'
HELPER=BASE/'releases/fba51aade2ee2410dda087e95cf054e53375ed3a/scripts/publish_research_snapshot.py'
HELPER_SHA='eed5960ee4aa35135cddbff04235e500c4c8209464d3b14745a5a52afc585918'


def digest(value):return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def save(name,value):
    with (ROOT/name).open('x') as h:json.dump(value,h,sort_keys=True,indent=2);h.write('\n');h.flush();os.fsync(h.fileno())
def summarize(value):
    clocks={k:v for k,v in value.items() if type(v) is str and (k.endswith('_at') or k in ('as_of','session','date','trading_day','actual_start','actual_end','revision','schema_version','evidence_sha256','master_sha256','generation_id'))}
    collections={k:{'type':type(v).__name__,'count':len(v),'canonical_sha256':digest(v),'sample':list(v)[:3]} for k,v in value.items() if type(v) in (list,dict)}
    return {'keys':sorted(value),'clock_identity_fields':clocks,'collections':collections}


def main():
    start=time.monotonic();deadline=start+60
    assert ROOT.parent==Path('/root/qtrade-local/operator-evidence') and ROOT.name.startswith('abe-catalog-lineage-readonly-')
    assert os.environ.get('LIVE_TRADING_ENABLED')=='false'
    helper=HELPER.read_bytes();assert sha256(helper).hexdigest()==HELPER_SHA
    node=next(n for n in ast.parse(helper).body if isinstance(n,ast.FunctionDef) and n.name=='_cleanup_git_group')
    ns={'os':os,'signal':signal,'time':time,'subprocess':subprocess,'suppress':suppress}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(HELPER),'exec'),ns);cleanup=ns['_cleanup_git_group']
    env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','GIT_NO_LAZY_FETCH':'1','GIT_CONFIG_GLOBAL':'/dev/null',
        'GIT_CONFIG_NOSYSTEM':'1','GIT_TERMINAL_PROMPT':'0','GIT_ASKPASS':'/bin/false','GIT_OPTIONAL_LOCKS':'0',
        'HOME':str(ROOT),'LIVE_TRADING_ENABLED':'false'}
    commands=[]
    def git(args,maximum=1_000_000):
        assert args[0] in ('show','ls-tree','log')
        command=['git','-c','core.hooksPath=/dev/null','-c','gc.auto=0','-c','maintenance.auto=false','--no-optional-locks',*args]
        budget=min(10,deadline-time.monotonic());assert budget>0
        child=subprocess.Popen(command,cwd=REPO,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        try:
            out,err=child.communicate(timeout=budget);rc=child.returncode
        finally:
            try:cleanup(child)
            finally:
                if child.stdout:child.stdout.close()
                if child.stderr:child.stderr.close()
        commands.append({'argv':command,'returncode':rc,'stdout_sha256':sha256(out).hexdigest(),'stdout_bytes':len(out),'stderr_sha256':sha256(err).hexdigest(),'stderr_bytes':len(err)})
        assert rc==0 and len(out)<=maximum
        return out
    def blob(commit,path):
        record=git(['ls-tree','-l',commit,'--',path],10000).decode().strip()
        fields,name=record.split('\t',1);mode,kind,oid,length=fields.split();assert kind=='blob' and name==path and int(length)<=10_000_000
        raw=git(['show',commit+':'+path],10_000_000);assert len(raw)==int(length)
        target=ROOT/'blobs'/(oid+'.json');target.parent.mkdir(exist_ok=True)
        if target.exists():assert target.read_bytes()==raw
        else:
            with target.open('xb') as h:h.write(raw)
        return json.loads(raw),{'commit':commit,'path':path,'blob_oid':oid,'raw_sha256':sha256(raw).hexdigest(),'bytes':len(raw),'local_artifact':str(target.relative_to(ROOT))}
    result={'schema_version':'qtrade.abe-catalog-lineage-readonly.v1','at':datetime.now(timezone.utc).isoformat(),'repository':str(REPO),
        'readonly_git_environment':{k:env[k] for k in ('GIT_NO_LAZY_FETCH','GIT_CONFIG_GLOBAL','GIT_CONFIG_NOSYSTEM','GIT_OPTIONAL_LOCKS')},
        'max_unified_change_commits':20,'network_fetch_push_or_service':False}
    try:
        commit=git(['log','-1','--format=%H','abe2986c'],1000).decode().strip();assert len(commit)==40
        result['frozen_commit']=commit
        tree=git(['ls-tree','-r','--name-only',commit],1_000_000).decode().splitlines()
        result['tree_file_count']=len(tree)
        unified=[n for n in tree if n.rsplit('/',1)[-1]=='unified.json']
        indexes=[n for n in tree if n.rsplit('/',1)[-1] in ('index.json','unified-market.json') and ('market' in n or 'unified' in n)]
        result['unified_paths']=unified;result['index_paths']=indexes
        assert len(unified)==len(indexes)==1
        current,unifiedref=blob(commit,unified[0]);index,indexref=blob(commit,indexes[0])
        master=current['master'];declared=index['master_sha256'];body={k:v for k,v in master.items() if k!='evidence_sha256'}
        result['frozen_index']={'ref':indexref,'summary':summarize(index),'master_sha256':declared}
        result['frozen_unified']={'ref':unifiedref,'summary':summarize(current),'master_summary':summarize(master),
            'master_evidence_sha256':master['evidence_sha256'],'master_recomputed_evidence_sha256':digest(body)}
        result['same_commit_master_matches_index']=declared==master['evidence_sha256']
        history=git(['log','-20','--format=%H',commit,'--',unified[0]],10000).decode().splitlines()
        result['history_count']=len(history);result['history']=[];result['matches']=[]
        for candidate in history:
            value,ref=blob(candidate,unified[0]);m=value.get('master',{});entry={'ref':ref,'summary':summarize(value),'master_summary':summarize(m),
                'master_evidence_sha256':m.get('evidence_sha256'),'master_recomputed_evidence_sha256':digest({k:v for k,v in m.items() if k!='evidence_sha256'})}
            result['history'].append(entry)
            if m.get('evidence_sha256')==declared and entry['master_recomputed_evidence_sha256']==declared:result['matches'].append(entry)
        public=BASE/'unified-data/public'
        result['public_history_top_level']={'path':str(public),'exists':public.is_dir()}
        if public.is_dir():
            children=sorted(public.iterdir(),key=lambda p:p.name)
            result['public_history_top_level'].update({'total_entries':len(children),'truncated':len(children)>100,
                'entries':[{'name':p.name,'is_dir':p.is_dir(),'is_symlink':p.is_symlink()} for p in children[:100]]})
        result['state']='BOUNDED_READONLY_COMPLETE'
    except BaseException as e:
        result['state']='BOUNDED_READONLY_FAILED';result['error_class']=type(e).__name__
    result['elapsed_seconds']=time.monotonic()-start;save('commands.json',commands);save('result.json',result)
    print(json.dumps({'state':result['state'],'elapsed_seconds':result['elapsed_seconds'],'matches':len(result.get('matches',[])),'root':str(ROOT)}))
    if result['state']!='BOUNDED_READONLY_COMPLETE':raise SystemExit(1)


if __name__=='__main__':main()
