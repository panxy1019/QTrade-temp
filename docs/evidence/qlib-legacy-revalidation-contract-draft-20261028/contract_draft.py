"""Pure contract prototype, not an on-disk auditor or a production acceptance gate.

Even successful metadata checks always return REAL_ACCEPTANCE_PENDING. Original
archive/Parquet/raw-source verification must be implemented separately first.
No filesystem, subprocess, network, queue, registry or account API is imported.
"""
from datetime import datetime
import hashlib
import json

def require(value,code):
    if not value:raise ValueError('LEGACY_REVALIDATION_'+code)

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
        ensure_ascii=False,allow_nan=False).encode()).hexdigest()

def checked(value):
    require(type(value) is dict and value.get('evidence_sha256')==digest(
        {k:v for k,v in value.items() if k!='evidence_sha256'}),'SIGNED_METADATA_HASH')
    if 'live_trading_enabled' in value:
        require(value['live_trading_enabled'] is False,'LIVE_DISABLED')

def clock(value,now):
    stamp=datetime.fromisoformat(value)
    require(stamp.tzinfo is not None and now.tzinfo is not None and stamp<=now,'FUTURE_OR_NAIVE_CLOCK')
    return stamp

def pending(**values):
    return {'state':'METADATA_CONTRACT_CHECKED_REAL_ACCEPTANCE_PENDING',
        'real_archive_verified':False,'real_validation_rows_verified':False,
        'whole_step1_accepted':False,'production_apply_authorized':False,
        'jobs_enqueued':False,'model_activated':False,**values}

def legacy_anchor(summary,record,*,expected_model,expected_job,expected_version):
    """Check explicit bound-research metadata without inventing a rolling plan."""
    checked(summary)
    require('rolling_plan' not in summary,'EXPLICIT_LEGACY_CONTRACT_REQUIRED')
    request=record['request'];result=record['result'];job=record['job']
    binding=summary['data_binding'];checked(binding)
    require(job['id']==request['job_id']==expected_job and job['state']=='COMPLETE'
        and job['error'] is None and request['kind']=='QLIB_RESEARCH_JOB'
        and request['live_trading_enabled'] is False,'ORIGINAL_RESEARCH_JOB_REQUIRED')
    require(request['spec']['data_binding']==binding
        and request['spec_sha256']==digest(request['spec']),'LEGACY_REQUEST_BINDING_CHANGED')
    require(binding['data_version_id']==expected_version
        and binding['actual_end']==summary['data_end']
        and binding['dataset_sha256']==summary['dataset_sha256'],'EXACT_TRAINING_VERSION_REQUIRED')
    require(result['model_artifact_id']==record['model']['model_id']==expected_model
        and result['run_id']==summary['run_id'],'EXACT_TRAINED_MODEL_REQUIRED')
    require(request['expected_strategy_version']==summary['strategy_version']
        ==result['strategy_version']
        and len(record['strategy_association'])==1
        and record['strategy_association'][0]['job_id']==expected_job
        and record['strategy_association'][0]['version']==summary['strategy_version'],
        'ORIGINAL_STRATEGY_ASSOCIATION_REQUIRED')
    return pending(training_contract='LEGACY_BOUND_RESEARCH_METADATA_ONLY',
        candidate_model=expected_model,trained_version=expected_version,
        training_origin_certified=False,strictly_unseen_forward_claimed=False)

def revalidation_metadata(facts,*,model,later,source,expected_active,expected_approval,
                          latest_complete_day,now):
    """Draft lifecycle/job contract only; accepts no self-attested real PASS."""
    before=facts['before'];after=facts['after'];require(before==after,'SNAPSHOT_CHANGED')
    require(before['active_model']==expected_active
        and before['approval_sha256']==expected_approval,'EXPLICIT_ACTIVE_APPROVAL_CHANGED')
    candidate=before['candidate']
    require(candidate['model_id']==model and candidate['state']=='STAGED'
        and type(candidate['activation_count']) is int and candidate['activation_count']==0
        and candidate['activated_at'] is None,'CANDIDATE_MUST_REMAIN_UNACTIVATED')
    require(facts['head_version']==later and facts['trained_version']!=later
        and facts['trained_day']<facts['later_day']<=latest_complete_day,'GENUINELY_LATER_COMPLETED_DAY')
    events=facts['candidate_events'];base=['TRAINED','VALIDATING','VALIDATED','STAGED']
    require(len(events)>4 and [e['kind'] for e in events[:4]]==base
        and all(e['kind']=='INPUT_VALIDATED' for e in events[4:]),'REVALIDATION_EVENT_CHAIN')
    times=[clock(e['at'],now) for e in events]
    require(times==sorted(times),'REGISTRY_CLOCK_ORDER')
    for event in events:
        checked(event);require(event['model_id']==model,'REGISTRY_MODEL_IDENTITY')
    proofs=facts['proofs']
    require(len({p['validation_id'] for p in proofs})==len(proofs),'DUPLICATE_PROOF')
    for proof in proofs:
        checked(proof);checked(proof['contract'])
        require(proof['model_bundle_sha256']==model and proof['model_activated'] is False
            and proof['fit_methods_forbidden'] is True
            and proof['fitted_processors_unchanged'] is True,'PROOF_FIT_OR_MODEL_CHANGED')
        clock(proof['contract']['generated_at'],now)
    indexed={p['validation_id']:p for p in proofs}
    original={events[i]['details']['imported_verified_validation'] for i in (1,2,3)}
    require(len(original)==1 and original<=set(indexed),'ORIGINAL_STAGING_PROOF_RETAINED')
    ids=[e['details']['validation_id'] for e in events[4:]]
    require(len(set(ids))==len(ids) and set(ids)<=set(indexed),'UNIQUE_REGISTERED_INPUT_VALIDATION')
    target=[p for p in proofs if p['contract']['data_version_id']==later]
    require(len(target)==1 and target[0]['validation_id'] in ids,'UNIQUE_TARGET_PROOF_IMPORTED')
    proof=target[0];identity=proof['validation_id']
    require(proof['contract']['data_end']==facts['later_day'],'TARGET_PROOF_DAY')
    jobs=facts['jobs'];require(len(jobs)==1,'EXACTLY_ONE_REAL_JOB_REQUIRED')
    job=jobs[0];request=job['request'];checked(request)
    bindings={'kind':'QLIB_MODEL_VALIDATION','model_id':model,'data_version_id':later,'source_revision':source}
    require(all(request.get(k)==v for k,v in bindings.items())
        and job['id']==request['job_id']==digest(bindings)[:32],'TARGET_JOB_BINDING')
    require(job['state']=='COMPLETE' and job['error'] is None
        and job['result']['validation_id']==identity,'TARGET_JOB_NOT_COMPLETE')
    states=[e['state'] for e in job['events']]
    require(states in (['QUEUED','RUNNING','COMPLETE'],
        ['QUEUED','RUNNING','VALIDATING','COMPLETE']),'REAL_JOB_EVENT_CHAIN')
    stamps=[clock(e['at'],now) for e in job['events']]
    require(stamps==sorted(stamps) and stamps[0]<=clock(proof['contract']['generated_at'],now)
        <=stamps[-1]<=clock(job['updated'],now),'REAL_JOB_CLOCK_CHAIN')
    imported=next(e for e in events[4:] if e['details']['validation_id']==identity)
    require(clock(proof['contract']['generated_at'],now)<=clock(imported['at'],now),
        'IMPORT_BEFORE_PROOF')
    return pending(validation_contract='STAGED_REVALIDATION_METADATA_ONLY',
        candidate_model=model,later_version=later,proof_id=identity,
        missing_real_gates=['FULL_ARCHIVE_CHECKSUMS_AND_SOURCE_ORIGINS',
            'EXACT_VERSION_AND_RESTORE_PARITY','FULL_FEATURE_SCORE_PARQUET_REPLAY',
            'RAW_DAY_RECEIPTS_AND_COMPLETE_SQLITE_FACTS'])
