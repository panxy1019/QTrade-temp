"""Isolated offline native fixture driver; no services, queue, registration or real dataset."""
from __future__ import annotations
import ast
from contextlib import suppress
import hashlib
import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent
SOURCE=ROOT/'source'
BASE=Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
PYTHON=BASE/'qlib-env/bin/python3.11'
HELPER=BASE/'releases/fba51aade2ee2410dda087e95cf054e53375ed3a/scripts/publish_research_snapshot.py'
HELPER_SHA='eed5960ee4aa35135cddbff04235e500c4c8209464d3b14745a5a52afc585918'


def sha(raw):return hashlib.sha256(raw).hexdigest()
def save(name,value):
    with (ROOT/'evidence'/name).open('x',encoding='utf-8') as handle:
        json.dump(value,handle,sort_keys=True,indent=2);handle.write('\n');handle.flush();os.fsync(handle.fileno())
def inventory():return {p.relative_to(SOURCE).as_posix():sha(p.read_bytes())
                       for p in SOURCE.rglob('*') if p.is_file()}
def disk(path):
    value=os.statvfs(path)
    return {'path':str(path),'free_bytes':value.f_bavail*value.f_frsize}


def main():
    started=time.monotonic();child=None;error=None;rc=None
    expected=json.loads((ROOT/'package-manifest.json').read_bytes())
    assert ROOT.parent==Path('/root/qtrade-local/operator-evidence')
    assert sys.dont_write_bytecode and sys.flags.safe_path and os.environ['LIVE_TRADING_ENABLED']=='false'
    assert PYTHON.resolve()==Path(sys.executable).resolve()
    assert inventory()==expected['files']
    for p in SOURCE.rglob('*'):assert not p.is_symlink()
    before={'source_sha256':inventory(),'nfs':disk(BASE),'local':disk(ROOT)}
    save('before.json',before)
    assert before['nfs']['free_bytes']>=100*1024**3 and before['local']['free_bytes']>=2*1024**3
    for name,relative in {'qtrade':'src/qtrade/__init__.py',
        'qtrade.research.factor_strategy_backtest':'src/qtrade/research/factor_strategy_backtest.py',
        'scripts.run_factor_strategy_backtest':'scripts/run_factor_strategy_backtest.py',
        'scripts.run_qlib_strategy_comparison':'scripts/run_qlib_strategy_comparison.py'}.items():
        module=importlib.import_module(name)
        assert Path(module.__file__).resolve()==SOURCE/relative
    import qlib
    assert qlib.__version__=='0.9.7'
    assert (BASE/'qlib-env').resolve() in Path(qlib.__file__).resolve().parents
    helper=HELPER.read_bytes();assert sha(helper)==HELPER_SHA
    node=next(n for n in ast.parse(helper).body if isinstance(n,ast.FunctionDef) and n.name=='_cleanup_git_group')
    namespace={'os':os,'signal':signal,'time':time,'subprocess':subprocess,'suppress':suppress}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(HELPER),'exec'),namespace)
    cleanup=namespace['_cleanup_git_group']
    command=[str(PYTHON),'-P','-B','-m','pytest','-q','-p','no:cacheprovider',
        '--basetemp',str(ROOT/'pytest-work'),'--junitxml='+str(ROOT/'evidence/linux-results.xml'),
        str(SOURCE/'tests/test_factor_strategy_backtest.py')]
    env={'PATH':str(PYTHON.parent)+':/usr/bin:/bin','LC_ALL':'C.UTF-8',
        'PYTHONPATH':str(SOURCE/'src')+':'+str(SOURCE),'PYTHONNOUSERSITE':'1',
        'PYTHONDONTWRITEBYTECODE':'1','PYTHONSAFEPATH':'1','LIVE_TRADING_ENABLED':'false',
        'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1',
        'NUMEXPR_MAX_THREADS':'1','TMPDIR':str(ROOT/'runtime-tmp'),
        'XDG_CACHE_HOME':str(ROOT/'runtime-cache'),'MPLCONFIGDIR':str(ROOT/'runtime-cache/mpl')}
    for name in ('work','runtime-tmp','runtime-cache','runtime-cache/mpl'):(ROOT/name).mkdir()
    try:
        with (ROOT/'evidence/stdout.txt').open('xb') as stdout,(ROOT/'evidence/stderr.txt').open('xb') as stderr:
            child=subprocess.Popen(command,cwd=ROOT/'work',env=env,start_new_session=True,
                stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr)
            fields=Path(f'/proc/{child.pid}/stat').read_text().rsplit(')',1)[1].split()
            save('owned-child.json',{'pid':child.pid,'start_ticks':fields[19],'pgid':os.getpgid(child.pid),
                'argv':command,'timeout_seconds':240,'cleanup_source_sha256':HELPER_SHA})
            try:
                rc=child.wait(timeout=240)
            finally:
                cleanup(child)  # Exact verified AST, TERM2 + KILL/wait2; no Popen context-manager wait.
    except BaseException as failure:
        error=type(failure).__name__
    finally:
        after={'source_sha256':inventory(),'nfs':disk(BASE),'local':disk(ROOT)}
        save('after.json',after)
        save('execution.json',{'returncode':rc,'error_class':error,'elapsed_seconds':time.monotonic()-started,
            'source_before_after_equal':after['source_sha256']==before['source_sha256'],
            'base_git_revision':expected['base_git_revision'],'overlay_sha256':expected['overlay_sha256'],
            'selected_git_plus_overlays_not_pure_git':True,'qlib_version':qlib.__version__,
            'qlib_origin':str(Path(qlib.__file__).resolve()),'network_queue_or_real_dataset_used':False,
            'original_registry_or_report_written':False})
    assert rc==0 and error is None and after['source_sha256']==before['source_sha256']


if __name__=='__main__':main()
