"""Transactional research model lifecycle; explicit approval is not trading authority."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.persistence.sqlite import _claim_single_process_owner


def checked_identity(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r'[a-f0-9]{64}', value) is None:
        raise ValueError('MODEL_IDENTITY_INVALID')
    return value


def checked_document(value: dict[str, Any]) -> None:
    if (signed({k: v for k, v in value.items() if k != 'evidence_sha256'}) != value
            or any(value.get(k) != v for k, v in AUTHORITY.items())):
        raise ValueError('MODEL_DOCUMENT_HASH_OR_AUTHORITY_INVALID')


def control_command(value: dict[str, Any]) -> dict[str, Any]:
    if (set(value) != {'request_id', 'operation', 'model_id', 'expected_active',
                      'data_version_id', 'validation_id', 'actor'}
            or re.fullmatch(r'[a-f0-9]{32}', str(value['request_id'])) is None
            or value['operation'] not in {'ACTIVATE', 'ROLLBACK'}
            or not isinstance(value['actor'], str)
            or re.fullmatch(r'[A-Za-z0-9._@-]{1,80}', value['actor']) is None):
        raise ValueError('MODEL_CONTROL_COMMAND_INVALID')
    for key in ('model_id', 'data_version_id', 'validation_id'):
        checked_identity(value[key])
    if value['expected_active'] is not None:
        checked_identity(value['expected_active'])
    return dict(value)


class ModelRegistry:
    def __init__(self, database: Path):
        if os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
            raise RuntimeError('LIVE_TRADING_MUST_REMAIN_DISABLED')
        self.database = database.resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS process_owner (
                    singleton INTEGER PRIMARY KEY, process_id INTEGER,
                    owner_token TEXT, claimed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS models (
                    model_id TEXT PRIMARY KEY, definition TEXT NOT NULL,
                    state TEXT NOT NULL, registered_at TEXT NOT NULL,
                    activated_at TEXT, activation_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS model_validations (
                    validation_id TEXT PRIMARY KEY, model_id TEXT NOT NULL,
                    data_version_id TEXT NOT NULL, proof TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS active_model (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), model_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_commands (
                    request_id TEXT PRIMARY KEY, command TEXT NOT NULL, result TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, model_id TEXT NOT NULL,
                    at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL
                );
            ''')
        _claim_single_process_owner(self.database)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.database, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('PRAGMA synchronous=FULL')
            c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    @staticmethod
    def event(c: sqlite3.Connection, model: str, kind: str, now: datetime,
              details: dict[str, Any]) -> None:
        if now.tzinfo is None:
            raise ValueError('MODEL_EVENT_TIMEZONE_REQUIRED')
        value = signed({**AUTHORITY, 'model_id': model, 'kind': kind,
                        'at': now.isoformat(), 'details': details})
        c.execute('INSERT INTO model_events(model_id,at,kind,payload) VALUES(?,?,?,?)',
                  (model, now.isoformat(), kind, json.dumps(value, sort_keys=True)))

    def register(self, definition: dict[str, Any], *, now: datetime) -> None:
        checked_document(definition)
        identity = checked_identity(definition['model_id'])
        if (definition.get('schema_version') != 'qtrade.online-model.v1'
                or definition.get('feature_count') != 158
                or definition.get('historical_pit_certified') is not False
                or definition.get('fitted_restore_verified') is not True):
            raise ValueError('MODEL_DEFINITION_CONTRACT_INVALID')
        raw = json.dumps(definition, sort_keys=True)
        with self.connect() as c:
            prior = c.execute('SELECT definition FROM models WHERE model_id=?',
                              (identity,)).fetchone()
            if prior:
                if prior['definition'] != raw:
                    raise ValueError('IMMUTABLE_MODEL_DEFINITION_CONFLICT')
                return
            c.execute('INSERT INTO models(model_id,definition,state,registered_at) '
                      'VALUES(?,?,?,?)', (identity, raw, 'TRAINED', now.isoformat()))
            self.event(c, identity, 'TRAINED', now, {'definition_sha256':
                                                   definition['evidence_sha256']})

    def record_validation(self, proof: dict[str, Any], *, now: datetime) -> None:
        # The runtime adapter verifies actual archive files before this metadata transaction.
        checked_document(proof)
        checked_document(proof['contract'])
        model = checked_identity(proof['model_bundle_sha256'])
        validation = checked_identity(proof['validation_id'])
        data = checked_identity(proof['contract']['data_version_id'])
        if (proof.get('schema_version') != 'qtrade.qlib-forward-validation.v1'
                or proof['state'] != 'FORWARD_INPUT_VALIDATED_NOT_ACTIVATED'
                or proof['contract']['model_bundle_sha256'] != model
                or proof['fitted_processors_unchanged'] is not True
                or proof['fit_methods_forbidden'] is not True
                or proof['model_activated'] is not False
                or proof['auto_predict_enabled'] is not False):
            raise ValueError('MODEL_FORWARD_VALIDATION_CONTRACT_INVALID')
        raw = json.dumps(proof, sort_keys=True)
        with self.connect() as c:
            row = c.execute('SELECT state FROM models WHERE model_id=?', (model,)).fetchone()
            if row is None or row['state'] == 'FAILED':
                raise ValueError('MODEL_NOT_REGISTERED_OR_FAILED')
            prior = c.execute('SELECT proof FROM model_validations WHERE validation_id=?',
                              (validation,)).fetchone()
            if prior:
                if prior['proof'] != raw:
                    raise ValueError('IMMUTABLE_MODEL_VALIDATION_CONFLICT')
                return
            c.execute('INSERT INTO model_validations VALUES(?,?,?,?)',
                      (validation, model, data, raw))
            if row['state'] == 'TRAINED':
                for state in ('VALIDATING', 'VALIDATED', 'STAGED'):
                    c.execute('UPDATE models SET state=? WHERE model_id=?', (state, model))
                    self.event(c, model, state, now, {'imported_verified_validation': validation})
            else:
                self.event(c, model, 'INPUT_VALIDATED', now, {'validation_id': validation})

    def fail_candidate(self, model: str, error_code: str, *, now: datetime) -> None:
        checked_identity(model)
        if re.fullmatch(r'[A-Z_]{1,100}', error_code) is None:
            raise ValueError('SAFE_MODEL_FAILURE_CODE_REQUIRED')
        with self.connect() as c:
            row = c.execute('SELECT state FROM models WHERE model_id=?', (model,)).fetchone()
            if row is None or row['state'] not in {'TRAINED', 'VALIDATING'}:
                raise ValueError('ONLY_PENDING_CANDIDATE_CAN_FAIL')
            c.execute('UPDATE models SET state=? WHERE model_id=?', ('FAILED', model))
            self.event(c, model, 'FAILED', now, {'error_code': error_code})

    def receipt(self, command: dict[str, Any]) -> dict[str, Any] | None:
        command = control_command(command)
        with self.connect() as c:
            row = c.execute('SELECT command,result FROM model_commands WHERE request_id=?',
                            (command['request_id'],)).fetchone()
        if row is None:
            return None
        if row['command'] != json.dumps(command, sort_keys=True):
            raise ValueError('IMMUTABLE_MODEL_COMMAND_CONFLICT')
        result: dict[str, Any] = json.loads(row['result'])
        return result

    def reject(self, command: dict[str, Any], error: str, *, now: datetime) -> dict[str, Any]:
        command = control_command(command)
        if re.fullmatch(r'[A-Z_]{1,100}', error) is None or now.tzinfo is None:
            raise ValueError('SAFE_MODEL_REJECTION_REQUIRED')
        previous = self.receipt(command)
        if previous is not None:
            return previous
        with self.connect() as c:
            row = c.execute('SELECT model_id FROM active_model WHERE singleton=1').fetchone()
            active = row['model_id'] if row else None
            result = signed({**AUTHORITY, 'request_id': command['request_id'],
                'command_sha256': signed(command)['evidence_sha256'],
                'operation': command['operation'], 'state': 'REJECTED',
                'model_id': command['model_id'], 'previous_active': active,
                'active_model': active, 'error_code': error, 'actor': command['actor'],
                'at': now.isoformat(), 'data_version_id': command['data_version_id'],
                'validation_id': command['validation_id']})
            c.execute('INSERT INTO model_commands VALUES(?,?,?)',
                      (command['request_id'], json.dumps(command, sort_keys=True),
                       json.dumps(result, sort_keys=True)))
            self.event(c, command['model_id'], 'CONTROL_REJECTED', now, result)
            return result

    def apply(self, command: dict[str, Any], *, current_data_version: str,
              now: datetime) -> dict[str, Any]:
        command = control_command(command)
        checked_identity(current_data_version)
        if now.tzinfo is None:
            raise ValueError('MODEL_CONTROL_TIMEZONE_REQUIRED')
        raw = json.dumps(command, sort_keys=True)
        with self.connect() as c:
            prior = c.execute('SELECT command,result FROM model_commands WHERE request_id=?',
                              (command['request_id'],)).fetchone()
            if prior:
                if prior['command'] != raw:
                    raise ValueError('IMMUTABLE_MODEL_COMMAND_CONFLICT')
                receipt: dict[str, Any] = json.loads(prior['result'])
                return receipt  # Original receipt, not reactivation.
            head = c.execute('SELECT model_id FROM active_model WHERE singleton=1').fetchone()
            active = head['model_id'] if head else None
            target = c.execute('SELECT * FROM models WHERE model_id=?',
                               (command['model_id'],)).fetchone()
            validation = c.execute('SELECT * FROM model_validations WHERE validation_id=?',
                                   (command['validation_id'],)).fetchone()
            error = None
            if active != command['expected_active']:
                error = 'ACTIVE_MODEL_CHANGED_REFRESH_REQUIRED'
            elif current_data_version != command['data_version_id']:
                error = 'READY_DATA_CHANGED_REFRESH_REQUIRED'
            elif target is None or target['state'] != (
                    'STAGED' if command['operation'] == 'ACTIVATE' else 'RETIRED'):
                error = 'MODEL_STATE_NOT_ELIGIBLE'
            elif command['operation'] == 'ROLLBACK' and not target['activation_count']:
                error = 'MODEL_HAS_NEVER_BEEN_ACTIVE'
            elif (validation is None or validation['model_id'] != command['model_id']
                  or validation['data_version_id'] != current_data_version):
                error = 'CURRENT_INPUT_VALIDATION_REQUIRED'
            elif now.tzinfo is None:
                raise ValueError('MODEL_CONTROL_TIMEZONE_REQUIRED')
            else:
                proof = json.loads(validation['proof'])
                generated = datetime.fromisoformat(proof['contract']['generated_at'])
                execute = datetime.fromisoformat(proof['contract']['expected_execution_at'])
                if (generated.tzinfo is None or execute.tzinfo is None
                        or not generated <= now < execute):
                    error = 'CURRENT_INPUT_VALIDATION_TIME_EXPIRED'
            if error is None:
                if active is not None:
                    c.execute('UPDATE models SET state=? WHERE model_id=?', ('RETIRED', active))
                    self.event(c, active, 'RETIRED', now, {'replacement': command['model_id']})
                c.execute('UPDATE models SET state=?,activated_at=?,activation_count='
                          'activation_count+1 WHERE model_id=?',
                          ('ACTIVE', now.isoformat(), command['model_id']))
                c.execute('INSERT INTO active_model VALUES(1,?) ON CONFLICT(singleton) '
                          'DO UPDATE SET model_id=excluded.model_id', (command['model_id'],))
            result = signed({**AUTHORITY, 'request_id': command['request_id'],
                'command_sha256': signed(command)['evidence_sha256'],
                'operation': command['operation'], 'state': 'REJECTED' if error else 'APPLIED',
                'model_id': command['model_id'], 'previous_active': active,
                'active_model': active if error else command['model_id'], 'error_code': error,
                'actor': command['actor'], 'at': now.isoformat(),
                'data_version_id': command['data_version_id'],
                'validation_id': command['validation_id']})
            c.execute('INSERT INTO model_commands VALUES(?,?,?)',
                      (command['request_id'], raw, json.dumps(result, sort_keys=True)))
            self.event(c, command['model_id'], 'CONTROL_'+result['state'], now, result)
            return result

    def snapshot(self) -> dict[str, Any]:
        with self.connect() as c:
            row = c.execute('SELECT model_id FROM active_model WHERE singleton=1').fetchone()
            return signed({**AUTHORITY, 'schema_version': 'qtrade.model-registry.v1',
                'observed_at': datetime.now(UTC).isoformat(),
                'active_model': row['model_id'] if row else None,
                'models': [{'definition': json.loads(r['definition']), 'state': r['state'],
                            'registered_at': r['registered_at'], 'activated_at': r['activated_at'],
                            'activation_count': r['activation_count']}
                           for r in c.execute('SELECT * FROM models ORDER BY registered_at')],
                'validations': [json.loads(r['proof']) for r in c.execute(
                    'SELECT proof FROM model_validations ORDER BY rowid')],
                'commands': [json.loads(r['result']) for r in c.execute(
                    'SELECT result FROM model_commands ORDER BY rowid DESC LIMIT 100')],
                'events': [json.loads(r['payload']) for r in c.execute(
                    'SELECT payload FROM model_events ORDER BY sequence DESC LIMIT 200')]})
