import gzip,hashlib,json,os,sys,time
from pathlib import Path
from datetime import UTC,datetime
owned=Path(sys.argv[1]);source=Path('/root/qtrade-local/operator-evidence/factor-strategy-s2c-20261028T111832Z-22bd6f85/source')
assert owned.parent==Path('/root/qtrade-local/operator-evidence') and os.environ['LIVE_TRADING_ENABLED']=='false'
os.nice(10);sys.path[:0]=[str(source/'src'),str(source)]
from qtrade.research.factor_strategy_contract import freeze_strategy,_SOURCE_FILES
from qtrade.research.factor_registry import FactorRegistry
def h(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def inventory():return {p.relative_to(source).as_posix():{'sha256':h(p),'bytes':p.stat().st_size} for p in source.rglob('*') if p.is_file()}
expected=json.loads((source.parent/'package-manifest.json').read_bytes())['expected_files']
assert inventory()==expected and len(_SOURCE_FILES)==13
original=Path('/root/qtrade-local/operator-evidence/factor-longer-s2b-20261028T112943Z-86cd84da/report.json.gz')
assert h(original)=='74c246d7822b4e860706dcc2c212763ede3aef62f38f5b94297237acf5457ada'
parent=Path('/root/qtrade-local/operator-evidence/factor-queue-s2d-20261028T123014Z-b12b8041')
request=json.loads((parent/'actual-request.json').read_bytes())
report=json.loads(gzip.decompress(original.read_bytes()))
started=time.perf_counter()
definition=freeze_strategy(request['factor_spec'],factors=FactorRegistry(parent/'approved-factor-archive/factors.sqlite',read_only=True),evaluation=report,source_revision=request['source_revision'])
target=owned/'factor_strategy_s2c13_archived.json'
target.write_text(json.dumps(definition,ensure_ascii=False,indent=2)+'\n')
assert inventory()==expected
receipt={'state':'OLD_C_PURE_FREEZE_ARCHIVE_NO_REGISTRATION','created_at':datetime.now(UTC).isoformat(),
 'old_source_root':str(source),'old_source_files':len(expected),'old_source_before_after_exact':True,
 'old_contract_source_count':len(_SOURCE_FILES),'old_contract_source_hashes':definition['source']['files'],
 'definition_sha256':h(target),'definition_bytes':target.stat().st_size,'strategy_version':definition['strategy_version'],
 'original_report_sha256':h(original),'actual_source_revision_label':request['source_revision'],
 'elapsed_seconds':time.perf_counter()-started}
(owned/'legacy-provenance.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt),flush=True)
