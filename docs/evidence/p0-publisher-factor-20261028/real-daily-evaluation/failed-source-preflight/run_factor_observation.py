
import json,time,resource,shutil,importlib.util,gzip
from pathlib import Path
from hashlib import sha256
from datetime import UTC,datetime
from qtrade.research.factor_registry import FactorRegistry
from qtrade.research.factor_evaluation import FactorEvaluationStore
from qtrade.research.strategy_contract import supported_universe
from scripts.run_daily_factor_evaluation import run
from scripts.atomic_json import write_json_immutable
from qtrade.monitoring.data_freshness import signed,AUTHORITY
b=Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade');r=Path('/root/qtrade-local/operator-evidence/step2-real-daily-20261028T045015Z');source=Path('/root/qtrade-local/operator-evidence/step2-factor-evaluation-20261028T044410Z/source')
manifest=json.loads((r/'source-manifest.json').read_bytes())
for name,digest in manifest['runtime_files'].items():assert sha256((source/name).read_bytes()).hexdigest()==digest,name
assert shutil.disk_usage(b).free>100*1024**3
assert shutil.disk_usage(r).free>2*1024**3
spec=importlib.util.spec_from_file_location('readonly_controls','/root/qtrade-local/operator-evidence/graded-quality-458552d-20261028/rollout-operator-3dd14200cc7a8a6d.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
before={'owners':m.controls(),'context':m.h.context(),'frozen_controls':m.frozen_controls()}
write_json_immutable(r/'before.json',signed({**AUTHORITY,**before}))
version='09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407'
root=b/'unified-data/qlib-versions/versions'/version
v=json.loads((root/'version.json').read_bytes())
assert v['version_id']==version and v['actual_end']=='2026-10-27'
calendar=[day for day in (root/'provider/calendars/day.txt').read_text().splitlines() if day<=v['actual_end']]
instruments=list(supported_universe(root));assert len(instruments)==891
days=calendar[-6:-1];assert len(days)==5 and days[-1]=='2026-10-26'
reg=FactorRegistry(r/'factors.sqlite')
refs=reg.register_builtins(source_revision=manifest['source_revision'],price_basis='QLIB_NORMALIZED_ADJUSTMENT_UNCERTIFIED',now=datetime.now(UTC))
request={'ordered_factor_refs':list(refs),'instruments':instruments,
 'decisions':[{'session':day,'decision_at':day+'T15:30:00+08:00'} for day in days],
 'label_horizon_sessions':1,'knowledge_mode':'RETROSPECTIVE_RECONSTRUCTION',
 'quantiles':5,'min_pairs':30,'stability_block_sessions':5,
 'source_revision':manifest['source_revision'],'revalidates_evaluation_id':None}
write_json_immutable(r/'request.json',request)
started=datetime.now(UTC);clock=time.monotonic()
try:
 ref=run(version_root=root,data_database=Path('/root/qtrade-local/qlib-versions.sqlite'),
  factor_database=r/'factors.sqlite',request=request,output_root=r/'results')
 report=FactorEvaluationStore(r/'results/evaluations.sqlite').get(ref['evaluation_id'])
 raw=(r/'results'/(ref['evaluation_id']+'.json')).read_bytes()
 (r/'report.json.gz').write_bytes(gzip.compress(raw,mtime=0))
 after={'owners':m.controls(),'context':m.h.context(),'frozen_controls':m.frozen_controls()}
 write_json_immutable(r/'after.json',signed({**AUTHORITY,**after}))
 receipt=signed({**AUTHORITY,'state':'NONPRODUCTION_DIAGNOSTICS_ARCHIVED',
  'started_at':started.isoformat(),'completed_at':datetime.now(UTC).isoformat(),
  'elapsed_seconds':time.monotonic()-clock,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
  'source_revision':manifest['source_revision'],'verified_runtime_files':len(manifest['runtime_files']),
  'data_version':version,'scope_symbols':len(instruments),'decision_sessions':days,
  'factor_count':len(refs),'dependency_cells':len(instruments)*len(days)*(21+6+21+21),
  'reference':ref,'summary':report['documents']['summary'],
  'daily_pair_counts':{k:[{'session':x['session'],'pairs':x['pairs'],'feature_valid':x['feature_valid'],'label_valid':x['label_valid']} for x in xs] for k,xs in report['documents']['daily_metrics'].items()},
  'report_raw_sha256':sha256(raw).hexdigest(),'report_raw_bytes':len(raw),
  'report_gzip_sha256':sha256((r/'report.json.gz').read_bytes()).hexdigest(),
  'before_after_controls_equal':before==after,'data_free_bytes':shutil.disk_usage(b).free,
  'operator_free_bytes':shutil.disk_usage(r).free,'production_stores_written':False,
  'whole_step1_accepted':False,'public_api_or_browser_delivered':False})
 write_json_immutable(r/'receipt.json',receipt)
 print(json.dumps({k:receipt[k] for k in ('state','elapsed_seconds','peak_rss_kib','scope_symbols','decision_sessions','factor_count','dependency_cells','reference','before_after_controls_equal','report_raw_bytes')}),flush=True)
 assert before==after,'CONTROLS_CHANGED_DURING_READONLY_RESEARCH_REQUIRES_REVIEW'
except Exception as exc:
 write_json_immutable(r/'failure.json',signed({**AUTHORITY,'state':'FAILED','error_type':type(exc).__name__,
  'error_code':str(exc) if str(exc).startswith(('FACTOR_','STRATEGY_','DATA_')) else 'NONPRODUCTION_OPERATOR_FAILURE',
  'at':datetime.now(UTC).isoformat(),'source_revision':manifest['source_revision']}))
 raise
