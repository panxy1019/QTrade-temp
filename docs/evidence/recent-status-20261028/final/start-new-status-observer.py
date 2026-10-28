import json,os,subprocess,time
from datetime import UTC,datetime
from pathlib import Path
from hashlib import sha256
r=Path('/root/qtrade-local/operator-evidence/recent-status-60c8313-20261028');b=Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade');source=r/'source'
program='qtrade-recent-status'
config=Path('/etc/supervisor/conf.d/qtrade-recent-status.conf')
assert not config.exists(),'NEW_PROGRAM_CONFIG_REQUIRED'
for d in Path('/proc').iterdir():
    if d.name.isdigit():
        try:cmd=(d/'cmdline').read_bytes()
        except (FileNotFoundError,ProcessLookupError,PermissionError):continue
        if b'scripts.collect_recent_status' in cmd and b'--watch' in cmd:
            raise ValueError('RECENT_STATUS_WATCHER_ALREADY_EXISTS')
m=json.loads((r/'source-manifest.json').read_bytes())
assert m['source_revision']=='60c83131aefd667abadb84a360b7bf225893776a'
for name,identity in m['files'].items():assert sha256((source/name).read_bytes()).hexdigest()==identity
tests=__import__('xml.etree.ElementTree',fromlist=['parse']).parse(r/'linux-tests.xml').getroot()
suites=list(tests) if tests.tag=='testsuites' else [tests]
assert sum(int(x.attrib.get('tests',0)) for x in suites)==80
assert all(int(x.attrib.get(k,0))==0 for x in suites for k in ['errors','failures','skipped'])
owners={str(pid):(Path('/proc')/str(pid)/'stat').read_text().split()[21]
        for pid in [383859,338258,334634,294214,299932,213587,164340,153768,4471]}
text='[program:'+program+']\ncommand='+str(b/'venv/bin/python')+' -B -m scripts.collect_recent_status --base '+str(b)+' --dataset '+str(b/'qlib-data-20261023/dataset.json')+' --source-revision '+'60c83131aefd667abadb84a360b7bf225893776a'+' --watch\ndirectory='+str(source)+'\nenvironment=PYTHONPATH="'+str(source/'src')+':'+str(source)+'",LIVE_TRADING_ENABLED="false",PYTHONUNBUFFERED="1"\nautostart=true\nautorestart=true\nstartsecs=5\nstopwaitsecs=45\nredirect_stderr=true\nstdout_logfile='+str(b/'recent-status/worker.log')+'\nstdout_logfile_maxbytes=1MB\nstdout_logfile_backups=3\n'
with config.open('x') as f:f.write(text)
os.chmod(config,0o600)
with (r/'new-program-public.conf').open('x') as f:f.write(text)
receipts=[]
for action in ['reread','add']:
    args=['/usr/bin/supervisorctl','-c','/etc/supervisor/supervisord.conf',action]
    if action=='add':args.append(program)
    p=subprocess.run(args,capture_output=True,text=True,timeout=30)
    value={'at':datetime.now(UTC).isoformat(),'action':action,'only_group':program if action=='add' else None,
           'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
    with (r/('ctl-'+action+'.json')).open('x') as f:json.dump(value,f,sort_keys=True,indent=2);f.write('\n')
    receipts.append(value)
    if action=='add' and p.returncode:
        raise ValueError('NEW_GROUP_ADD_FAILED_PRESERVE_RECEIPT_NO_EXISTING_RESTART')
for pid,ticks in owners.items():assert (Path('/proc')/pid/'stat').read_text().split()[21]==ticks
proof={'at':datetime.now(UTC).isoformat(),'source_revision':'60c83131aefd667abadb84a360b7bf225893776a',
       'config_sha256':sha256(config.read_bytes()).hexdigest(),'existing_owners':owners,
       'existing_owners_unchanged':True,'no_existing_service_restarted':True,
       'runtime_observation_still_required':True,'whole_step1_accepted':False,'actions':receipts}
with (r/'start-dispatched.json').open('x') as f:json.dump(proof,f,sort_keys=True,indent=2);f.write('\n')
print(json.dumps({'actions':[(v['action'],v['exit_code']) for v in receipts],
                  'existing_owners_unchanged':True,'runtime_observation_still_required':True}))
