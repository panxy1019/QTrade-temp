from __future__ import annotations
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parent
OLD=ROOT.parent/'publication-cold-recovery-preparation-20261028T143911Z-9d25431d'
SOURCE_SHA='738c295d55d9b1a4081ca2547d2bfdad3ea59553d0102cc152bb34b3916917fa'
TEST_SHA='c00aeac29cf2697b68b68046f56a877d28c205f6e4023868c20510fa30e8742f'
BASE_SHA='76bf3e2653ab51491b966db9f62c415c0d7161b4d642e7f89cefc30b687a61fc'


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def entry(path):return {'bytes':path.stat().st_size,'sha256':digest(path)}
def load(path):return json.loads(path.read_text(encoding='utf-8'))


def xml(path,tests,failures):
    root=ET.parse(path).getroot();suites=list(root) if root.tag=='testsuites' else [root]
    assert sum(int(s.attrib['tests']) for s in suites)==tests
    assert sum(int(s.attrib['failures']) for s in suites)==failures
    assert sum(int(s.attrib.get('errors','0')) for s in suites)==0
    assert sum(int(s.attrib.get('skipped','0')) for s in suites)==0
    assert len(root.findall('.//testcase'))==tests
    return {'tests':tests,'failures':failures,'errors':0,'skipped':0,'file':entry(path)}


def main():
    assert digest(ROOT/'publication_cold_recovery_operator_20261028.py')==SOURCE_SHA
    assert digest(ROOT.parent/'publication_cold_recovery_operator_20261028.py')==SOURCE_SHA
    assert digest(ROOT/'test_publication_cold_recovery_operator_20261028.py')==TEST_SHA
    assert digest(ROOT/'publication_native_relay_operator_v3_20261028.py')==BASE_SHA
    assert digest(ROOT.parent/'publication_native_relay_operator_v3_20261028.py')==BASE_SHA
    assert digest(OLD/'publication_cold_recovery_operator_20261028.py')=='7ec53cc558c62c56dade0cf390ba274ff34de2c97c80ddb664a6103e6ce79f09'
    assert digest(OLD/'test_publication_cold_recovery_operator_20261028.py')=='93ee73337dee4260a8fdd14b2526d0989f162991623bc3446004735814cbaf5c'
    actual=load(ROOT/'linux-actual/execution.json')
    assert actual['returncode']==0 and actual['network_git_push_performed'] is False
    assert actual['ordinary_production_changed'] is False
    for relative,expected in actual['source_sha256'].items():assert digest(ROOT/relative)==expected,relative
    old_actual=load(OLD/'linux-actual/execution.json')
    for relative,expected in old_actual['source_sha256'].items():assert digest(OLD/relative)==expected,relative
    result={'windows64':xml(ROOT/'windows-results.xml',64,0),
        'linux64':xml(ROOT/'linux-actual/linux-results.xml',64,0),
        'retained_windows57_red':xml(OLD/'windows-results.xml',57,1),
        'retained_windows60_red':xml(OLD/'windows-final-results.xml',60,3),
        'intermediate_windows60':xml(OLD/'windows-green-results.xml',60,0),
        'intermediate_windows61':xml(OLD/'windows-frozen-results.xml',61,0),
        'intermediate_linux61':xml(OLD/'linux-actual/linux-results.xml',61,0)}
    retained=ROOT/'retained-native';retained.mkdir(exist_ok=True)
    for label,trial in [('windows57-red',OLD/'pytest-work'),('windows60-red',OLD/'pytest-work-final'),
                        ('windows60-green',OLD/'pytest-work-green'),('windows61',OLD/'pytest-work-frozen'),
                        ('windows64',ROOT/'pytest-work')]:
        for name in ('native-cas-raw-result.json','native-commands.json'):
            original=trial/'test_native_server_old_oid_cas0'/name
            if original.exists():
                raw=original.read_bytes();destination=retained/(label+'-'+name)
                if destination.exists():assert destination.read_bytes()==raw
                else:destination.write_bytes(raw)
    evidence={'schema_version':'qtrade.cold-recovery-preparation-evidence.v1',
        'source_sha256':SOURCE_SHA,'test_sha256':TEST_SHA,'base_v3_sha256':BASE_SHA,
        'results':result,'source_freeze_verified':True,'linux_report_source_inventory_verified':True,
        'ordinary_export_seconds':45,'ordinary_git_seconds':90,'proposed_cold_push_seconds':180,
        'cold180_real_performance_pass':False,'new_user_push_approval_obtained':False,
        'this_agent_network_or_production_operation':False,'normal_production_source_modified':False}
    (ROOT/'evidence-summary.json').write_text(json.dumps(evidence,indent=2)+'\n',encoding='utf-8',newline='\n')
    files={}
    for path in sorted(ROOT.rglob('*')):
        if not path.is_file() or path.name=='manifest.json' or 'pytest-work' in path.relative_to(ROOT).parts:continue
        assert not path.is_symlink()
        files[path.relative_to(ROOT).as_posix()]=entry(path)
    intermediate={}
    for name in ('publication_cold_recovery_operator_20261028.py','publication_native_relay_operator_v3_20261028.py',
                 'test_publication_cold_recovery_operator_20261028.py','native_receive_pack_barrier.py',
                 'first-red-test-source.py','second-red-test-source.py','operator-before-explicit-close.py',
                 'windows-results.xml','windows-final-results.xml','windows-green-results.xml','windows-frozen-results.xml',
                 'linux-actual/execution.json','linux-actual/linux-results.xml','linux-actual/stdout.txt','linux-actual/stderr.txt',
                 'fixtures/PUSH_INTENT.json','fixtures/PUSH_UNCERTAIN.json','PREPARATION.md'):
        intermediate[name]=entry(OLD/name)
    manifest={'schema_version':'qtrade.cold-recovery-preparation-manifest.v1','root':str(ROOT),
        'files':files,'intermediate_reference':{'root':str(OLD),'files':intermediate},
        'dynamic_pytest_work_excluded':True,'source_sha256':SOURCE_SHA,'test_sha256':TEST_SHA,
        'ordinary_export_seconds':45,'ordinary_git_seconds':90,'cold180_status':'PROPOSED_NOT_APPROVED_NOT_PERFORMANCE_PASSED',
        'actual_send_executed':False}
    output=ROOT/'manifest.json'
    output.write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps({'manifest':str(output),'sha256':digest(output),'files':len(files),
        'intermediate_files':len(intermediate),'windows':64,'linux':64,'push_authorization':'PENDING'}))


if __name__=='__main__':main()
