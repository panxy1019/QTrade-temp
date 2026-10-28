"""Two isolated pack-generation measurements; no source writes or network."""
from pathlib import Path
import hashlib,importlib.util,json,os,shutil,time

ROOT=Path(__file__).parent
STAGE=Path('/root/qtrade-local/operator-evidence/publication-pack-cache-stage-20261028T140528Z')
RELAY=Path('/root/qtrade-local/operator-evidence/publication-relay-cold-20261028T130000Z')

def load(name,path,sha):
    assert hashlib.sha256(path.read_bytes()).hexdigest()==sha
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def main():
    assert ROOT.parent==Path('/root/qtrade-local/operator-evidence') and ROOT.name.startswith('publication-wire-pack-')
    assert not any(p.is_symlink() for p in (ROOT,*ROOT.parents))
    profile=load('frozen_cold_profile',STAGE/'profile_publication_pack_cache_cold_20261028.py',
        'ff1319715de4f9904a64790bb269800a3ae5a4ca2cc895fa5064bc276ba3e1a3')
    op=load('frozen_operator',RELAY/'publication_native_relay_operator_v3_20261028.py',
        '76bf3e2653ab51491b966db9f62c415c0d7161b4d642e7f89cefc30b687a61fc')
    op.guard_scope();op.runtime_origins()
    from scripts.publish_research_snapshot import _cleanup_git_group
    certificate=json.loads((STAGE/'profile.json').read_bytes())
    assert certificate['state']=='CANDIDATE_VALIDATED_NOT_INSTALLED'
    assert certificate['target_commit']==profile.TARGET and certificate['base_commit']==profile.ANCESTOR
    cache=ROOT/'cache.git';cache.mkdir()
    op.git(cache,['init','--bare',str(cache)])
    with (cache/'objects/info/alternates').open('xb') as f:
        f.write((str(op.SOURCE/'.git/objects')+'\n').encode());f.flush();os.fsync(f.fileno())
    for extension,expected in [('pack',certificate['pack']['sha256']),('idx',certificate['pack_index_sha256'])]:
        raw=(STAGE/('candidate.'+extension)).read_bytes();assert hashlib.sha256(raw).hexdigest()==expected
        target=cache/'objects/pack'/('pack-'+certificate['pack_id']+'.'+extension)
        with target.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    result={};deadline=time.monotonic()+120
    for label,repo in [('source',op.SOURCE/'.git'),('isolated_cached',cache)]:
        command=['/usr/bin/git','-c','pack.useSparse=false','-c','core.hooksPath=/dev/null',
            '-c','gc.auto=0','--git-dir='+str(repo),'pack-objects','--revs','--stdout','--thin','--delta-base-offset']
        value=profile.stream_pack(command,op.clean_env(),
            (profile.TARGET+'\n^'+profile.ANCESTOR+'\n').encode(),ROOT/(label+'.pack'),deadline,_cleanup_git_group)
        with (ROOT/(label+'.pack')).open('rb') as f:header=f.read(12)
        assert header[:4]==b'PACK';value['wire_object_count']=int.from_bytes(header[8:12],'big')
        result[label]=value
    report={'schema_version':'qtrade.wire-pack-comparison.v1','target':profile.TARGET,'base':profile.ANCESTOR,
        'results':result,'source_objects_written':False,'network_used':False,'production_cache_installed':False,
        'git_push_accepted':False,'filesystem_cache_purged':False,'diagnostic_total_budget_seconds':120}
    (ROOT/'report.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n');print(json.dumps(report))

if __name__=='__main__':main()
