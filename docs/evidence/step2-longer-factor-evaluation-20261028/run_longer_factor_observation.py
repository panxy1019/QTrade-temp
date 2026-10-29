
import gzip,hashlib,json,os,resource,shutil,sys,time
from collections import Counter
from datetime import UTC,datetime,time as wall_time
from pathlib import Path
from zoneinfo import ZoneInfo
import pyarrow as pa
import pyarrow.parquet as pq
from qtrade.monitoring.data_freshness import AUTHORITY,signed
from qtrade.research.factor_evaluation import FactorEvaluationStore,validate_report
from qtrade.research.factor_registry import FactorRegistry,factor_ref
from qtrade.research.strategy_contract import ready_dataset,supported_universe,_admission
from scripts.atomic_json import write_json_immutable
from scripts.run_daily_factor_evaluation import run

owned=Path(sys.argv[1]);source=Path(sys.argv[2]);mode=sys.argv[3]
assert owned.parent==Path('/root/qtrade-local/operator-evidence') and mode in {'preflight','evaluate'}
b=Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
old=Path('/root/qtrade-local/operator-evidence/step2-real-daily-20261028T045123Z')
data_database=Path('/root/qtrade-local/qlib-versions.sqlite')
version='09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407'
version_root=b/'unified-data/qlib-versions/versions'/version
package=json.loads((owned/'package-manifest.json').read_bytes())
def h(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def seal(name,value):write_json_immutable(owned/name,signed({**AUTHORITY,**value}))
def inventory(path):
 return {p.relative_to(path).as_posix():{'bytes':p.stat().st_size,'sha256':h(p)}
         for p in sorted(path.rglob('*')) if p.is_file()}
def sources(name):
 actual=inventory(source);seal(name,{'matches':actual==package['expected_files'],'files':actual})
 assert actual==package['expected_files']
def reserves(name):
 actual={label:shutil.disk_usage(path).free for label,path in [('nfs',b),('local',owned)]}
 seal(name,actual);assert actual['nfs']>=100*1024**3 and actual['local']>=2*1024**3
assert os.environ.get('LIVE_TRADING_ENABLED')=='false'
if mode=='preflight':
 print('PHASE preflight_started',flush=True)
 reserves('resources-before.json');sources('source-before.json')
 original=inventory(old);seal('original-experiment-before.json',{'files':original})
 assert h(old/'report.json.gz')=='47bed3add84aff16f901531115c17ac4534cfd667b6716595a1ce098b9af37dd'
 raw=(old/'report.json.gz').read_bytes();old_report=validate_report(json.loads(gzip.decompress(raw)))
 old_request=json.loads((old/'request.json').read_bytes())
 assert old_request==old_report['documents']['specification']['request']
 binding=ready_dataset(version_root,data_database)
 assert binding==old_report['documents']['specification']['data_binding']
 assert supported_universe(version_root)==tuple(old_request['instruments'])
 assert len(old_request['instruments'])==891 and len(old_request['ordered_factor_refs'])==4
 calendar=[x for x in (version_root/'provider/calendars/day.txt').read_text().splitlines()
           if x<=binding['actual_end']]
 assert calendar==old_report['documents']['specification']['calendar']
 assert len(calendar)>=37 and calendar==sorted(set(calendar))
 days=calendar[-17:-1];needed=calendar[-37:]
 assert len(days)==16 and len(needed)==37 and days[-1]=='2026-10-26'
 now=datetime.now(UTC);zone=ZoneInfo('Asia/Shanghai')
 assert all(datetime.combine(datetime.fromisoformat(x).date(),wall_time(15),zone)<=now
            for x in calendar[-16:])
 factor_source=old/'factors.sqlite'
 assert all(not Path(str(factor_source)+suffix).exists() or Path(str(factor_source)+suffix).stat().st_size==0
            for suffix in ('-wal','-journal'))
 (owned/'inputs').mkdir()
 shutil.copyfile(factor_source,owned/'inputs/factors.sqlite')
 assert h(factor_source)==h(owned/'inputs/factors.sqlite')
 registry=FactorRegistry(owned/'inputs/factors.sqlite',read_only=True)
 definitions=[registry.get(ref) for ref in old_request['ordered_factor_refs']]
 assert definitions==old_report['documents']['factor_definitions']
 closes=[row['required_closes'] for row in definitions]
 assert closes==[21,6,21,21]
 cells=len(days)*891*sum(closes);assert cells==983664 and cells<=1_000_000
 counts=Counter();presence=[]
 for instrument in old_request['instruments']:
  path=version_root/'stocks'/instrument[2:]/'daily.parquet'
  parquet=pq.ParquetFile(path);kind=parquet.schema_arrow.field('date').type
  lower,upper=(datetime.fromisoformat(x).date() if pa.types.is_date(kind) else x for x in (needed[0],needed[-1]))
  table=pq.read_table(path,columns=['date'],filters=[('date','>=',lower),('date','<=',upper)])
  actual=[str(x) for x in table['date'].to_pylist()]
  assert len(actual)==len(set(actual)) and set(actual)<=set(needed)
  counts.update(actual)
  presence.append({'instrument':instrument,'present_sessions':len(actual),
                   'missing_sessions':[x for x in needed if x not in set(actual)]})
 assert all(counts[x]>0 for x in needed)
 request={**old_request,'decisions':[{'session':day,'decision_at':day+'T15:30:00+08:00'} for day in days],
          'source_revision':package['baseline_commit'],'revalidates_evaluation_id':old_report['evaluation_id']}
 assert request['knowledge_mode']=='RETROSPECTIVE_RECONSTRUCTION'
 write_json_immutable(owned/'request.json',request)
 seal('preflight.json',{'state':'READY_FOR_16_SESSION_NONPRODUCTION_DIAGNOSTIC',
  'checked_at':now.isoformat(),'original_evaluation_ref':{k:old_report[k] for k in
    ('evaluation_id','specification_sha256','report_manifest_sha256')},
  'data_binding':binding,'calendar_count':len(calendar),'calendar_sha256':hashlib.sha256(
    json.dumps(calendar,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest(),
  'required_calendar':needed,'decision_sessions':days,'label_exit_sessions':calendar[-16:],
  'date_grid_counts':dict(counts),'instrument_history_presence':presence,
  'complete_date_grid_instruments':sum(not row['missing_sessions'] for row in presence),
  'factor_refs':[factor_ref(row) for row in definitions],'required_closes':closes,
  'dependency_cells':cells,'request_sha256':h(owned/'request.json'),
  'original_factor_database_sha256':h(factor_source),'admission':_admission(data_database,version),
  'maturity':'SESSION_15_00_ONLY_NOT_EXACT_HISTORICAL_INFORMATION_CLOCK',
  'selection':'EXACT_ORIGINAL_891_INSTRUMENTS_NO_SHRINK_OR_NEW_SAMPLE',
  'price_validity_not_certified_by_date_preflight':True})
 print(json.dumps({'state':'PREFLIGHT_COMPLETE','root':str(owned),'dates':days,
  'warmup_first':needed[0],'label_last':needed[-1],'dependency_cells':cells,
  'complete_date_grid_instruments':sum(not row['missing_sessions'] for row in presence)}),flush=True)
else:
 print('PHASE longer_evaluation_started',flush=True)
 preflight=json.loads((owned/'preflight.json').read_bytes())
 assert preflight['request_sha256']==h(owned/'request.json')
 old_report=validate_report(json.loads(gzip.decompress((old/'report.json.gz').read_bytes())))
 (owned/'results').mkdir()
 store=FactorEvaluationStore(owned/'results/evaluations.sqlite')
 assert store.save(old_report)==preflight['original_evaluation_ref']
 started=datetime.now(UTC);clock=time.monotonic()
 try:
  reference=run(version_root=version_root,data_database=data_database,
   factor_database=owned/'inputs/factors.sqlite',request=json.loads((owned/'request.json').read_bytes()),
   output_root=owned/'results')
  print('PHASE report_committed',flush=True)
  report=store.get(reference['evaluation_id'])
  raw=(owned/'results'/(reference['evaluation_id']+'.json')).read_bytes()
  (owned/'report.json.gz').write_bytes(gzip.compress(raw,mtime=0))
  original=inventory(old);seal('original-experiment-after.json',{'files':original})
  assert original==json.loads((owned/'original-experiment-before.json').read_bytes())['files']
  assert _admission(data_database,version)==preflight['admission']
  assert ready_dataset(version_root,data_database)==preflight['data_binding']
  sources('source-after.json');reserves('resources-after.json')
  reasons=Counter((str(row['factor_index']),row['feature_reason'],row['label_reason'])
                  for row in report['documents']['eligibility_ledger'])
  seal('receipt.json',{'state':'NONPRODUCTION_16_SESSION_DIAGNOSTICS_ARCHIVED',
   'started_at':started.isoformat(),'completed_at':datetime.now(UTC).isoformat(),
   'elapsed_seconds':time.monotonic()-clock,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
   'reference':reference,'request_sha256':h(owned/'request.json'),
   'source_package':{'baseline_commit':package['baseline_commit'],'overlays':package['overlays'],
                     'file_count':len(package['expected_files'])},
   'scope_symbols':891,'factor_count':4,'decision_sessions':preflight['decision_sessions'],
   'dependency_cells':preflight['dependency_cells'],'summary':report['documents']['summary'],
   'daily_metrics':report['documents']['daily_metrics'],'stability':report['documents']['stability'],
   'redundancy_daily':report['documents']['redundancy_daily'],
   'rejection_counts':[{'factor_index':a,'feature_reason':b,'label_reason':c,'count':count}
      for (a,b,c),count in sorted(reasons.items(),key=lambda item:str(item[0]))],
   'report_raw_sha256':hashlib.sha256(raw).hexdigest(),'report_raw_bytes':len(raw),
   'report_gzip_sha256':h(owned/'report.json.gz'),'old_experiment_bytes_unchanged':True,
   'original_ready_admission_unchanged':True,'production_stores_written':False,
   'production_state_unchanged_claim':False,'oos_claim':False,'net_portfolio_claim':False})
  print(json.dumps({'state':'COMPLETE','reference':reference,'seconds':time.monotonic()-clock,
    'report_raw_bytes':len(raw),'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}),flush=True)
 except Exception as error:
  seal('operator-failure.json',{'state':'FAILED','error_type':type(error).__name__,
      'at':datetime.now(UTC).isoformat()})
  raise
