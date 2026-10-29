"""Executable negative examples for a proposed contract, never real acceptance."""
import copy
import json
from pathlib import Path
from datetime import datetime,timezone

import pytest
import contract_draft as c

REPO=Path('E:/QTrade-git')
MODEL='c'*64;LATER='b'*64;SOURCE='d'*40;ACTIVE='e'*64;APPROVAL='f'*64

def signed(value):return {**value,'evidence_sha256':c.digest(value)}

def legacy():
    root=REPO/'docs/evidence/qlib-archived-version-20261028/cluster'
    return json.loads((root/'summary.json').read_bytes()),json.loads((root/'job-and-registry.json').read_bytes())

def anchor(summary,record):
    return c.legacy_anchor(summary,record,
        expected_model='98d27455a7cb224ef3ecbd7cf850455f386fbb93baf8137a4d789e142ab91795',
        expected_job='578bb6107577406185782a08e1c499d7',
        expected_version='d33a74663373b1d4a0d7cf1ecf1622b34657a6fda08aacc7bacadb70d47ec4fa')

def reseal(value):
    value.pop('evidence_sha256',None);value['evidence_sha256']=c.digest(value)

def test_existing_legacy_metadata_uses_explicit_binding_without_rolling_plan():
    summary,record=legacy();assert 'rolling_plan' not in summary
    result=anchor(summary,record)
    assert result['state']=='METADATA_CONTRACT_CHECKED_REAL_ACCEPTANCE_PENDING'
    assert result['real_archive_verified'] is False and result['whole_step1_accepted'] is False

@pytest.mark.parametrize('damage',['wrong_version','wrong_dataset','wrong_model','job_failed',
    'wrong_strategy','fabricated_rolling_plan'])
def test_legacy_changed_metadata_cannot_be_rescued_by_new_outer_hash(damage):
    summary,record=legacy()
    if damage=='wrong_version':
        summary['data_binding']['data_version_id']='0'*64;reseal(summary['data_binding'])
        record['request']['spec']['data_binding']=copy.deepcopy(summary['data_binding'])
        record['request']['spec_sha256']=c.digest(record['request']['spec'])
    elif damage=='wrong_dataset':summary['dataset_sha256']='0'*64
    elif damage=='wrong_model':record['result']['model_artifact_id']='0'*64
    elif damage=='job_failed':record['job']['state']='FAILED'
    elif damage=='wrong_strategy':record['strategy_association'][0]['version']='0'*64
    else:summary['rolling_plan']={'data_version_id':summary['data_binding']['data_version_id']}
    reseal(summary)
    with pytest.raises(ValueError):anchor(summary,record)

def facts():
    before={'active_model':ACTIVE,'approval_sha256':APPROVAL,'candidate':{
        'model_id':MODEL,'state':'STAGED','activation_count':0,'activated_at':None}}
    def proof(identity,version,day,at):
        contract=signed({'data_version_id':version,'data_end':day,'generated_at':at})
        return signed({'validation_id':identity,'model_bundle_sha256':MODEL,'contract':contract,
            'model_activated':False,'fit_methods_forbidden':True,'fitted_processors_unchanged':True})
    old=proof('1'*64,'a'*64,'2026-10-27','2026-10-27T17:49:20+00:00')
    target=proof('2'*64,LATER,'2026-10-28','2026-10-28T12:02:00+00:00')
    events=[]
    for kind in ['TRAINED','VALIDATING','VALIDATED','STAGED']:
        details={} if kind=='TRAINED' else {'imported_verified_validation':old['validation_id']}
        events.append(signed({'model_id':MODEL,'kind':kind,'at':'2026-10-27T17:50:00+00:00','details':details}))
    events.append(signed({'model_id':MODEL,'kind':'INPUT_VALIDATED','at':'2026-10-28T12:03:00+00:00',
        'details':{'validation_id':target['validation_id']}}))
    bindings={'kind':'QLIB_MODEL_VALIDATION','model_id':MODEL,'data_version_id':LATER,'source_revision':SOURCE}
    identity=c.digest(bindings)[:32]
    request=signed({**bindings,'job_id':identity,'live_trading_enabled':False})
    job={'id':identity,'request':request,'state':'COMPLETE','error':None,
        'result':{'validation_id':target['validation_id']},'updated':'2026-10-28T12:04:00+00:00',
        'events':[{'state':state,'at':at} for state,at in zip(['QUEUED','RUNNING','COMPLETE'],
            ['2026-10-28T12:00:00+00:00','2026-10-28T12:01:00+00:00','2026-10-28T12:04:00+00:00'])]}
    return {'before':before,'after':copy.deepcopy(before),'head_version':LATER,
        'trained_version':'9'*64,'trained_day':'2026-10-26','later_day':'2026-10-28',
        'candidate_events':events,'proofs':[old,target],'jobs':[job]}

def check(value):
    return c.revalidation_metadata(value,model=MODEL,later=LATER,source=SOURCE,
        expected_active=ACTIVE,expected_approval=APPROVAL,latest_complete_day='2026-10-28',
        now=datetime(2026,10,28,12,10,tzinfo=timezone.utc))

def test_mock_revalidation_preserves_old_staging_and_never_returns_real_pass():
    value=facts();value['real_archive_verified']=True;value['state']='PASS'
    result=check(value)
    assert result['state']=='METADATA_CONTRACT_CHECKED_REAL_ACCEPTANCE_PENDING'
    assert result['real_archive_verified'] is False and result['real_validation_rows_verified'] is False
    assert result['whole_step1_accepted'] is False and result['production_apply_authorized'] is False

@pytest.mark.parametrize('damage',['context_changed','active_changed','approval_changed',
    'activated','boolean_count','missing_old_proof','missing_import','duplicate_import',
    'unknown_registry_event','missing_job','failed_job','duplicate_job','wrong_job_source',
    'job_skip_running','future_generation','same_version','future_day','refit_allowed'])
def test_revalidation_negative_boundaries(damage):
    value=facts()
    if damage=='context_changed':value['after']['active_model']='0'*64
    elif damage=='active_changed':value['before']['active_model']=value['after']['active_model']='0'*64
    elif damage=='approval_changed':value['before']['approval_sha256']=value['after']['approval_sha256']='0'*64
    elif damage in ['activated','boolean_count']:
        for key in ['before','after']:value[key]['candidate']['activation_count']=1 if damage=='activated' else True
    elif damage=='missing_old_proof':value['proofs']=value['proofs'][1:]
    elif damage=='missing_import':value['candidate_events']=value['candidate_events'][:4]
    elif damage=='duplicate_import':value['candidate_events'].append(copy.deepcopy(value['candidate_events'][-1]))
    elif damage=='unknown_registry_event':
        value['candidate_events'][-1]['kind']='ACTIVATE';reseal(value['candidate_events'][-1])
    elif damage=='missing_job':value['jobs']=[]
    elif damage=='failed_job':value['jobs'][0]['state']='FAILED'
    elif damage=='duplicate_job':value['jobs'].append(copy.deepcopy(value['jobs'][0]))
    elif damage=='wrong_job_source':
        value['jobs'][0]['request']['source_revision']='0'*40;reseal(value['jobs'][0]['request'])
    elif damage=='job_skip_running':value['jobs'][0]['events'].pop(1)
    elif damage=='future_generation':
        value['proofs'][-1]['contract']['generated_at']='2026-10-29T12:00:00+00:00'
        reseal(value['proofs'][-1]['contract']);reseal(value['proofs'][-1])
    elif damage=='same_version':value['trained_version']=LATER
    elif damage=='future_day':value['later_day']='2026-10-29'
    elif damage=='refit_allowed':value['proofs'][-1]['fit_methods_forbidden']=False;reseal(value['proofs'][-1])
    with pytest.raises(ValueError):check(value)
