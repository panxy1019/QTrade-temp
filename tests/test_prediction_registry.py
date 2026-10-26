import sqlite3
from datetime import timedelta

import pytest
from test_model_registry import DATA, NOW, command, stage

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.model_registry import ModelRegistry
from qtrade.research.prediction_registry import PredictionRegistry


@pytest.fixture
def ledger(tmp_path):
    models = ModelRegistry(tmp_path/'models.sqlite')
    stage(models)
    return PredictionRegistry(models)


def binding(ledger):
    approval = ledger.models.apply(command(), current_data_version=DATA, now=NOW)
    return {'model_id': 'a'*64, 'approval_sha256': approval['evidence_sha256'],
            'data_version_id': DATA, 'data_event_id': 'e'*64,
            'observation_universe_version': 'f'*64, 'data_end': '2026-10-26',
            'code_revision': 'c'*40}


def result(request):
    b = request['bindings']
    contract = signed({**AUTHORITY, 'data_version_id': b['data_version_id'],
                       'model_bundle_sha256': b['model_id'], 'data_end': b['data_end'],
                       'observation_universe_version': b['observation_universe_version'],
                       'generated_at': (NOW+timedelta(minutes=1)).isoformat(),
                       'expected_execution_at': (NOW+timedelta(hours=12)).isoformat(),
                       'forward_return_observed': False})
    return signed({**AUTHORITY, 'schema_version': 'qtrade.daily-prediction.v1',
                   'prediction_id': request['prediction_id'], 'bindings': b,
                   'request_sha256': request['evidence_sha256'], 'contract': contract,
                   'state': 'SEALED_ACTIVE_MODEL_PREDICTION', 'auto_predict_enabled': True,
                   'feature_count': 158, 'score_count': 888, 'limitations': ['non-PIT']})


def test_unapproved_candidate_never_generates_automatic_prediction(ledger):
    assert ledger.active() is None
    with pytest.raises(ValueError, match='CURRENT_APPROVAL'):
        ledger.claim({'model_id': 'a'*64, 'approval_sha256': 'b'*64, 'data_version_id': DATA,
                      'data_event_id': 'e'*64, 'observation_universe_version': 'f'*64,
                      'code_revision': 'c'*40, 'data_end': '2026-10-26'}, now=NOW)
    assert ledger.tasks() == []


def test_repeated_data_event_claim_and_completion_are_idempotent(ledger):
    b = binding(ledger)
    request = ledger.claim(b, now=NOW)
    assert ledger.claim(b, now=NOW+timedelta(seconds=10)) == request
    assert ledger.start(request['prediction_id'], now=NOW)
    assert not ledger.start(request['prediction_id'], now=NOW)
    proof = result(request)
    assert ledger.finish(proof, current_data_version=DATA,
                         now=NOW+timedelta(minutes=2)) == 'COMPLETE'
    assert ledger.finish(proof, current_data_version='e'*64,
                         now=NOW+timedelta(days=1)) == 'COMPLETE'
    assert len(ledger.tasks()) == 1
    assert ledger.models.snapshot()['active_model'] == 'a'*64


@pytest.mark.parametrize('changed', ['model', 'data', 'expired'])
def test_superseded_computation_is_kept_but_cannot_be_current_signal(ledger, changed):
    request = ledger.claim(binding(ledger), now=NOW)
    assert ledger.start(request['prediction_id'], now=NOW)
    data, clock = DATA, NOW+timedelta(minutes=2)
    if changed == 'model':
        stage(ledger.models, 'b')
        ledger.models.apply(command('b', expected='a'*64, request='2'),
                            current_data_version=DATA, now=NOW)
    elif changed == 'data':
        data = 'e'*64
    else:
        clock += timedelta(days=1)
    assert ledger.finish(result(request), current_data_version=data, now=clock) == 'SUPERSEDED'
    assert ledger.tasks()[0]['result'] is not None
    assert ledger.tasks()[0]['error_code']


def test_old_validation_generated_before_approval_cannot_be_relabelled(ledger):
    request = ledger.claim(binding(ledger), now=NOW)
    ledger.start(request['prediction_id'], now=NOW)
    proof = result(request)
    proof['contract'] = signed({**{k: v for k, v in proof['contract'].items()
        if k != 'evidence_sha256'}, 'generated_at': (NOW-timedelta(minutes=1)).isoformat()})
    proof = signed({k: v for k, v in proof.items() if k != 'evidence_sha256'})
    with pytest.raises(ValueError, match='BINDING_OR_CLOCK'):
        ledger.finish(proof, current_data_version=DATA, now=NOW)
    assert ledger.tasks()[0]['state'] == 'RUNNING'


def test_completion_and_event_are_one_transaction(ledger):
    request = ledger.claim(binding(ledger), now=NOW)
    ledger.start(request['prediction_id'], now=NOW)
    with ledger.models.connect() as c:
        c.execute("CREATE TRIGGER fail_prediction_event BEFORE INSERT ON prediction_events "
                  "BEGIN SELECT RAISE(ABORT,'injected event failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        ledger.finish(result(request), current_data_version=DATA,
                      now=NOW+timedelta(minutes=2))
    task = ledger.tasks()[0]
    assert task['state'] == 'RUNNING' and task['result'] is None


def test_interrupted_run_requires_reconciliation_and_does_not_implicitly_rerun(ledger):
    request = ledger.claim(binding(ledger), now=NOW)
    identity = request['prediction_id']
    ledger.start(identity, now=NOW)
    ledger.fail(identity, 'WORKER_RESTART_RECONCILIATION_REQUIRED', now=NOW, interrupted=True)
    assert not ledger.start(identity, now=NOW)
    assert ledger.finish(result(request), current_data_version=DATA,
                         now=NOW+timedelta(minutes=2)) == 'COMPLETE'


def test_switch_back_creates_a_new_activation_epoch_not_a_replayed_prediction(ledger):
    first = ledger.claim(binding(ledger), now=NOW)
    stage(ledger.models, 'b')
    ledger.models.apply(command('b', expected='a'*64, request='2'),
                        current_data_version=DATA, now=NOW)
    restored = ledger.models.apply(command(expected='b'*64, operation='ROLLBACK', request='3'),
                                   current_data_version=DATA, now=NOW)
    new_binding = {**first['bindings'], 'approval_sha256': restored['evidence_sha256']}
    second = ledger.claim(new_binding, now=NOW)
    assert first['prediction_id'] != second['prediction_id']
    assert not ledger.start(first['prediction_id'], now=NOW)
    assert ledger.start(second['prediction_id'], now=NOW)
