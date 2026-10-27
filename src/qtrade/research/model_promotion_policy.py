"""Explicit human standing consent for bounded research-only model promotion.

Input validation is compatibility evidence, never evidence of superior returns.
The existing single-owner worker serializes policy commands and promotion ticks.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_epoch import current_epoch
from qtrade.research.forward_prediction import next_execution, verify_validation
from qtrade.research.model_registry import checked_document, checked_identity
from qtrade.research.workflow import MODELS
from qtrade.research.workflow_queue import unseal
from scripts.atomic_json import write_json_immutable
from scripts.prepare_qlib_incremental import load_signed

if TYPE_CHECKING:
    from scripts.online_model_control import OnlineModels

SCHEMA = 'qtrade.model-promotion-policy.v1'
AUTHORIZATION_SOURCE = 'USER_EXPLICIT_INSTRUCTION_2026-10-27'
SCOPES = ['ACTIVE_DAILY_RESEARCH_PREDICTION', 'VALIDATED_CANDIDATE_RESEARCH_ACTIVATION']


def default_policy() -> dict[str, Any]:
    return {'mode': 'OFF', 'allowed_model_ids': [], 'allowed_models': []}


def validate_policy(value: dict[str, Any]) -> None:
    if (set(value) != {'mode', 'allowed_model_ids', 'allowed_models'}
            or value['mode'] not in {'OFF', 'ENABLED'}
            or not isinstance(value['allowed_model_ids'], list)
            or not isinstance(value['allowed_models'], list)
            or len(value['allowed_model_ids']) > 100
            or value['allowed_model_ids'] != sorted(set(value['allowed_model_ids']))
            or value['allowed_models'] != sorted(set(value['allowed_models']))
            or not set(value['allowed_models']) <= set(MODELS)
            or value['mode'] == 'ENABLED' and not (
                value['allowed_model_ids'] or value['allowed_models'])):
        raise ValueError('PROMOTION_POLICY_SCOPE_INVALID')
    for identity in value['allowed_model_ids']:
        checked_identity(identity)


def _clock(value: str, now: datetime) -> datetime:
    clock = datetime.fromisoformat(value)
    if clock.tzinfo is None or not clock <= now:
        raise ValueError('PROMOTION_POLICY_CLOCK_INVALID')
    return clock


def _family(name: str) -> str | None:
    return {'Alpha158 + LightGBM': 'lightgbm', 'Alpha158 + Ridge': 'linear'}.get(name)


class ModelPromotionPolicy:
    def __init__(self, online: OnlineModels, calendar: Calendar):
        self.online, self.calendar = online, calendar
        self.paper_root = online.registry.database.parent/'forward-research'
        with online.registry.connect() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS model_promotion_policy (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    policy_id TEXT NOT NULL, document TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_promotion_commands (
                    request_id TEXT PRIMARY KEY, command TEXT NOT NULL, receipt TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_promotion_approvals (
                    attempt_id TEXT PRIMARY KEY, approval TEXT NOT NULL,
                    state TEXT NOT NULL, result TEXT
                );
            ''')
            document = signed({**AUTHORITY, 'schema_version': SCHEMA,
                               'policy': default_policy(), 'authorization': None})
            connection.execute('INSERT OR IGNORE INTO model_promotion_policy VALUES(1,?,?)',
                (document['evidence_sha256'], json.dumps(document, sort_keys=True)))

    def current(self) -> dict[str, Any]:
        with self.online.registry.connect() as connection:
            row = connection.execute('SELECT policy_id,document FROM model_promotion_policy '
                                     'WHERE singleton=1').fetchone()
        document = json.loads(row['document'])
        checked_document(document)
        if document['evidence_sha256'] != row['policy_id']:
            raise ValueError('PROMOTION_POLICY_DOCUMENT_CHANGED')
        validate_policy(document['policy'])
        return {'policy_id': row['policy_id'], **document}

    def configure(self, envelope: dict[str, Any], secret: str, *,
                  now: datetime) -> dict[str, Any]:
        """Only an authenticated human command can enable or replace this policy."""
        self._safe(now)
        value = unseal(envelope, secret)
        command = value['command']
        if (set(value) != {'job_id', 'kind', 'command', 'live_trading_enabled'}
                or value['kind'] != 'QLIB_MODEL_PROMOTION_POLICY'
                or value['live_trading_enabled'] is not False
                or set(command) != {'request_id', 'expected_policy_id', 'policy', 'actor',
                                    'explicit_human_authorization', 'authorization'}
                or value['job_id'] != command['request_id']
                or re.fullmatch('[a-f0-9]{32}', str(command['request_id'])) is None
                or re.fullmatch('[A-Za-z0-9._@-]{1,80}', str(command['actor'])) is None
                or command['explicit_human_authorization'] is not True):
            raise ValueError('PROMOTION_POLICY_HUMAN_COMMAND_REQUIRED')
        checked_identity(command['expected_policy_id'])
        validate_policy(command['policy'])
        authorization = command['authorization']
        if (set(authorization) != {'source', 'quote', 'scopes'}
                or authorization['source'] != AUTHORIZATION_SOURCE
                or authorization['quote'] != '都允许批准'
                or authorization['scopes'] != SCOPES
                or now.date() < date(2026, 10, 27)):
            raise ValueError('PROMOTION_POLICY_STANDING_AUTHORIZATION_REQUIRED')
        # Keep the actual authenticated envelope, including its signature, for
        # operator audit. No signing key is ever persisted in policy evidence.
        raw = json.dumps(envelope, sort_keys=True)
        with self.online.registry.connect() as connection:
            prior = connection.execute('SELECT command,receipt FROM model_promotion_commands '
                'WHERE request_id=?', (command['request_id'],)).fetchone()
            if prior:
                if prior['command'] != raw:
                    raise ValueError('IMMUTABLE_PROMOTION_POLICY_COMMAND_CONFLICT')
                return dict(json.loads(prior['receipt']))
            previous = connection.execute('SELECT policy_id FROM model_promotion_policy '
                                          'WHERE singleton=1').fetchone()['policy_id']
            accepted = previous == command['expected_policy_id']
            document = signed({**AUTHORITY, 'schema_version': SCHEMA,
                'policy': command['policy'], 'authorization': authorization,
                'actor': command['actor'], 'enabled_at': now.isoformat(),
                'authorization_envelope_sha256': signed(envelope)['evidence_sha256'],
                'command_sha256': signed(command)['evidence_sha256']})
            receipt = signed({**AUTHORITY, 'request_id': command['request_id'],
                'state': 'APPLIED' if accepted else 'REJECTED', 'at': now.isoformat(),
                'previous_policy_id': previous,
                'policy_id': document['evidence_sha256'] if accepted else previous,
                'command': command, 'authenticated_envelope': envelope,
                'authorization_verification': 'AUTHENTICATED_QUEUE_HMAC_SHA256',
                'error_code': None if accepted else 'POLICY_CHANGED'})
            if accepted:
                connection.execute('UPDATE model_promotion_policy SET policy_id=?,document=? '
                    'WHERE singleton=1', (document['evidence_sha256'],
                                         json.dumps(document, sort_keys=True)))
            connection.execute('INSERT INTO model_promotion_commands VALUES(?,?,?)',
                (command['request_id'], raw, json.dumps(receipt, sort_keys=True)))
            self.online.registry.event(connection, '0'*64, 'PROMOTION_POLICY_'+receipt['state'],
                                       now, receipt)
            return receipt

    @staticmethod
    def _safe(now: datetime) -> None:
        if (now.tzinfo is None
                or os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false'):
            raise ValueError('PROMOTION_RESEARCH_ONLY_CLOCK_REQUIRED')

    def reserve_uninitialized_paper(self, model: str, attempt: str) -> dict[str, Any]:
        """Reserve the new account without opening/recovering a PAPER runtime.

        The existing forward owner treats an existing directory as non-pristine.
        It therefore cannot reuse its process-wide initial-start actor for a new
        model. No old book, database, process or recovery receipt is modified.
        """
        config = {'strategy_id': 'approved_model_'+model, 'mode': 'PAPER',
                  'cash': '1000000', 'fee_version': 'forward-research-cost-v1'}
        identity = signed(config)['evidence_sha256']
        root = self.paper_root/identity
        marker = root/'research-promotion-reservation.json'
        document = signed({**AUTHORITY, 'kind': 'RESEARCH_MODEL_PAPER_INITIALIZATION_BLOCK',
            'book_id': identity, 'model_id': model, 'attempt_id': attempt,
            'config': config, 'paper_initialization_authorized': False,
            'paper_recovery_authorized': False})
        if self.paper_root.is_symlink() or root.is_symlink():
            raise ValueError('PROMOTION_PAPER_GUARD_PATH_INVALID')
        if marker.exists():
            if load_signed(marker) != document:
                raise ValueError('PROMOTION_PAPER_GUARD_CHANGED')
            return document
        self.paper_root.mkdir(parents=True, exist_ok=True)
        # Fail closed on an existing unrecognized directory. Do not create or
        # rewrite any old account's files to force a desired state.
        root.mkdir(exist_ok=False)
        write_json_immutable(marker, document)
        return document

    def _finish(self, attempt: str, result: dict[str, Any], now: datetime) -> dict[str, Any]:
        checked_document(result)
        with self.online.registry.connect() as connection:
            row = connection.execute('SELECT state,result FROM model_promotion_approvals '
                                     'WHERE attempt_id=?', (attempt,)).fetchone()
            if row['state'] != 'CLAIMED':
                return dict(json.loads(row['result']))
            connection.execute('UPDATE model_promotion_approvals SET state=?,result=? '
                'WHERE attempt_id=?',
                (result['state'], json.dumps(result, sort_keys=True), attempt))
            self.online.registry.event(connection, result['model_id'],
                                       'PROMOTION_'+result['state'], now, result)
        return result

    def recover(self, *, now: datetime) -> None:
        """An uncertain claimed action is never reissued, even after a policy edit."""
        with self.online.registry.connect() as connection:
            pending = connection.execute('SELECT attempt_id,approval FROM '
                "model_promotion_approvals WHERE state='CLAIMED'").fetchall()
        for row in pending:
            approval = json.loads(row['approval'])
            checked_document(approval)
            if approval['attempt_id'] != row['attempt_id']:
                raise ValueError('PROMOTION_APPROVAL_IDENTITY_CHANGED')
            result = self.online.registry.receipt(approval['command'])
            if result is None:
                result = self.online.registry.reject(approval['command'],
                    'PROMOTION_UNCERTAIN_REQUIRES_HUMAN_REVIEW', now=now)
            self._finish(row['attempt_id'], result, now)

    def tick(self, *, now: datetime) -> dict[str, Any]:
        self._safe(now)
        try:
            return self._tick(now=now)
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
            # A promotion failure does not withdraw an existing explicit ACTIVE
            # prediction approval. Claimed actions remain recoverable, not retryable.
            return {'state': 'BLOCKED_PROMOTION_VERIFICATION'}

    def _tick(self, *, now: datetime) -> dict[str, Any]:
        self.recover(now=now)
        current = self.current()
        if current['policy']['mode'] == 'OFF':
            return {'state': 'OFF'}
        _clock(current['enabled_at'], now)
        snapshot = self.online.registry.snapshot()
        allowed = current['policy']
        candidates = [row for row in snapshot['models'] if row['state'] == 'STAGED'
            and (row['definition']['model_id'] in allowed['allowed_model_ids']
                 or _family(row['definition'].get('name', '')) in allowed['allowed_models'])]
        if len(candidates) != 1:
            return {'state': 'NO_UNIQUE_ALLOWED_STAGED_CANDIDATE'}
        data = self.online.data_head(verify=True)
        if (data['state'] != 'RESEARCH_READY_QUALIFIED_SUBSET'
                or data['actual_end'] != self.calendar.expected(now, 'daily').date().isoformat()
                or not now < next_execution(data['actual_end'], self.calendar)):
            return {'state': 'BLOCKED_DATA_OR_EXECUTION_WINDOW'}
        epoch = current_epoch(self.online.data_database, data['version_id'])
        with sqlite3.connect(self.online.data_database.resolve().as_uri()+'?mode=ro',
                             uri=True) as connection:
            payload = connection.execute('SELECT payload FROM data_events WHERE event_id=?',
                                          (epoch,)).fetchone()
        event = json.loads(payload[0])
        admitted = _clock(event['admitted_at'] if event['kind'] == 'QLIB_DATA_READY'
                          else event['created_at'], now)
        candidate = candidates[0]
        definition = candidate['definition']
        checked_document(definition)
        model = definition['model_id']
        if (candidate['activation_count'] != 0
                or _family(definition.get('name', '')) not in MODELS
                or data['actual_end'] <= definition['training_data_end']):
            return {'state': 'CANDIDATE_NOT_LATER_UNACTIVATED_SUPPORTED_MODEL'}
        proofs = [p for p in snapshot['validations'] if p['model_bundle_sha256'] == model
                  and p['contract']['data_version_id'] == data['version_id']]
        if len(proofs) != 1:
            return {'state': 'NO_UNIQUE_CURRENT_VALIDATION'}
        proof = proofs[0]
        checked_document(proof)
        checked_document(proof['contract'])
        contract = proof['contract']
        generated = _clock(contract['generated_at'], now)
        registered = _clock(candidate['registered_at'], now)
        trained = _clock(definition['training_completed_at'], now)
        if (generated < max(admitted, registered, trained)
                or contract['data_end'] != data['actual_end']
                or contract['expected_execution_at'] != next_execution(
                    data['actual_end'], self.calendar).isoformat()
                or contract['model_reference_end'] != definition['training_data_end']
                or contract['forward_return_observed'] is not False):
            return {'state': 'CURRENT_VALIDATION_CLOCK_OR_TRAINING_CONTEXT_INVALID'}
        bindings = {'model_id': model, 'validation_id': proof['validation_id'],
                    'data_version_id': data['version_id'], 'data_event_id': epoch}
        attempt = signed(bindings)['evidence_sha256']
        command = {'request_id': attempt[:32], 'operation': 'ACTIVATE',
            'model_id': model, 'expected_active': snapshot['active_model'],
            'data_version_id': data['version_id'], 'validation_id': proof['validation_id'],
            'actor': 'standing-policy-'+current['policy_id'][:16]}
        approval = signed({**AUTHORITY, 'schema_version': 'qtrade.model-policy-approval.v1',
            'attempt_id': attempt, **bindings, 'policy_id': current['policy_id'],
            'policy_document': {k: v for k, v in current.items() if k != 'policy_id'},
            'command': command, 'at': now.isoformat(),
            'performance_superiority_certified': False,
            'limitations': ['INPUT_COMPATIBILITY_IS_NOT_PERFORMANCE_SUPERIORITY',
                            'RESEARCH_ONLY_NO_PAPER_RECOVERY_OR_EXECUTION_AUTHORITY']})
        with self.online.registry.connect() as connection:
            prior = connection.execute('SELECT result,state FROM model_promotion_approvals '
                                       'WHERE attempt_id=?', (attempt,)).fetchone()
            if prior:
                return dict(json.loads(prior['result']))
            connection.execute('INSERT INTO model_promotion_approvals VALUES(?,?,?,NULL)',
                (attempt, json.dumps(approval, sort_keys=True), 'CLAIMED'))
            self.online.registry.event(connection, model, 'PROMOTION_APPROVED', now, approval)
        try:
            model_index = load_signed(self.online.root/'models'/(model+'.json'))
            index = load_signed(self.online.root/'validations'/(proof['validation_id']+'.json'))
            actual = verify_validation(self.online.local(index['archive']))
            if (model_index['definition'] != definition or actual != proof
                    or actual['evidence_sha256'] != index['manifest_sha256']):
                raise ValueError('PROMOTION_VERIFIED_PROOF_CHANGED')
            if self.current()['policy_id'] != current['policy_id']:
                raise ValueError('PROMOTION_POLICY_CHANGED')
            self.reserve_uninitialized_paper(model, attempt)
            result = self.online.execute(command, now=now, expected_data_event_id=epoch)
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
            result = self.online.registry.reject(command,
                'PROMOTION_ARTIFACT_OR_POLICY_VERIFICATION_FAILED', now=now)
        return self._finish(attempt, result, now)
