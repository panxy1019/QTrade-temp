"""Rolling policy and task claims share the existing single-worker job transaction."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.model_registry import checked_identity
from qtrade.research.rolling_policy import default_policy, due, plan, validate_policy


def command(value: dict[str, Any]) -> dict[str, Any]:
    if (set(value) != {'request_id', 'expected_policy_id', 'policy', 'actor'}
            or re.fullmatch('[a-f0-9]{32}', str(value['request_id'])) is None
            or re.fullmatch('[A-Za-z0-9._@-]{1,80}', str(value['actor'])) is None):
        raise ValueError('ROLLING_CONTROL_COMMAND_INVALID')
    checked_identity(value['expected_policy_id'])
    validate_policy(value['policy'])
    return dict(value)


class RollingControl:
    def __init__(self, connection: sqlite3.Connection):
        self.db = connection
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS rolling_policy (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                policy_id TEXT NOT NULL, policy TEXT NOT NULL, last_attempt_day TEXT
            );
            CREATE TABLE IF NOT EXISTS rolling_commands (
                request_id TEXT PRIMARY KEY, command TEXT NOT NULL, receipt TEXT NOT NULL
            );
        ''')
        value = default_policy()
        self.db.execute('INSERT OR IGNORE INTO rolling_policy VALUES(1,?,?,NULL)',
                        (signed(value)['evidence_sha256'], json.dumps(value, sort_keys=True)))
        self.db.commit()

    def current(self) -> dict[str, Any]:
        row = self.db.execute('SELECT policy_id,policy,last_attempt_day FROM rolling_policy '
                              'WHERE singleton=1').fetchone()
        return {'policy_id': row[0], 'policy': json.loads(row[1]), 'last_attempt_day': row[2]}

    def apply(self, value: dict[str, Any], *, now: datetime) -> dict[str, Any]:
        value = command(value)
        if now.tzinfo is None:
            raise ValueError('ROLLING_CONTROL_CLOCK_REQUIRED')
        raw = json.dumps(value, sort_keys=True)
        with self.db:
            prior = self.db.execute('SELECT command,receipt FROM rolling_commands '
                                    'WHERE request_id=?', (value['request_id'],)).fetchone()
            if prior:
                if prior[0] != raw:
                    raise ValueError('IMMUTABLE_ROLLING_COMMAND_CONFLICT')
                return dict(json.loads(prior[1]))
            current = self.current()
            accepted = current['policy_id'] == value['expected_policy_id']
            new_id = signed(value)['evidence_sha256'] if accepted else current['policy_id']
            receipt = signed({**AUTHORITY, 'request_id': value['request_id'],
                'command_sha256': signed(value)['evidence_sha256'],
                'state': 'APPLIED' if accepted else 'REJECTED', 'policy_id': new_id,
                'previous_policy_id': current['policy_id'], 'actor': value['actor'],
                'at': now.isoformat(), 'policy': value['policy'],
                'error_code': None if accepted else 'POLICY_CHANGED_REFRESH_REQUIRED'})
            if accepted:
                # Preserve cadence for a no-op policy edit; changing the policy starts a new epoch.
                previous = (current['last_attempt_day']
                            if value['policy'] == current['policy'] else None)
                self.db.execute('UPDATE rolling_policy SET policy_id=?,policy=?,last_attempt_day=? '
                    'WHERE singleton=1',
                    (new_id, json.dumps(value['policy'], sort_keys=True), previous))
                # Policy changes never allow old queued jobs to execute under different consent.
                for identity, request in self.db.execute(
                        "SELECT id,request FROM jobs WHERE state='QUEUED'").fetchall():
                    if json.loads(request).get('kind') == 'QLIB_ROLLING_JOB':
                        self.db.execute("UPDATE jobs SET state='CANCELLED',updated=?,error=? "
                            'WHERE id=?', (now.isoformat(), 'ROLLING_POLICY_REPLACED', identity))
                        self.db.execute('INSERT INTO events VALUES(?,?,?)',
                                         (identity, now.isoformat(), 'CANCELLED'))
            self.db.execute('INSERT INTO rolling_commands VALUES(?,?,?)',
                            (value['request_id'], raw, json.dumps(receipt, sort_keys=True)))
        return receipt

    def schedule(self, calendar: list[str], data_end: str, version_id: str, revision: str,
                 *, now: datetime) -> str | None:
        if now.tzinfo is None:
            raise ValueError('ROLLING_SCHEDULE_CLOCK_REQUIRED')
        with self.db:
            current = self.current()
            if not due(current['policy'], calendar, data_end, current['last_attempt_day']):
                return None
            pending = self.db.execute("SELECT count(*) FROM jobs WHERE state NOT IN "
                "('COMPLETE','FAILED','CANCELLED','INTERRUPTED','REJECTED')").fetchone()[0]
            if pending:
                return None
            planned = plan(current['policy'], calendar, data_end,
                           data_version_id=version_id, source_revision=revision)
            bindings = {'policy_id': current['policy_id'], 'plan': planned}
            identity = signed(bindings)['evidence_sha256'][:32]
            request = signed({**AUTHORITY, 'kind': 'QLIB_ROLLING_JOB', 'job_id': identity,
                **bindings, 'name': 'rolling-alpha158-'+current['policy']['model'],
                'model': current['policy']['model'], 'created_at': now.isoformat()})
            self.db.execute('INSERT INTO jobs VALUES(?,?,?,?,NULL,NULL)',
                             (identity, json.dumps(request, sort_keys=True),
                              'QUEUED', now.isoformat()))
            self.db.execute('INSERT INTO events VALUES(?,?,?)',
                            (identity, now.isoformat(), 'QUEUED'))
            self.db.execute('UPDATE rolling_policy SET last_attempt_day=? WHERE singleton=1',
                             (data_end,))
        return str(identity)

    def publication(self) -> dict[str, Any]:
        current = self.current()
        receipts = [json.loads(row[0]) for row in self.db.execute(
            'SELECT receipt FROM rolling_commands ORDER BY rowid DESC LIMIT 50')]
        tasks: list[dict[str, Any]] = []
        validations: list[dict[str, Any]] = []
        for identity, raw, state, updated, result, error in self.db.execute(
                'SELECT id,request,state,updated,result,error FROM jobs ORDER BY rowid DESC'):
            request = json.loads(raw)
            if request.get('kind') == 'QLIB_MODEL_VALIDATION' and len(validations) < 50:
                validations.append({'job_id': identity, 'model_id': request['model_id'],
                    'data_version_id': request['data_version_id'], 'state': state,
                    'error_code': error, 'updated_at': updated})
            if request.get('kind') != 'QLIB_ROLLING_JOB':
                continue
            if len(tasks) >= 50:
                continue
            tasks.append({'job_id': identity, 'state': state, 'updated_at': updated,
                'error_code': error, 'plan': request['plan'],
                'result': json.loads(result) if result else None})
            if len(tasks) == 50 and len(validations) == 50:
                break
        return {'mode': current['policy']['mode'], 'state': 'CONTROL_READY', **current,
                'receipts': receipts, 'tasks': tasks, 'validation_tasks': validations,
                'automatic_activation': False}
