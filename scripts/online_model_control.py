"""Archive-backed model control adapter for the existing supervised research worker."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_versions import verify_version
from qtrade.research.forward_prediction import verify_validation
from qtrade.research.model_registry import ModelRegistry, checked_identity, control_command
from qtrade.research.qlib_publication import verify_archive
from scripts.atomic_json import write_json_immutable
from scripts.prepare_qlib_incremental import load_signed
from scripts.qlib_restore_bundle import verify_bundle

REPO = Path(__file__).resolve().parents[1]


class OnlineModels:
    def __init__(self, base: Path, database: Path, data_database: Path):
        self.base = base.resolve()
        self.root = self.base/'online-research/control'
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry = ModelRegistry(database)
        self.data_database = data_database

    def local(self, relative: str) -> Path:
        value = self.base/relative
        if (Path(relative).is_absolute() or value.is_symlink()
                or not value.resolve().is_relative_to(self.base)):
            raise ValueError('MODEL_ARTIFACT_PATH_OUTSIDE_LOCAL_ARCHIVE')
        return value

    def remember(self, path: Path, value: dict[str, Any]) -> None:
        if path.exists():
            if load_signed(path) != value:
                raise ValueError('IMMUTABLE_MODEL_ARTIFACT_INDEX_CONFLICT')
        else:
            write_json_immutable(path, value)

    def import_candidate(self, archive: Path, validation: Path, *, now: datetime) -> str:
        archive = self.local(archive.resolve().relative_to(self.base).as_posix())
        validation = self.local(validation.resolve().relative_to(self.base).as_posix())
        summary = verify_archive(archive)
        bundle = verify_bundle(archive/'online-restore', archive/'provider')
        restore = load_signed(archive/'independent-restore.json')
        proof = verify_validation(validation)
        identity = checked_identity(bundle['evidence_sha256'])
        if (restore['bundle_sha256'] != identity
                or restore['state'] != 'PARITY_VERIFIED_NOT_ACTIVATED'
                or restore['fitted_processors_unchanged'] is not True
                or proof['model_bundle_sha256'] != identity
                or proof['model_run_id'] != summary['run_id']):
            raise ValueError('MODEL_CANDIDATE_PROOF_IDENTITY_CHANGED')
        definition = signed({
            **AUTHORITY, 'schema_version': 'qtrade.online-model.v1', 'model_id': identity,
            'model_run_id': summary['run_id'], 'name': summary['model'],
            'feature_count': summary['feature_count'], 'training_data_end': summary['data_end'],
            'training_data_sha256': summary['dataset_sha256'],
            'training_code_revision': summary['source_revision'],
            'training_completed_at': summary['completed_at'], 'segments': summary['segments'],
            'portfolio': summary['portfolio'], 'cost_assumptions': summary['cost_assumptions'],
            'metrics': summary['metrics'], 'limitations': summary['limitations'],
            'historical_pit_certified': False, 'fitted_restore_verified': True,
            'restore_proof_sha256': restore['evidence_sha256'],
            'source_snapshot_sha256': summary['source_snapshot_sha256'],
        })
        self.remember(self.root/'models'/(identity+'.json'), signed({
            **AUTHORITY, 'model_id': identity, 'archive': archive.relative_to(self.base).as_posix(),
            'definition': definition,
        }))
        self.remember(self.root/'validations'/(proof['validation_id']+'.json'), signed({
            **AUTHORITY, 'validation_id': proof['validation_id'], 'model_id': identity,
            'archive': validation.relative_to(self.base).as_posix(),
            'manifest_sha256': proof['evidence_sha256'],
        }))
        self.registry.register(definition, now=now)
        self.registry.record_validation(proof, now=now)
        return identity

    def data_head(self, *, verify: bool) -> dict[str, Any]:
        with sqlite3.connect(self.data_database.resolve().as_uri()+'?mode=ro', uri=True) as c:
            row = c.execute('SELECT v.version_id,v.manifest_sha256 FROM data_heads h '
                            'JOIN data_versions v ON h.version_id=v.version_id '
                            "WHERE h.frequency='day'").fetchone()
        if row is None:
            raise ValueError('NO_QUALIFIED_DATA_VERSION')
        identity = checked_identity(row[0])
        root = self.local('unified-data/qlib-versions/versions/'+identity)
        value = verify_version(root) if verify else load_signed(root/'version.json')
        if value['evidence_sha256'] != row[1] or value['version_id'] != identity:
            raise ValueError('DATA_HEAD_IDENTITY_CHANGED')
        return value

    def execute(self, command: dict[str, Any], *, now: datetime) -> dict[str, Any]:
        command = control_command(command)
        prior = self.registry.receipt(command)
        if prior is not None:
            return prior
        try:
            index = load_signed(self.root/'models'/(command['model_id']+'.json'))
            proof_index = load_signed(self.root/'validations'/(command['validation_id']+'.json'))
            archive = self.local(index['archive'])
            summary = verify_archive(archive)
            bundle = verify_bundle(archive/'online-restore', archive/'provider')
            proof = verify_validation(self.local(proof_index['archive']))
            if (bundle['evidence_sha256'] != command['model_id']
                    or summary['run_id'] != index['definition']['model_run_id']
                    or proof['evidence_sha256'] != proof_index['manifest_sha256']
                    or proof['model_bundle_sha256'] != command['model_id']):
                raise ValueError('MODEL_CONTROL_ARTIFACT_BINDING_CHANGED')
            data = self.data_head(verify=True)
            calendar = Calendar(REPO/'configs/observer_calendar_2026.json')
            if str(calendar.expected(now, 'daily').date()) != data['actual_end']:
                return self.registry.reject(command, 'READY_DATA_NOT_CURRENT', now=now)
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
            return self.registry.reject(command, 'MODEL_CONTROL_ARTIFACT_VALIDATION_FAILED',
                                        now=now)
        # Keep the data head stable for the short model transaction. Heavy file checks
        # happen before this lease, so admission is blocked only for the final commit.
        with sqlite3.connect(self.data_database, timeout=10) as connection:
            connection.execute('BEGIN IMMEDIATE')
            head = connection.execute('SELECT version_id FROM data_heads '
                                      "WHERE frequency='day'").fetchone()
            final_clock = datetime.now(UTC)
            if head is None or head[0] != data['version_id']:
                return self.registry.reject(command, 'READY_DATA_CHANGED_REFRESH_REQUIRED',
                                            now=final_clock)
            if str(calendar.expected(final_clock, 'daily').date()) != data['actual_end']:
                return self.registry.reject(command, 'READY_DATA_NOT_CURRENT', now=final_clock)
            return self.registry.apply(command, current_data_version=data['version_id'],
                                       now=final_clock)

    def publication(self, revision: str, *, now: datetime,
                    worker_state: str = 'IDLE') -> dict[str, Any]:
        state = self.registry.snapshot()
        data = None
        data_error = None
        try:
            version = self.data_head(verify=False)
            data = {k: version[k] for k in (
                'version_id', 'actual_end', 'state', 'created_at', 'blocked_symbols',
                'historical_pit_certified', 'external_execution_ready', 'evidence_sha256',
            )}
            data['current_symbols'] = len(version['current_symbols'])
            data['historical_symbols'] = len(version['historical_symbols'])
            data['observation_universe_version'] = version['bindings'][
                'observation_universe_version']
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
            data_error = 'QUALIFIED_DATA_HEAD_UNAVAILABLE'
        validations = []
        models = state['models'][-199:]
        if state['active_model'] and not any(r['definition']['model_id'] == state['active_model']
                                             for r in models):
            models = [r for r in state['models']
                      if r['definition']['model_id'] == state['active_model']] + models
        model_ids = {r['definition']['model_id'] for r in models}
        for proof in state['validations'][-200:]:
            if proof['model_bundle_sha256'] not in model_ids:
                continue
            validations.append({k: proof[k] for k in (
                'validation_id', 'model_run_id', 'model_bundle_sha256', 'state', 'score_count',
                'feature_count', 'max_reference_score_difference', 'evidence_sha256',
            )} | {'contract': {k: proof['contract'][k] for k in (
                'data_version_id', 'data_end', 'generated_at', 'expected_execution_at',
                'forward_return_observed',
            )}})
        return signed({**AUTHORITY, 'schema_version': 'qtrade.online-catalog.v1',
            'published_at': now.isoformat(), 'worker': {'heartbeat_at': now.isoformat(),
                'source_revision': revision,
                'state': 'MODEL_CONTROL_READY' if worker_state == 'IDLE' else worker_state},
            'data_version': data, 'data_error': data_error, 'active_model': state['active_model'],
            'models': models, 'validations': validations,
            'commands': state['commands'], 'events': state['events'],
            'auto_predict': {'enabled': False, 'state': 'EVENT_WORKER_INTEGRATION_PENDING'},
            'auto_train': {'mode': 'OFF', 'state': 'ROLLING_INTEGRATION_PENDING'}})
