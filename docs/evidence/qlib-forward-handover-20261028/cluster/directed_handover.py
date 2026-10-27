"""One explicitly authorized forward-owner handover; no live, no old claim reuse."""
import argparse
import base64
import configparser
import hashlib
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.model_registry import checked_document
from qtrade.research.prediction_registry import active_approval
from scripts.atomic_json import write_json_immutable
from scripts.run_qlib_forward_service import load, prepare_recovery_request

BASE = Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
BOOKS = Path('/root/qtrade-local/forward-research')
OP = Path('/root/qtrade-local/operator-evidence/forward-handover-9846a7f-20261028')
REV = '9846a7f30774d6ae4b4d41f02b493c5246be87d9'
OLD_PID = 175982
CONFIG = Path('/etc/supervisor/conf.d/qtrade-forward-research.conf')
MAIN_CONFIG = '/etc/supervisor/supervisord.conf'
PROGRAM = 'qtrade-forward-research'
MODEL = '29589b4486a09159eb3a07153c54fba609fbc80b52c168dd665cd46660be8487'
LOCAL = Path('/root/qtrade-local')
SERVICES = {'workflow':338258,'data':334634,'publisher':294214,'etf-shadow':299932,
            'maintenance':213587,'history':164340,'pit':153768,'observer':4471}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write(name, value):
    write_json_immutable(OP/name, signed({**AUTHORITY, **{k:v for k,v in value.items()
                                                       if k!='evidence_sha256'}}))


def ro(path):
    c = sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
    return c


def context():
    with closing(ro(LOCAL/'online-models.sqlite')) as c:
        c.row_factory=sqlite3.Row
        approval=active_approval(c)
        assert approval and approval['model_id']==MODEL
        states=dict(c.execute('SELECT model_id,state FROM models'))
    with closing(ro(LOCAL/'qlib-versions.sqlite')) as c:
        head=c.execute("SELECT version_id FROM data_heads WHERE frequency='day'").fetchone()[0]
        event=c.execute('SELECT event_id FROM data_events WHERE version_id=? ORDER BY sequence DESC LIMIT 1',(head,)).fetchone()[0]
    return {'active':MODEL,'approval_sha256':approval['evidence_sha256'],'data_version':head,
            'data_event_id':event,'model_states':states}


def fingerprint():
    roster=load(BOOKS/'roster.json')['books']
    result={}
    for identity in sorted(roster):
        root=BOOKS/identity;row={}
        for database,tables in [('forward.sqlite',['config','targets','sessions']),('operations.sqlite',['fill_journal'])]:
            with closing(ro(root/database)) as c:
                for table in tables:
                    values=c.execute('SELECT * FROM '+table+' ORDER BY rowid LIMIT 10001').fetchall()
                    assert len(values)<=10000
                    row[database+':'+table]={'count':len(values),'sha256':digest(json.dumps(values,separators=(',',':'),default=str).encode())}
        row['admissions']={p.name:digest(p.read_bytes()) for p in sorted((root/'admissions').glob('*.json'))}
        row['archive-link']=digest((root/'archive-link.json').read_bytes())
        result[identity]=row
    return {'roster_sha256':digest((BOOKS/'roster.json').read_bytes()),'books':result}


def service_tokens():
    values={}
    for name,pid in SERVICES.items():
        p=Path('/proc')/str(pid)
        assert p.is_dir(),name
        fields=(p/'stat').read_text().rsplit(')',1)[1].split()
        values[name]={'pid':pid,'start_ticks':fields[19]}
    return values


def run_ctl(action, timeout=45):
    args=['/usr/bin/supervisorctl','-c',MAIN_CONFIG,action]
    if action in {'stop','start','status'}:args.append(PROGRAM)
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    # Supervisor status/progress contains no command/environment; never log private config.
    row={'action':action,'returncode':p.returncode,'stdout':p.stdout[-4000:],
         'stderr':p.stderr[-1000:],'at':datetime.now(UTC).isoformat()}
    write('ctl-'+action+'-'+datetime.now(UTC).strftime('%H%M%S%f')+'.json',row)
    assert p.returncode==0,(action,p.returncode)
    return p.stdout


def actual_instance():
    instance=load(BOOKS/'service-instance.json')
    now=datetime.now(UTC);stamp=datetime.fromisoformat(instance['heartbeat_at'])
    assert instance['state']=='RUNNING' and 0<=(now-stamp).total_seconds()<=90
    return instance


def preflight():
    assert Path('/proc/'+str(OLD_PID)).is_dir()
    assert os.environ.get('LIVE_TRADING_ENABLED','false').lower()=='false'
    assert shutil.disk_usage(BASE).free>=100*1024**3
    auth=load(OP/'authorization.json')
    assert auth['kind']=='HUMAN_AUTHORIZED_SINGLE_DIRECTED_FORWARD_OWNER_HANDOVER'
    assert auth['handover_count']==1 and auth['paper_recovery_request_max']==6
    assert auth['retired_models_must_remain_stopped'] is True
    roster=load(BOOKS/'roster.json')['books'];assert len(roster)==14
    allowed={identity for identity,c in roster.items()
             if c['mode']=='PAPER' and c['strategy_id'] in auth['allowed_paper_strategies']}
    assert len(allowed)==6
    old_instance=actual_instance()
    for p in (BOOKS/'recovery/requests').glob('*.json'):
        assert (BOOKS/'recovery/receipts'/p.name).is_file(),'pending recovery must resolve first'
    publication=load(BASE/'workflow/public/forward.json')
    current={(b['strategy_id'],b['mode']):b for b in publication['books']}
    for identity in allowed:
        c=roster[identity];b=current[(c['strategy_id'],'PAPER')]
        assert b['running'] is True and b['risk_state']=='NORMAL' and not b['recovery_required']
    for identity in roster:
        root=BOOKS/identity
        with closing(ro(root/'forward.sqlite')) as c:
            assert c.execute("SELECT 1 FROM sessions WHERE status!='COMPLETE' LIMIT 1").fetchone() is None
        with closing(ro(root/'operations.sqlite')) as c:
            assert c.execute("SELECT 1 FROM fill_journal WHERE state!='APPLIED' LIMIT 1").fetchone() is None
    value={'kind':'DIRECTED_HANDOVER_READ_ONLY_PREFLIGHT','at':datetime.now(UTC).isoformat(),
           'context':context(),'fingerprints':fingerprint(),'services':service_tokens(),
           'allowed_books':sorted(allowed),'old_instance_id':old_instance['instance_id'],
           'config_sha256':digest(CONFIG.read_bytes()),'authorization_sha256':auth['evidence_sha256'],
           'handover_applied':False}
    write('preflight.json',value)
    print('PREFLIGHT_PASSED',len(allowed))


def install():
    # This step does not stop or start anything. All copied bytes are hash/Git verified.
    manifest=json.loads((OP/'source-manifest.json').read_bytes())
    assert manifest['revision']==REV and len(manifest['files'])==582
    release=BASE/'releases'/REV
    assert not release.exists();release.mkdir()
    for name,meta in manifest['files'].items():
        raw=(OP/'source'/name).read_bytes()
        assert len(raw)==meta['bytes'] and digest(raw)==meta['sha256']
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==meta['gitblob']
        p=release/name;p.parent.mkdir(parents=True,exist_ok=True)
        with p.open('xb') as f:f.write(raw)
    write('release-verification.json',{'kind':'INSTALLED_EXACT_RELEASE_NO_SERVICE_CHANGE',
          'revision':REV,'files':582,'at':datetime.now(UTC).isoformat()})
    print('RELEASE_INSTALLED',REV)


def handover():
    pre=load(OP/'preflight.json');assert context()==pre['context']
    assert fingerprint()==pre['fingerprints'] and service_tokens()==pre['services']
    assert digest(CONFIG.read_bytes())==pre['config_sha256']
    assert actual_instance()['instance_id']==pre['old_instance_id']
    assert Path('/proc/'+str(OLD_PID)).is_dir()
    # Durable irreversible operation claim; never repeat on interruption.
    write('handover-consumed.json',{'kind':'SINGLE_DIRECTED_HANDOVER_CONSUMED',
          'authorization_sha256':pre['authorization_sha256'],'at':datetime.now(UTC).isoformat()})
    run_ctl('stop')
    assert not Path('/proc/'+str(OLD_PID)).exists()
    assert context()==pre['context'] and service_tokens()==pre['services']
    assert fingerprint()==pre['fingerprints']
    backup=OP/'stopped-backup';backup.mkdir()
    shutil.copyfile(BOOKS/'roster.json',backup/'roster.json')
    for identity in pre['fingerprints']['books']:
        original=BOOKS/identity;dest=backup/identity;dest.mkdir()
        for name in ['forward.sqlite','operations.sqlite']:
            with closing(ro(original/name)) as src,closing(sqlite3.connect(dest/name)) as dst:
                src.backup(dst)
        for name in ['admissions','retirements']:
            if (original/name).is_dir():shutil.copytree(original/name,dest/name)
        shutil.copyfile(original/'archive-link.json',dest/'archive-link.json')
    if (BOOKS/'recovery').is_dir():shutil.copytree(BOOKS/'recovery',backup/'recovery')
    os.chmod(backup,0o700)
    write('stopped-backup.json',{'kind':'STOPPED_BOOK_BACKUP_COMPLETE','at':datetime.now(UTC).isoformat(),
          'fingerprints':fingerprint(),'services':service_tokens(),'context':context()})
    private_backup=OP/'private-original-forward.conf'
    with private_backup.open('xb') as f:f.write(CONFIG.read_bytes())
    os.chmod(private_backup,0o600)
    config=configparser.ConfigParser(interpolation=None);config.read(CONFIG)
    section='program:'+PROGRAM;assert config.has_section(section)
    old_directory=config.get(section,'directory')
    assert old_directory==str(BASE/'releases/e107eb9c74001822dda220b1e6ca96a55ee00ef6')
    args=shlex.split(config.get(section,'command'))
    pos=args.index('--source-revision');assert args[pos+1]=='e107eb9c74001822dda220b1e6ca96a55ee00ef6'
    args[pos+1]=REV
    if '--initialize-paper' in args:
        i=args.index('--initialize-paper');del args[i:i+2]
    assert '--initialize-paper' not in args
    assert args[0]==str(BASE/'venv/bin/python')
    assert args[args.index('--local-root')+1]==str(BOOKS)
    config.set(section,'command',shlex.join(args));config.set(section,'directory',str(BASE/'releases'/REV))
    tmp=CONFIG.with_suffix('.handover.tmp')
    with tmp.open('x',encoding='utf-8') as f:config.write(f)
    os.chmod(tmp,0o600);os.replace(tmp,CONFIG)
    write('config-update.json',{'kind':'TARGETED_FORWARD_CONFIG_UPDATED','at':datetime.now(UTC).isoformat(),
          'old_sha256':pre['config_sha256'],'new_sha256':digest(CONFIG.read_bytes()),
          'source_revision':REV,'initialize_paper_argument':False})
    run_ctl('reread');run_ctl('start')
    write('start-dispatched.json',{'kind':'TARGETED_FORWARD_START_DISPATCHED','at':datetime.now(UTC).isoformat(),
          'revision':REV,'services_unchanged':service_tokens()==pre['services'],
          'recovery_requested':False})
    print('HANDOVER_START_DISPATCHED')


def check():
    pre=load(OP/'preflight.json')
    instance=actual_instance()
    assert instance['instance_id']!=pre['old_instance_id']
    output=run_ctl('status')
    assert 'RUNNING' in output
    # Supervisor status is the authoritative new process identifier.
    import re
    newpid=int(re.search(r'pid (\d+)',output).group(1))
    args=Path('/proc/'+str(newpid)+'/cmdline').read_bytes().split(b'\0')
    assert b'--initialize-paper' not in args
    assert args[args.index(b'--source-revision')+1].decode()==REV
    assert Path('/proc/'+str(newpid)+'/cwd').resolve()==BASE/'releases'/REV
    manifest=json.loads((OP/'source-manifest.json').read_bytes())
    for name,meta in manifest['files'].items():
        raw=(BASE/'releases'/REV/name).read_bytes()
        assert len(raw)==meta['bytes'] and digest(raw)==meta['sha256'],name
    assert context()==pre['context'] and service_tokens()==pre['services']
    assert fingerprint()==pre['fingerprints']
    publication=load(BASE/'workflow/public/forward.json')
    assert publication['source_revision']==REV and len(publication['books'])==14
    assert not publication['errors'],publication['errors']
    retired=list(BOOKS.glob('*/retirements/*.json'));assert len(retired)==2
    assert len(list(BOOKS.glob('*/rejected-targets/*.json')))==12
    assert all(not b['running'] for b in publication['books'] if b['mode']=='PAPER')
    write('new-owner-pre-recovery.json',{'kind':'NEW_OWNER_EXACT_SOURCE_AND_STOPPED_BOOKS_VERIFIED',
          'at':datetime.now(UTC).isoformat(),'new_pid':newpid,'new_instance':instance,
          'publication_sha256':publication['evidence_sha256'],'fingerprints_unchanged':True,
          'retirement_receipts':[str(p.relative_to(BOOKS)) for p in retired],
          'services_unchanged':True,'context':context()})
    print('NEW_OWNER_VERIFIED',newpid,publication['state'])


def recover():
    pre=load(OP/'preflight.json');new=load(OP/'new-owner-pre-recovery.json')
    assert context()==pre['context'] and service_tokens()==pre['services']
    assert fingerprint()==pre['fingerprints']
    assert actual_instance()['instance_id']==new['new_instance']['instance_id']
    # Only the explicit authorized account set. A consumed batch is never reset/retried.
    write('recovery-batch-consumed.json',{'kind':'AUTHORIZED_SIX_BOOK_RECOVERY_BATCH_CONSUMED',
          'at':datetime.now(UTC).isoformat(),'authorization_sha256':pre['authorization_sha256'],
          'books':pre['allowed_books'],'instance_id':new['new_instance']['instance_id']})
    rows=[]
    for identity in pre['allowed_books']:
        assert context()==pre['context']
        request=prepare_recovery_request(BOOKS,book_id=identity,actor='panxy1019-directed-handover',ttl_seconds=300)
        rows.append({'book_id':identity,'request_id':request['request_id'],'request_sha256':request['evidence_sha256']})
    write('recovery-request-batch.json',{'kind':'FRESH_SIX_BOOK_INSTANCE_BOUND_REQUESTS',
          'at':datetime.now(UTC).isoformat(),'requests':rows})
    print('FRESH_RECOVERY_REQUESTS',len(rows))


def verify():
    pre=load(OP/'preflight.json');new=load(OP/'new-owner-pre-recovery.json');batch=load(OP/'recovery-request-batch.json')
    assert actual_instance()['instance_id']==new['new_instance']['instance_id']
    assert context()==pre['context'] and service_tokens()==pre['services'] and fingerprint()==pre['fingerprints']
    rows=[]
    for row in batch['requests']:
        consumed=load(BOOKS/'recovery/consumed'/(row['request_id']+'.json'))
        receipt=load(BOOKS/'recovery/receipts'/(row['request_id']+'.json'))
        assert receipt['state']=='SUCCEEDED' and consumed['instance_id']==new['new_instance']['instance_id']
        assert receipt['book_id']==row['book_id'] and receipt['request_sha256']==row['request_sha256']
        rows.append({'request':row,'consumed':consumed,'receipt':receipt})
    pub=load(BASE/'workflow/public/forward.json');roster=load(BOOKS/'roster.json')['books']
    bykey={(b['strategy_id'],b['mode']):b for b in pub['books']}
    assert len(bykey)==14 and pub['state']=='RUNNING' and not pub['errors']
    assert pub['source_revision']==REV
    for identity,c in roster.items():
        book=bykey[(c['strategy_id'],c['mode'])]
        if identity in pre['allowed_books']:
            assert book['running'] is True and not book['recovery_required'] and book['risk_state']=='NORMAL'
            with closing(ro(BOOKS/identity/'operations.sqlite')) as db:
                control=db.execute("SELECT event_type FROM audit_events WHERE service='control' ORDER BY sequence DESC LIMIT 1").fetchone()
                assert control[0]=='SYSTEM_START'
        elif c['strategy_id'].startswith('approved_model_') and c['strategy_id']!='approved_model_'+MODEL:
            assert book['running'] is False
    write('forward-after-recovery.json',pub)
    write('handover-verification.json',{'kind':'ACTUAL_DIRECTED_HANDOVER_SIX_RECONCILED_PAPER',
          'state':'PASSED_PRIVATE_HANDOVER','at':datetime.now(UTC).isoformat(),'revision':REV,
          'old_pid':OLD_PID,'old_pid_exited':not Path('/proc/'+str(OLD_PID)).exists(),
          'new_pid':new['new_pid'],'new_instance_id':new['new_instance']['instance_id'],
          'recovered_current_paper_count':6,'retired_book_count':2,'all_book_count':14,
          'original_fingerprints_unchanged':True,'other_services_unchanged':True,
          'context':context(),'recoveries':rows,'public_delivered':False,
          'future_day_observed':False,'whole_step1_accepted':False})
    print('PRIVATE_HANDOVER_VERIFIED',new['new_pid'],len(rows))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['preflight','install','handover','check','recover','verify'])
    args=parser.parse_args()
    attempt={'action':args.action,'started_at':datetime.now(UTC).isoformat(),'state':'RUNNING'}
    try:
        globals()[args.action]();attempt['state']='SUCCEEDED'
    except BaseException as error:
        attempt.update(state='FAILED',error_type=type(error).__name__)
        raise
    finally:
        attempt['finished_at']=datetime.now(UTC).isoformat()
        write('operator-attempt-'+args.action+'-'+datetime.now(UTC).strftime('%H%M%S%f')+'.json',attempt)
