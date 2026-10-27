from __future__ import annotations

import json
import os
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_qlib_worker import worker as worker

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research import model_promotion_policy as policy_module
from qtrade.research.forward_prediction import next_execution
from qtrade.research.prediction_registry import PredictionRegistry
from qtrade.research.workflow_queue import seal
from scripts import online_model_control as adapter
from scripts import run_qlib_worker as worker_module

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
SECRET = 'test-only-standing-policy-signature'*2
DATA = 'd'*64


def test_promotion_reserves_non_pristine_paper_without_account_or_recovery(runtime):
    runtime.add()
    runtime.configure()
    receipt = runtime.promotion.tick(now=NOW)
    assert receipt['state'] == 'APPLIED'
    guards = list(runtime.promotion.paper_root.glob('*/research-promotion-reservation.json'))
    assert len(guards) == 1
    value = json.loads(guards[0].read_bytes())
    assert value['model_id'] == 'b'*64
    assert value['paper_recovery_authorized'] is False
    assert value['paper_initialization_authorized'] is False
    assert not (guards[0].parent/'forward.sqlite').exists()
    assert guards[0].parent.is_dir()  # The existing owner sees pristine=False.
    before = guards[0].read_bytes()
    runtime.promotion.tick(now=NOW)
    assert guards[0].read_bytes() == before


def test_existing_forward_owner_cannot_reuse_initial_actor_for_reserved_model(runtime):
    from types import SimpleNamespace

    from scripts.run_qlib_forward_service import ForwardService

    runtime.add()
    runtime.configure()
    assert runtime.promotion.tick(now=NOW)['state'] == 'APPLIED'
    guard, = runtime.promotion.paper_root.glob('*/research-promotion-reservation.json')
    marker = json.loads(guard.read_bytes())
    owner = SimpleNamespace(root=runtime.promotion.paper_root, books={}, links={},
                            initialize_paper='original-initial-start-actor')
    # This is the unchanged old owner's actual _open decision, with a real
    # persisted PAPER book. Existing directory means no call to recover.
    ForwardService._open(owner, marker['book_id'], marker['config'],
                         pristine=not guard.parent.exists())
    book = owner.books[marker['book_id']]
    try:
        assert book.bundle.runtime.status.running is False
        assert not (guard.parent/'initialization.json').exists()
    finally:
        book.db.close()


def test_existing_unrecognized_paper_directory_blocks_model_promotion(runtime):
    runtime.add()
    runtime.configure()
    config = {'strategy_id': 'approved_model_'+'b'*64, 'mode': 'PAPER',
              'cash': '1000000', 'fee_version': 'forward-research-cost-v1'}
    root = runtime.promotion.paper_root/signed(config)['evidence_sha256']
    root.mkdir(parents=True)
    (root/'operator-note.txt').write_bytes(b'preserve-existing-book')
    assert runtime.promotion.tick(now=NOW)['state'] == 'REJECTED'
    assert (root/'operator-note.txt').read_bytes() == b'preserve-existing-book'
    assert not (root/'research-promotion-reservation.json').exists()


def resign(value):
    return signed({k: v for k, v in value.items() if k != 'evidence_sha256'})


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('LIVE_TRADING_ENABLED', 'false')
    online = adapter.OnlineModels(tmp_path, tmp_path/'models.sqlite', tmp_path/'data.sqlite')
    calendar = Calendar(Path('configs/observer_calendar_2026.json'))
    clocks = [NOW]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=UTC):
            return clocks[0].astimezone(tz)
    monkeypatch.setattr(adapter, 'datetime', Clock)
    if os.name == 'nt':
        def immutable(path, document):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x') as handle:
                json.dump(document, handle)
        monkeypatch.setattr(adapter, 'write_json_immutable', immutable)
        monkeypatch.setattr(policy_module, 'write_json_immutable', immutable)
    event = signed({**AUTHORITY, 'kind': 'QLIB_DATA_READY', 'version_id': DATA,
                    'admitted_at': (NOW-timedelta(hours=2)).isoformat()})
    with sqlite3.connect(online.data_database) as connection:
        connection.executescript('''
            CREATE TABLE data_heads(frequency TEXT,version_id TEXT);
            CREATE TABLE data_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT,version_id TEXT,kind TEXT,payload TEXT);
        ''')
        connection.execute('INSERT INTO data_heads VALUES(?,?)', ('day', DATA))
        connection.execute('INSERT INTO data_events(event_id,version_id,kind,payload) '
            'VALUES(?,?,?,?)', (event['evidence_sha256'], DATA, event['kind'], json.dumps(event)))
    data = {'version_id': DATA, 'actual_end': '2026-10-27',
            'state': 'RESEARCH_READY_QUALIFIED_SUBSET'}
    reads = []
    def head(*, verify):
        reads.append(verify)
        return data
    monkeypatch.setattr(online, 'data_head', head)
    proofs = {}
    def add_model(character='b', *, staged=True, name='Alpha158 + LightGBM'):
        identity = character*64
        definition = signed({**AUTHORITY, 'schema_version': 'qtrade.online-model.v1',
            'model_id': identity, 'model_run_id': character+'-run', 'feature_count': 158,
            'name': name, 'training_data_end': '2026-10-26',
            'training_completed_at': (NOW-timedelta(days=1)).isoformat(),
            'historical_pit_certified': False, 'fitted_restore_verified': True})
        contract = signed({**AUTHORITY, 'model_bundle_sha256': identity,
            'data_version_id': DATA, 'data_end': '2026-10-27',
            'model_reference_end': '2026-10-26',
            'generated_at': (NOW-timedelta(minutes=5)).isoformat(),
            'expected_execution_at': next_execution('2026-10-27', calendar).isoformat(),
            'forward_return_observed': False})
        proof = signed({**AUTHORITY, 'schema_version': 'qtrade.qlib-forward-validation.v1',
            'state': 'FORWARD_INPUT_VALIDATED_NOT_ACTIVATED', 'model_bundle_sha256': identity,
            'model_run_id': character+'-run', 'validation_id': character.upper().lower()*64,
            'contract': contract, 'fitted_processors_unchanged': True,
            'fit_methods_forbidden': True, 'model_activated': False, 'auto_predict_enabled': False})
        online.registry.register(definition, now=NOW-timedelta(hours=3))
        if staged:
            online.registry.record_validation(proof, now=NOW-timedelta(minutes=4))
        online.remember(online.root/'models'/(identity+'.json'), signed({**AUTHORITY,
            'model_id': identity, 'archive': 'archives/'+character, 'definition': definition}))
        online.remember(online.root/'validations'/(proof['validation_id']+'.json'), signed({
            **AUTHORITY, 'archive': 'validations/'+character,
            'manifest_sha256': proof['evidence_sha256']}))
        proofs[character] = proof
        return identity
    monkeypatch.setattr(adapter, 'verify_archive', lambda p: {'run_id': p.name+'-run'})
    monkeypatch.setattr(adapter, 'verify_bundle', lambda p, provider:
                        {'evidence_sha256': p.parent.name*64})
    monkeypatch.setattr(adapter, 'verify_validation', lambda p: proofs[p.name])
    monkeypatch.setattr(policy_module, 'verify_validation', lambda p: proofs[p.name])
    promotion = policy_module.ModelPromotionPolicy(online, calendar)
    def command(mode='ENABLED', *, ids=None, models=None, request='1'):
        body = {'request_id': request*32, 'expected_policy_id': promotion.current()['policy_id'],
            'actor': 'authorized-operator', 'explicit_human_authorization': True,
            'authorization': {'source': policy_module.AUTHORIZATION_SOURCE,
                              'quote': '都允许批准', 'scopes': policy_module.SCOPES},
            'policy': {'mode': mode, 'allowed_model_ids': ids if ids is not None else ['b'*64],
                       'allowed_models': models or []}}
        return {'job_id': request*32, 'kind': 'QLIB_MODEL_PROMOTION_POLICY',
                'command': body, 'live_trading_enabled': False}
    def configure(payload=None, **kwargs):
        return promotion.configure(seal(payload or command(**kwargs), SECRET), SECRET,
                                   now=clocks[0])
    return SimpleNamespace(online=online, promotion=promotion, calendar=calendar, add=add_model,
        event=event, proofs=proofs, data=data, clocks=clocks, reads=reads, command=command,
        configure=configure)


def approvals(runtime):
    with runtime.online.registry.connect() as connection:
        return [dict(row) for row in connection.execute('SELECT * FROM model_promotion_approvals')]


def test_policy_defaults_off_without_data_reads_or_activation(runtime):
    runtime.add()
    assert runtime.promotion.tick(now=NOW) == {'state': 'OFF'}
    assert runtime.reads == [] and approvals(runtime) == []
    assert runtime.online.registry.snapshot()['active_model'] is None


@pytest.mark.parametrize('damage', [
    'signature', 'human', 'source', 'quote', 'scope', 'wide', 'live'])
def test_human_signed_exact_scope_is_required(runtime, damage):
    value = runtime.command()
    if damage == 'human':
        value['command']['explicit_human_authorization'] = False
    elif damage in {'source', 'quote'}:
        value['command']['authorization'][damage] = 'unapproved'
    elif damage == 'scope':
        value['command']['authorization']['scopes'] = ['LIVE_TRADING']
    elif damage == 'wide':
        value['command']['policy']['allowed_models'] = ['arbitrary-code']
    elif damage == 'live':
        value['live_trading_enabled'] = True
    envelope = seal(value, SECRET)
    if damage == 'signature':
        envelope['command']['actor'] = 'forged'
    with pytest.raises(ValueError):
        runtime.promotion.configure(envelope, SECRET, now=NOW)
    assert runtime.promotion.current()['policy']['mode'] == 'OFF'


def test_config_cas_and_repeated_signed_command_are_immutable(runtime):
    value = runtime.command()
    first = runtime.configure(value)
    assert runtime.configure(value) == first
    stale = deepcopy(value)
    stale['job_id'] = stale['command']['request_id'] = '2'*32
    assert runtime.configure(stale)['state'] == 'REJECTED'
    changed = deepcopy(value)
    changed['command']['policy']['mode'] = 'OFF'
    with pytest.raises(ValueError, match='IMMUTABLE'):
        runtime.configure(changed)


def test_approved_unique_candidate_promotes_once_and_supplies_prediction_approval(runtime):
    runtime.add()
    runtime.configure()
    receipt = runtime.promotion.tick(now=NOW)
    assert receipt['state'] == 'APPLIED'
    registry = runtime.online.registry
    ledger = PredictionRegistry(registry)
    assert ledger.active()['model_id'] == 'b'*64
    before = registry.snapshot()
    assert runtime.promotion.tick(now=NOW)['state'] == 'NO_UNIQUE_ALLOWED_STAGED_CANDIDATE'
    assert registry.snapshot()['events'] == before['events']
    assert before['models'][0]['activation_count'] == 1
    row, = approvals(runtime)
    approval = json.loads(row['approval'])
    assert approval['policy_document']['authorization']['quote'] == '都允许批准'
    assert approval['performance_superiority_certified'] is False
    assert approval['data_event_id'] == runtime.event['evidence_sha256']
    assert row['state'] == 'APPLIED'


@pytest.mark.parametrize('mode', ['ids', 'family'])
def test_allowlists_are_bounded_and_ambiguous_candidates_never_rank_on_metrics(runtime, mode):
    runtime.add('b')
    runtime.add('c', name='Alpha158 + Ridge')
    if mode == 'ids':
        runtime.configure(ids=['b'*64])
        assert runtime.promotion.tick(now=NOW)['active_model'] == 'b'*64
    else:
        runtime.configure(ids=[], models=['lightgbm', 'linear'])
        assert runtime.promotion.tick(now=NOW)['state'] == 'NO_UNIQUE_ALLOWED_STAGED_CANDIDATE'
        assert approvals(runtime) == []


@pytest.mark.parametrize('state', ['TRAINED', 'FAILED', 'RETIRED'])
def test_nonstaged_terminal_and_pending_candidates_cannot_be_revived(runtime, state):
    runtime.add(staged=False)
    if state == 'FAILED':
        runtime.online.registry.fail_candidate('b'*64, 'FAILED_VALIDATION', now=NOW)
    elif state == 'RETIRED':
        with runtime.online.registry.connect() as connection:
            connection.execute("UPDATE models SET state='RETIRED'")
    runtime.configure()
    assert runtime.promotion.tick(now=NOW)['state'] == 'NO_UNIQUE_ALLOWED_STAGED_CANDIDATE'
    assert approvals(runtime) == []


@pytest.mark.parametrize('damage', ['stale', 'unready', 'window', 'same_training', 'future',
    'wrong_end', 'reference', 'wrong_open', 'pre_epoch', 'no_proof', 'two_proofs', 'unsupported'])
def test_current_later_ready_input_and_next_open_validation_are_mandatory(runtime, damage):
    runtime.add(name='unknown' if damage == 'unsupported' else 'Alpha158 + LightGBM')
    runtime.configure()
    if damage == 'stale':
        runtime.data['actual_end'] = '2026-10-26'
    elif damage == 'unready':
        runtime.data['state'] = 'PREPARATION_ONLY'
    elif damage == 'window':
        runtime.clocks[0] = next_execution('2026-10-27', runtime.calendar)
    elif damage == 'same_training':
        with runtime.online.registry.connect() as c:
            row = c.execute('SELECT definition FROM models').fetchone()
            definition = json.loads(row[0])
            definition['training_data_end'] = '2026-10-27'
            c.execute('UPDATE models SET definition=?', (json.dumps(resign(definition)),))
    elif damage == 'no_proof':
        with runtime.online.registry.connect() as c:
            c.execute('DELETE FROM model_validations')
    else:
        proof = deepcopy(runtime.proofs['b'])
        if damage == 'two_proofs':
            proof['validation_id'] = 'c'*64
            runtime.online.registry.record_validation(resign(proof), now=NOW)
        elif damage != 'unsupported':
            key, value = {'future': ('generated_at', (NOW+timedelta(seconds=1)).isoformat()),
                'wrong_end': ('data_end', '2026-10-26'),
                'reference': ('model_reference_end', '2026-10-23'),
                'wrong_open': ('expected_execution_at', (NOW+timedelta(hours=1)).isoformat()),
                'pre_epoch': ('generated_at', (NOW-timedelta(hours=3)).isoformat())}[damage]
            proof['contract'][key] = value
            proof['contract'] = resign(proof['contract'])
            with runtime.online.registry.connect() as c:
                c.execute('UPDATE model_validations SET proof=?', (json.dumps(resign(proof)),))
    runtime.promotion.tick(now=runtime.clocks[0])
    assert runtime.online.registry.snapshot()['active_model'] is None


def rollback_epoch(runtime, *, later=False):
    event = signed({**AUTHORITY, 'kind': 'QLIB_DATA_ROLLBACK', 'version_id': DATA,
        'previous_head': 'e'*64,
        'created_at': (NOW-timedelta(minutes=1) if later else NOW-timedelta(hours=1)).isoformat()})
    with sqlite3.connect(runtime.online.data_database) as c:
        c.execute('INSERT INTO data_events(event_id,version_id,kind,payload) VALUES(?,?,?,?)',
                  (event['evidence_sha256'], DATA, event['kind'], json.dumps(event)))
    return event


def test_rollback_to_same_version_requires_validation_after_new_epoch(runtime):
    runtime.add()
    runtime.configure()
    rollback_epoch(runtime, later=True)
    result = runtime.promotion.tick(now=NOW)
    assert result['state'] == 'CURRENT_VALIDATION_CLOCK_OR_TRAINING_CONTEXT_INVALID'
    assert runtime.online.registry.snapshot()['active_model'] is None


def test_epoch_aba_during_actual_archive_checks_rejects_activation_and_never_retries(runtime,
                                                                                  monkeypatch):
    runtime.add()
    runtime.configure()
    original = adapter.verify_archive
    def race(path):
        rollback_epoch(runtime, later=True)
        return original(path)
    monkeypatch.setattr(adapter, 'verify_archive', race)
    result = runtime.promotion.tick(now=NOW)
    assert result['state'] == 'REJECTED' and result['error_code'] == 'READY_DATA_EPOCH_CHANGED'
    assert runtime.online.registry.snapshot()['active_model'] is None
    before = runtime.online.registry.snapshot()['events']
    # The new epoch can become an explicit new context, but the same validation
    # predates its rollback and cannot be recycled into an automatic retry.
    assert runtime.promotion.tick(now=NOW)['state'] == (
        'CURRENT_VALIDATION_CLOCK_OR_TRAINING_CONTEXT_INVALID')
    assert runtime.online.registry.snapshot()['events'] == before


def test_corrupt_actual_validation_creates_one_terminal_rejected_approval(runtime, monkeypatch):
    runtime.add()
    runtime.configure()
    monkeypatch.setattr(policy_module, 'verify_validation', lambda p:
                        {**runtime.proofs['b'], 'score_count': 0})
    first = runtime.promotion.tick(now=NOW)
    assert first['state'] == 'REJECTED'
    monkeypatch.setattr(policy_module, 'verify_validation', lambda p:
                        pytest.fail('terminal rejection cannot retry artifact verification'))
    assert runtime.promotion.tick(now=NOW) == first
    runtime.configure(request='2')
    assert runtime.promotion.tick(now=NOW) == first
    assert len(approvals(runtime)) == 1


def test_claim_without_control_receipt_is_not_reissued_after_restart(runtime, monkeypatch):
    runtime.add()
    runtime.configure()
    monkeypatch.setattr(runtime.online, 'execute', lambda *args, **kw:
                        (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        runtime.promotion.tick(now=NOW)
    assert approvals(runtime)[0]['state'] == 'CLAIMED'
    runtime.promotion.recover(now=NOW)
    assert approvals(runtime)[0]['state'] == 'REJECTED'
    assert runtime.online.registry.snapshot()['active_model'] is None


def test_applied_control_receipt_is_reconciled_without_second_activation(runtime, monkeypatch):
    runtime.add()
    runtime.configure()
    finish = runtime.promotion._finish
    monkeypatch.setattr(runtime.promotion, '_finish', lambda *a:
                        (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        runtime.promotion.tick(now=NOW)
    monkeypatch.setattr(runtime.promotion, '_finish', finish)
    runtime.promotion.recover(now=NOW)
    assert approvals(runtime)[0]['state'] == 'APPLIED'
    assert runtime.online.registry.snapshot()['models'][0]['activation_count'] == 1


def test_policy_disabled_during_verification_cannot_authorize_activation(runtime, monkeypatch):
    runtime.add()
    runtime.configure()
    def disable(path):
        runtime.configure(mode='OFF', request='2')
        return runtime.proofs['b']
    monkeypatch.setattr(policy_module, 'verify_validation', disable)
    assert runtime.promotion.tick(now=NOW)['state'] == 'REJECTED'
    assert runtime.online.registry.snapshot()['active_model'] is None


def test_event_failure_rolls_back_approval_before_any_control_action(runtime):
    runtime.add()
    runtime.configure()
    with runtime.online.registry.connect() as c:
        c.execute("CREATE TRIGGER reject_policy BEFORE INSERT ON model_events "
                  "WHEN NEW.kind='PROMOTION_APPROVED' BEGIN SELECT RAISE(ABORT,'test'); END")
    assert runtime.promotion.tick(now=NOW)['state'] == 'BLOCKED_PROMOTION_VERIFICATION'
    assert approvals(runtime) == []
    assert runtime.online.registry.snapshot()['active_model'] is None


@pytest.mark.parametrize('race', ['active', 'data_head'])
def test_final_control_cas_rejects_active_or_data_head_change(runtime, monkeypatch, race):
    runtime.add('b')
    runtime.add('c')
    runtime.configure()
    original = adapter.verify_archive
    def changed(path):
        if race == 'active':
            runtime.online.registry.apply({'request_id': 'e'*32, 'operation': 'ACTIVATE',
                'model_id': 'c'*64, 'expected_active': None, 'data_version_id': DATA,
                'validation_id': 'c'*64, 'actor': 'separate-operator'},
                current_data_version=DATA, now=NOW)
        else:
            with sqlite3.connect(runtime.online.data_database) as connection:
                connection.execute('UPDATE data_heads SET version_id=?', ('e'*64,))
        return original(path)
    monkeypatch.setattr(adapter, 'verify_archive', changed)
    result = runtime.promotion.tick(now=NOW)
    assert result['state'] == 'REJECTED'
    assert ('ACTIVE_MODEL_CHANGED' if race == 'active' else 'READY_DATA_CHANGED') in (
        result['error_code'])
    assert runtime.online.registry.snapshot()['active_model'] == ('c'*64 if race == 'active'
                                                                 else None)


def test_policy_does_not_withdraw_existing_active_prediction_approval(runtime, monkeypatch):
    runtime.add('b')
    runtime.configure()
    first = runtime.promotion.tick(now=NOW)
    runtime.configure(mode='OFF', request='2')
    assert runtime.promotion.tick(now=NOW) == {'state': 'OFF'}
    assert PredictionRegistry(runtime.online.registry).active() == first
    runtime.add('c')
    runtime.configure(ids=['c'*64], request='3')
    monkeypatch.setattr(runtime.online, 'data_head', lambda **kw:
                        (_ for _ in ()).throw(OSError('fixture storage failure')))
    assert runtime.promotion.tick(now=NOW)['state'] == 'BLOCKED_PROMOTION_VERIFICATION'
    assert PredictionRegistry(runtime.online.registry).active() == first


def test_manual_legacy_control_requires_no_data_event_table(runtime):
    runtime.add('b')
    with sqlite3.connect(runtime.online.data_database) as connection:
        connection.execute('DROP TABLE data_events')
    result = runtime.online.execute({'request_id': 'e'*32, 'operation': 'ACTIVATE',
        'model_id': 'b'*64, 'expected_active': None, 'data_version_id': DATA,
        'validation_id': 'b'*64, 'actor': 'legacy-operator'}, now=NOW)
    assert result['state'] == 'APPLIED'


def test_worker_ingress_requires_signed_request_and_keeps_rolling_off(worker, monkeypatch):
    value, queue = worker
    class Clock(datetime):
        @classmethod
        def now(cls, tz=UTC):
            return NOW
    monkeypatch.setattr(worker_module, 'datetime', Clock)
    command = {'request_id': 'f'*32,
        'expected_policy_id': value.promotion.current()['policy_id'],
        'actor': 'authorized-human', 'explicit_human_authorization': True,
        'authorization': {'source': policy_module.AUTHORIZATION_SOURCE,
                          'quote': '都允许批准', 'scopes': policy_module.SCOPES},
        'policy': {'mode': 'ENABLED', 'allowed_model_ids': ['b'*64], 'allowed_models': []}}
    payload = {'job_id': 'f'*32, 'kind': 'QLIB_MODEL_PROMOTION_POLICY',
               'command': command, 'live_trading_enabled': False}
    queue.read = lambda: {'requests/'+'f'*32: payload}
    value.ingest()
    assert value.promotion.current()['policy']['mode'] == 'OFF'
    sealed = seal(payload, value.secret)
    queue.read = lambda: {'cancellations/'+'f'*32: sealed}
    value.ingest()
    assert value.promotion.current()['policy']['mode'] == 'OFF'
    queue.read = lambda: {'requests/'+'f'*32: sealed}
    value.ingest()
    assert value.promotion.current()['policy']['mode'] == 'ENABLED'
    assert value.rolling.current()['policy']['mode'] == 'OFF'


@pytest.mark.parametrize('busy', ['stop', 'child', 'job'])
def test_worker_promotion_yields_to_stop_and_existing_work(worker, monkeypatch, busy):
    value, _ = worker
    monkeypatch.setattr(value.promotion, 'tick', lambda **kw: pytest.fail('must yield'))
    if busy == 'stop':
        value.stop = True
    elif busy == 'child':
        value.predictor.child = object()
    else:
        with value.db:
            value.db.execute('INSERT INTO jobs VALUES(?,?,?,?,NULL,NULL)',
                             ('f'*32, '{}', 'QUEUED', NOW.isoformat()))
    value.schedule_model_promotion()


def test_live_flag_cannot_be_enabled_by_policy(runtime, monkeypatch):
    runtime.configure()
    monkeypatch.setenv('LIVE_TRADING_ENABLED', 'true')
    with pytest.raises(ValueError, match='RESEARCH_ONLY'):
        runtime.promotion.tick(now=NOW)
