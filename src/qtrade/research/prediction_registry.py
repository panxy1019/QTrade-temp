"""Durable research prediction claims bound to explicit model approval epochs."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.model_registry import ModelRegistry, checked_document, checked_identity


def active_approval(connection: sqlite3.Connection) -> dict[str, Any] | None:
    row = connection.execute('SELECT m.model_id,m.state FROM active_model a '
                             'JOIN models m ON a.model_id=m.model_id '
                             'WHERE a.singleton=1').fetchone()
    if row is None:
        return None
    if row['state'] != 'ACTIVE':
        raise ValueError('PREDICTION_ACTIVE_MODEL_STATE_INCONSISTENT')
    event = connection.execute("SELECT payload FROM model_events WHERE model_id=? "
                               "AND kind='CONTROL_APPLIED' ORDER BY sequence DESC LIMIT 1",
                               (row['model_id'],)).fetchone()
    if event is None:
        raise ValueError('PREDICTION_EXPLICIT_APPROVAL_REQUIRED')
    value = json.loads(event['payload'])['details']
    checked_document(value)
    if (value['state'] != 'APPLIED' or value['model_id'] != row['model_id']
            or value['active_model'] != row['model_id']):
        raise ValueError('PREDICTION_APPROVAL_RECEIPT_INCONSISTENT')
    result: dict[str, Any] = value
    return result


def checked_bindings(value: dict[str, Any]) -> dict[str, Any]:
    if set(value) != {'model_id', 'approval_sha256', 'data_version_id', 'data_event_id',
                      'observation_universe_version', 'code_revision', 'data_end'}:
        raise ValueError('PREDICTION_BINDING_FIELDS_INVALID')
    for key in ('model_id', 'approval_sha256', 'data_version_id', 'data_event_id',
                'observation_universe_version'):
        checked_identity(value[key])
    if re.fullmatch('[a-f0-9]{40}', value['code_revision']) is None:
        raise ValueError('PREDICTION_CODE_REVISION_REQUIRED')
    date.fromisoformat(value['data_end'])
    return value


class PredictionRegistry:
    """Shares the single model worker's database and atomic approval transaction."""

    def __init__(self, models: ModelRegistry):
        self.models = models
        with models.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS prediction_tasks (
                    prediction_id TEXT PRIMARY KEY, request TEXT NOT NULL,
                    state TEXT NOT NULL, updated_at TEXT NOT NULL,
                    result TEXT, error_code TEXT
                );
                CREATE TABLE IF NOT EXISTS prediction_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    prediction_id TEXT NOT NULL, payload TEXT NOT NULL
                );
            ''')

    def active(self) -> dict[str, Any] | None:
        with self.models.connect() as c:
            return active_approval(c)

    @staticmethod
    def event(c: sqlite3.Connection, identity: str, state: str, now: datetime,
              details: dict[str, Any]) -> None:
        if now.tzinfo is None:
            raise ValueError('PREDICTION_EVENT_TIMEZONE_REQUIRED')
        payload = signed({**AUTHORITY, 'prediction_id': identity, 'state': state,
                          'at': now.isoformat(), 'details': details})
        c.execute('INSERT INTO prediction_events(prediction_id,payload) VALUES(?,?)',
                  (identity, json.dumps(payload, sort_keys=True)))

    def claim(self, bindings: dict[str, Any], *, now: datetime) -> dict[str, Any]:
        checked_bindings(bindings)
        if now.tzinfo is None:
            raise ValueError('PREDICTION_CLAIM_TIMEZONE_REQUIRED')
        identity = signed(bindings)['evidence_sha256']
        with self.models.connect() as c:
            old = c.execute('SELECT request FROM prediction_tasks WHERE prediction_id=?',
                            (identity,)).fetchone()
            if old:
                result: dict[str, Any] = json.loads(old['request'])
                return result
            approval = active_approval(c)
            if (approval is None or approval['model_id'] != bindings['model_id']
                    or approval['evidence_sha256'] != bindings['approval_sha256']
                    or datetime.fromisoformat(approval['at']) > now):
                raise ValueError('PREDICTION_CURRENT_APPROVAL_REQUIRED')
            request = signed({**AUTHORITY, 'schema_version': 'qtrade.daily-prediction-request.v1',
                              'prediction_id': identity, 'bindings': bindings,
                              'claimed_at': now.isoformat(), 'approval': approval})
            c.execute('INSERT INTO prediction_tasks VALUES(?,?,?,?,?,?)',
                      (identity, json.dumps(request, sort_keys=True), 'QUEUED',
                       now.isoformat(), None, None))
            self.event(c, identity, 'QUEUED', now, {'approval_sha256': bindings['approval_sha256']})
            return request

    def start(self, identity: str, *, now: datetime) -> bool:
        checked_identity(identity)
        with self.models.connect() as c:
            row = c.execute('SELECT state,request FROM prediction_tasks WHERE prediction_id=?',
                            (identity,)).fetchone()
            if row is None or row['state'] != 'QUEUED':
                return False
            request = json.loads(row['request'])
            approval = active_approval(c)
            if (approval is None
                    or approval['evidence_sha256'] != request['bindings']['approval_sha256']):
                c.execute('UPDATE prediction_tasks SET state=?,updated_at=?,error_code=? '
                          'WHERE prediction_id=?', ('SUPERSEDED', now.isoformat(),
                                                    'MODEL_APPROVAL_CHANGED', identity))
                self.event(c, identity, 'SUPERSEDED', now, {'reason': 'MODEL_APPROVAL_CHANGED'})
                return False
            c.execute('UPDATE prediction_tasks SET state=?,updated_at=? WHERE prediction_id=?',
                      ('RUNNING', now.isoformat(), identity))
            self.event(c, identity, 'RUNNING', now, {})
            return True

    def finish(self, proof: dict[str, Any], *, current_data_version: str,
               now: datetime) -> str:
        # The worker verifies all immutable prediction files before this transaction,
        # while holding the short data-head lease. No computation happens in this lock.
        checked_document(proof)
        identity = checked_identity(proof['prediction_id'])
        checked_identity(current_data_version)
        with self.models.connect() as c:
            row = c.execute('SELECT * FROM prediction_tasks WHERE prediction_id=?',
                            (identity,)).fetchone()
            if row is None:
                raise ValueError('PREDICTION_MUST_BE_CLAIMED_FIRST')
            raw = json.dumps(proof, sort_keys=True)
            if row['result'] is not None:
                if row['result'] != raw:
                    raise ValueError('IMMUTABLE_PREDICTION_RESULT_CONFLICT')
                return str(row['state'])
            if row['state'] not in {'RUNNING', 'INTERRUPTED'}:
                raise ValueError('PREDICTION_STATE_NOT_COMPLETABLE')
            request = json.loads(row['request'])
            bindings = request['bindings']
            contract = proof['contract']
            checked_document(contract)
            clocks = [datetime.fromisoformat(v) for v in (
                request['approval']['at'], request['claimed_at'], contract['generated_at'],
                contract['expected_execution_at'])]
            if (proof['schema_version'] != 'qtrade.daily-prediction.v1'
                    or proof['request_sha256'] != request['evidence_sha256']
                    or proof['bindings'] != bindings
                    or contract['data_version_id'] != bindings['data_version_id']
                    or contract['model_bundle_sha256'] != bindings['model_id']
                    or contract['data_end'] != bindings['data_end']
                    or contract['observation_universe_version'] != bindings[
                        'observation_universe_version']
                    or contract['forward_return_observed'] is not False
                    or proof['state'] != 'SEALED_ACTIVE_MODEL_PREDICTION'
                    or proof['auto_predict_enabled'] is not True
                    or proof['feature_count'] != 158 or not 1 <= proof['score_count'] <= 6000
                    or any(v.tzinfo is None for v in clocks) or now.tzinfo is None
                    or not clocks[0] <= clocks[1] <= clocks[2] < clocks[3]
                    or clocks[2] > now):
                raise ValueError('PREDICTION_RESULT_BINDING_OR_CLOCK_INVALID')
            approval = active_approval(c)
            reason = None
            if approval is None or approval['evidence_sha256'] != bindings['approval_sha256']:
                reason = 'MODEL_APPROVAL_CHANGED'
            elif current_data_version != bindings['data_version_id']:
                reason = 'QUALIFIED_DATA_HEAD_CHANGED'
            elif now >= clocks[3]:
                reason = 'EXPECTED_EXECUTION_PASSED'
            state = 'SUPERSEDED' if reason else 'COMPLETE'
            c.execute('UPDATE prediction_tasks SET state=?,updated_at=?,result=?,error_code=? '
                      'WHERE prediction_id=?', (state, now.isoformat(), raw, reason, identity))
            self.event(c, identity, state, now, {'result_sha256': proof['evidence_sha256'],
                                               'reason': reason})
            return state

    def fail(self, identity: str, reason: str, *, now: datetime,
             interrupted: bool = False) -> None:
        checked_identity(identity)
        if re.fullmatch('[A-Z_]{1,100}', reason) is None:
            raise ValueError('SAFE_PREDICTION_ERROR_CODE_REQUIRED')
        state = 'INTERRUPTED' if interrupted else 'FAILED'
        with self.models.connect() as c:
            changed = c.execute('UPDATE prediction_tasks SET state=?,updated_at=?,error_code=? '
                                "WHERE prediction_id=? AND state IN "
                                "('QUEUED','RUNNING','INTERRUPTED') AND state!=?",
                                (state, now.isoformat(), reason, identity, state))
            if changed.rowcount:
                self.event(c, identity, state, now, {'reason': reason})

    def tasks(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200:
            raise ValueError('BOUNDED_PREDICTION_QUERY_REQUIRED')
        with self.models.connect() as c:
            return [{'prediction_id': r['prediction_id'], 'request': json.loads(r['request']),
                     'state': r['state'], 'updated_at': r['updated_at'],
                     'result': json.loads(r['result']) if r['result'] else None,
                     'error_code': r['error_code']} for r in c.execute(
                         'SELECT * FROM prediction_tasks ORDER BY rowid DESC LIMIT ?', (limit,))]

    def state(self, identity: str) -> str:
        checked_identity(identity)
        with self.models.connect() as c:
            row = c.execute('SELECT state FROM prediction_tasks WHERE prediction_id=?',
                            (identity,)).fetchone()
            if row is None:
                raise ValueError('PREDICTION_TASK_NOT_FOUND')
            return str(row['state'])
