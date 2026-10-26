"""Read-only later-input candidate validation acceptance; never training or activation."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_versions import file_hash, verify_version
from qtrade.research.forward_prediction import (
    expected_codes,
    inference_contract,
    rank_scores,
    verify_validation,
)
from qtrade.research.model_registry import checked_document, checked_identity
from qtrade.research.prediction_registry import active_approval
from qtrade.research.qlib_publication import verify_archive, verify_signed
from qtrade.research.rolling_policy import verify_plan
from scripts.atomic_json import write_json_immutable
from scripts.qlib_restore_bundle import verify_bundle
from scripts.unified_store import UnifiedBatchStore

REPO = Path(__file__).resolve().parents[1]
MAX_ROWS = 10000


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError('CANDIDATE_AUDIT_'+code)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def checked(value: dict[str, Any]) -> None:
    checked_document(value)
    require(all(type(value.get(key)) is type(expected) for key, expected in AUTHORITY.items()),
            'AUTHORITY_TYPE_INVALID')


def document(path: Path, *, authority: bool = True) -> dict[str, Any]:
    require(not path.is_symlink() and path.is_file() and path.stat().st_size <= 8_000_000,
            'DOCUMENT_PATH_OR_BOUND_INVALID')
    value: dict[str, Any] = json.loads(path.read_bytes())
    verify_signed(value)
    if authority:
        checked(value)
    return value


def local(base: Path, relative: str) -> Path:
    path = base/relative
    require(not Path(relative).is_absolute() and not path.is_symlink()
            and path.resolve().is_relative_to(base.resolve()), 'ARCHIVE_PATH_ESCAPE')
    return path


def readonly(path: Path) -> sqlite3.Connection:
    require(not path.is_symlink() and path.is_file(), 'DATABASE_PATH_INVALID')
    connection = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    connection.execute('BEGIN')
    return connection


def rows(connection: sqlite3.Connection, sql: str,
         parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    result = connection.execute(sql+' LIMIT ?', (*parameters, MAX_ROWS+1)).fetchall()
    require(len(result) <= MAX_ROWS, 'LEDGER_SCAN_BOUND_EXCEEDED')
    return [dict(row) for row in result]


def snapshot(models: Path, workflow: Path, data: Path, model_id: str, trained: str,
             later: str) -> dict[str, Any]:
    """Materialize only required facts, then release WAL read snapshots immediately."""
    with closing(readonly(models)) as connection:
        approval = active_approval(connection)
        active = rows(connection, 'SELECT m.* FROM active_model a JOIN models m '
                      'ON a.model_id=m.model_id WHERE a.singleton=1')
        candidate = rows(connection, 'SELECT * FROM models WHERE model_id=?', (model_id,))
        validations = rows(connection,
            'SELECT * FROM model_validations WHERE model_id=? AND data_version_id=?',
            (model_id, later))
        events = rows(connection, 'SELECT * FROM model_events WHERE model_id IN (?,?) '
                      "OR kind='CONTROL_APPLIED' ORDER BY sequence",
                      (model_id, (approval or {}).get('model_id', '')))
        commands = rows(connection, 'SELECT * FROM model_commands WHERE request_id=?',
                         ((approval or {}).get('request_id', ''),))
    with closing(readonly(workflow)) as connection:
        jobs = rows(connection, 'SELECT * FROM jobs WHERE '
            "json_extract(request,'$.kind')='QLIB_MODEL_VALIDATION' AND "
            "json_extract(request,'$.model_id')=? AND "
            "json_extract(request,'$.data_version_id')=? ORDER BY id", (model_id, later))
        job_events = {row['id']: rows(connection,
            'SELECT rowid,* FROM events WHERE id=? ORDER BY rowid', (row['id'],))
            for row in jobs}
    with closing(readonly(data)) as connection:
        versions = rows(connection, 'SELECT * FROM data_versions WHERE version_id IN (?,?) '
                        'ORDER BY version_id', (trained, later))
        admissions = rows(connection, 'SELECT * FROM data_events WHERE version_id IN (?,?) '
                          'ORDER BY sequence', (trained, later))
        head = rows(connection, "SELECT * FROM data_heads WHERE frequency='day'")
    return {'approval': approval, 'active': active, 'candidate': candidate,
            'validations': validations,
            'model_events': events, 'commands': commands, 'jobs': jobs,
            'job_events': job_events,
            'versions': versions, 'data_events': admissions, 'data_head': head}


def clock(value: str, now: datetime) -> datetime:
    stamp = datetime.fromisoformat(value)
    require(stamp.tzinfo is not None and stamp <= now, 'FUTURE_OR_NAIVE_CLOCK')
    return stamp


def job_chain(job: dict[str, Any], facts: dict[str, Any],
              now: datetime) -> tuple[datetime, datetime]:
    require(job['state'] == 'COMPLETE' and job['error'] is None, 'JOB_NOT_COMPLETE')
    events = facts['job_events'][job['id']]
    require([row['state'] for row in events] == ['QUEUED', 'RUNNING', 'COMPLETE'],
            'JOB_EVENT_CHAIN_INVALID')
    times = [clock(row['at'], now) for row in events]
    require(times == sorted(times) and times[-1] <= clock(job['updated'], now),
            'JOB_EVENT_CLOCK_INVALID')
    return times[0], times[-1]


def raw_day(base: Path, root: Path, version: dict[str, Any], calendar: Calendar,
            now: datetime) -> dict[str, Any]:
    """Reuse the existing read-only raw day/normalized Parquet replay, with no book."""
    request = document(root/'preparation-request.json')
    require(request['evidence_sha256'] == version['bindings']['preparation_sha256']
            and request['day'] == version['actual_end'], 'PREPARATION_BINDING_INVALID')
    symbols = version['current_symbols']
    identities = sorted({request['batches'][symbol] for symbol in symbols})
    require(all(isinstance(identity, str) and re.fullmatch('[a-f0-9]{32}', identity)
                for identity in identities), 'COLLECTION_BATCH_ID_INVALID')
    store = UnifiedBatchStore(base/'unified-data/lake')  # Constructor only resolves a path.
    receipts = {identity: store.load(identity) for identity in identities}
    for symbol in symbols:
        partition = document(root/'stocks'/symbol/'manifest.json', authority=False)
        receipt = receipts[request['batches'][symbol]]
        require(partition['batch_sha256'] == receipt['evidence_sha256']
                and receipt['spec']['universe_version'] == request['universe_version'],
                'RAW_PARTITION_RECEIPT_MISMATCH')
    adapter = import_module('scripts.qlib_forward_paper_adapter')
    market, evidence = adapter.observed_session(store.root, identities,
        day=datetime.fromisoformat(version['actual_end']).date(),
        instruments=[('SH' if symbol.startswith('6') else 'SZ')+symbol for symbol in symbols],
        calendar=calendar, now=now)
    require(len(market.securities) == len(symbols)
            and market.observed_at <= clock(version['created_at'], now),
            'RAW_SOURCE_SCOPE_OR_VERSION_CLOCK_INVALID')
    return {'evidence_sha256': evidence['evidence_sha256'], 'receipts': evidence['receipts'],
            'observed_at': market.observed_at.isoformat(), 'symbols': len(symbols)}


def validation_rows(root: Path, data_root: Path, proof: dict[str, Any], bundle: dict[str, Any],
                    current: dict[str, Any]) -> None:
    codes = expected_codes((data_root/'provider/instruments/workflow_supported.txt').read_text(
        ).splitlines(), current['incremental']['current_symbols'], current['end'])
    scores = pq.read_table(root/'scores.parquet')
    require(scores.column_names == ['instrument', 'score', 'rank']
            and scores.num_rows == proof['score_count'], 'SCORES_SCHEMA_OR_COUNT_INVALID')
    records = scores.to_pylist()
    require(rank_scores([{'instrument': row['instrument'], 'score': row['score']}
                         for row in records], codes) == records, 'SCORES_COVERAGE_OR_RANK_INVALID')
    features = pq.read_table(root/'features.parquet')
    require(features.num_rows == len(codes)
            and features.column_names == [*bundle['feature_order'], 'datetime', 'instrument']
            and all(field.type == pa.float64() for field in list(features.schema)[:158]),
            'FEATURES_SCHEMA_OR_COUNT_INVALID')
    index = features.select(['datetime', 'instrument']).to_pylist()
    require(sorted(row['instrument'] for row in index) == codes
            and all(row['datetime'].date().isoformat() == current['end'] for row in index),
            'FEATURES_SESSION_OR_INSTRUMENT_INVALID')


def verify(base: Path, models: Path, workflow: Path, data: Path, model_id: str,
           later_id: str, expected_active: str, expected_approval: str, calendar: Calendar,
           now: datetime, evidence: dict[str, Any], checks: dict[str, bool]) -> None:
    index = document(base/'online-research/control/models'/(model_id+'.json'))
    require(index['model_id'] == model_id, 'MODEL_INDEX_IDENTITY_INVALID')
    archive = local(base, index['archive'])
    # Read just enough to choose short SQLite snapshots; full verification follows.
    header = document(archive/'summary.json')
    trained_id = checked_identity(header['rolling_plan']['data_version_id'])
    facts = snapshot(models, workflow, data, model_id, trained_id, later_id)
    evidence['snapshot_sha256'] = signed(facts)['evidence_sha256']
    evidence['observed_data_head'] = (facts['data_head'][0]['version_id']
                                      if len(facts['data_head']) == 1 else None)
    require(len(facts['candidate']) == 1, 'CANDIDATE_NOT_REGISTERED')
    candidate = facts['candidate'][0]
    evidence.update(candidate_state=candidate['state'], registered_at=candidate['registered_at'],
                    activation_count=candidate['activation_count'])
    require(candidate['state'] == 'STAGED' and candidate['activation_count'] == 0
            and candidate['activated_at'] is None, 'CANDIDATE_NOT_UNACTIVATED_STAGED')
    definition = json.loads(candidate['definition'])
    checked(definition)
    require(canonical(definition) == canonical(index['definition']), 'MODEL_DEFINITION_CHANGED')
    approval = facts['approval']
    require(approval is not None and approval['model_id'] == expected_active
            and approval['evidence_sha256'] == expected_approval and expected_active != model_id,
            'ACTIVE_APPROVAL_CHANGED')
    checked(approval)
    require(len(facts['active']) == 1, 'ACTIVE_MODEL_ROW_MISSING')
    active_row = facts['active'][0]
    active_definition = json.loads(active_row['definition'])
    checked(active_definition)
    require(active_row['model_id'] == expected_active
            and active_definition['model_id'] == expected_active
            and active_definition['schema_version'] == 'qtrade.online-model.v1'
            and active_definition['fitted_restore_verified'] is True
            and active_definition['historical_pit_certified'] is False
            and active_row['activation_count'] >= 1
            and active_row['activated_at'] == approval['at'], 'ACTIVE_MODEL_IDENTITY_INVALID')
    active_training = [row for row in facts['model_events']
                       if row['model_id'] == expected_active and row['kind'] == 'TRAINED']
    require(len(active_training) == 1, 'ACTIVE_TRAINED_EVENT_MISSING')
    active_event = json.loads(active_training[0]['payload'])
    checked(active_event)
    require(active_event['model_id'] == expected_active and active_event['kind'] == 'TRAINED'
            and active_event['at'] == active_row['registered_at'] == active_training[0]['at']
            and active_event['details']['definition_sha256']
            == active_definition['evidence_sha256'],
            'ACTIVE_DEFINITION_EVENT_MISMATCH')
    require(clock(active_row['registered_at'], now) <= clock(approval['at'], now),
            'ACTIVE_REGISTRATION_CLOCK_INVALID')
    controls = [row for row in facts['model_events'] if row['kind'] == 'CONTROL_APPLIED']
    require(bool(controls), 'ACTIVE_CONTROL_EVENT_MISSING')
    last = json.loads(controls[-1]['payload'])
    checked(last)
    require(last['details'] == approval and len(facts['commands']) == 1
            and last['kind'] == controls[-1]['kind'] == 'CONTROL_APPLIED'
            and last['model_id'] == controls[-1]['model_id'] == expected_active
            and last['at'] == controls[-1]['at'] == approval['at'],
            'ACTIVE_CONTROL_CHAIN_CHANGED')
    require(active_row['activation_count'] == sum(
        row['model_id'] == expected_active for row in controls), 'ACTIVE_CONTROL_COUNT_MISMATCH')
    command_row = facts['commands'][0]
    command = json.loads(command_row['command'])
    require(json.loads(command_row['result']) == approval
            and approval['command_sha256'] == signed(command)['evidence_sha256'],
            'ACTIVE_COMMAND_RECEIPT_MISMATCH')
    approved_at = clock(approval['at'], now)
    checks['active_approval_unchanged'] = True

    summary = verify_archive(archive)
    require(summary == header, 'TRAINING_SUMMARY_CHANGED_DURING_SNAPSHOT')
    bundle = verify_bundle(archive/'online-restore', archive/'provider')
    restore = document(archive/'independent-restore.json')
    require(bundle['evidence_sha256'] == model_id and restore['bundle_sha256'] == model_id
            and restore['state'] == 'PARITY_VERIFIED_NOT_ACTIVATED'
            and restore['fitted_processors_unchanged'] is True
            and restore['fit_methods_forbidden'] is True and restore['model_activated'] is False,
            'TRAINED_RESTORE_BINDING_INVALID')
    require(all(definition[key] == value for key, value in {
        'model_id': model_id, 'model_run_id': summary['run_id'],
        'training_data_end': summary['data_end'], 'training_data_sha256': summary['dataset_sha256'],
        'training_code_revision': summary['source_revision'],
        'training_completed_at': summary['completed_at'],
        'restore_proof_sha256': restore['evidence_sha256'],
        'source_snapshot_sha256': summary['source_snapshot_sha256'],
        'fitted_restore_verified': True, 'historical_pit_certified': False}.items()),
        'TRAINING_DEFINITION_BINDING_INVALID')
    require(definition['fitted_restore_verified'] is True
            and definition['historical_pit_certified'] is False
            and definition['schema_version'] == 'qtrade.online-model.v1'
            and type(definition['feature_count']) is int and definition['feature_count'] == 158
            and bundle['code_revision'] == summary['source_revision']
            and bundle['source_snapshot_sha256'] == summary['source_snapshot_sha256']
            and re.fullmatch('[a-f0-9]{40}', summary['source_revision']) is not None
            and math.isfinite(restore['max_absolute_score_difference'])
            and 0 <= restore['max_absolute_score_difference'] <= 1e-10
            and restore['feature_count'] == 158
            and all(restore[key] == bundle[key] for key in (
                'reference_rows', 'reference_start', 'reference_end', 'handler_start')),
            'TRAINED_RESTORE_PARITY_OR_CODE_INVALID')
    trained_root = base/'unified-data/qlib-versions/versions'/trained_id
    later_root = base/'unified-data/qlib-versions/versions'/later_id
    trained, later = verify_version(trained_root), verify_version(later_root)
    require(trained['version_id'] == trained_id and later['version_id'] == later_id
            and trained_id != later_id and trained['actual_end'] == summary['data_end']
            and later['actual_end'] > trained['actual_end'], 'DATA_NOT_GENUINELY_LATER')
    require(later['actual_end'] <= calendar.expected(now, 'daily').date().isoformat(),
            'LATER_SESSION_NOT_COMPLETE')
    trained_data = document(archive/'data-manifest.json')
    current_data = document(later_root/'dataset.json')
    require(trained_data == document(trained_root/'dataset.json')
            and file_hash(archive/'data-manifest.json') == summary['dataset_sha256']
            == bundle['data_sha256'] == trained['files']['dataset.json'],
            'TRAINING_DATA_VERSION_MISMATCH')
    verify_plan(summary['rolling_plan'], (trained_root/'provider/calendars/day.txt').read_text(
        ).splitlines())
    request = document(archive/'request.json', authority=False)
    require(request['data_manifest_sha256'] == summary['dataset_sha256']
            and request['source_revision'] == summary['source_revision']
            and request['source_snapshot_sha256'] == summary['source_snapshot_sha256'],
            'TRAINING_REQUEST_MISMATCH')
    training_at = clock(summary['completed_at'], now)
    restored_at = clock(restore['verified_at'], now)
    registered_at = clock(candidate['registered_at'], now)
    require(clock(trained['created_at'], now) <= restored_at <= training_at <= registered_at,
            'TRAINING_CLOCK_CHAIN_INVALID')
    trained_events = [row for row in facts['model_events'] if row['model_id'] == model_id]
    expected_states = ['TRAINED', 'VALIDATING', 'VALIDATED', 'STAGED']
    require([row['kind'] for row in trained_events] == expected_states,
            'CANDIDATE_REGISTRY_EVENT_CHAIN_INVALID')
    for row in trained_events:
        payload = json.loads(row['payload'])
        checked(payload)
        require(payload['model_id'] == model_id and payload['kind'] == row['kind']
                and payload['at'] == row['at'], 'CANDIDATE_EVENT_PAYLOAD_CHANGED')
    require(json.loads(trained_events[0]['payload'])['details']['definition_sha256']
            == definition['evidence_sha256']
            and clock(trained_events[0]['at'], now) == registered_at,
            'TRAINED_EVENT_DEFINITION_MISMATCH')
    source_files = document(archive/'checksums.json', authority=False)['files']
    training_code = {name: value for name, value in source_files.items()
                     if name.startswith('source/')}
    require({'source/scripts/run_qlib_baseline.py', 'source/scripts/run_qlib_rolling.py',
             'source/scripts/qlib_restore_bundle.py'} <= set(training_code),
            'TRAINING_CODE_PROOF_MISSING')
    evidence.update(training_run_id=summary['run_id'], trained_version_id=trained_id,
        trained_data_end=trained['actual_end'], later_data_end=later['actual_end'],
        training_summary_sha256=summary['evidence_sha256'], model_definition_sha256=definition[
            'evidence_sha256'], restore_proof_sha256=restore['evidence_sha256'],
        training_code=training_code, training_origin='UNKNOWN',
        training_origin_certified=False, training_job_verified=False)
    checks['training_archive_and_registry'] = True

    require(len(facts['jobs']) == 1 and len(facts['validations']) == 1,
            'UNIQUE_LATER_VALIDATION_JOB_AND_REGISTRATION_REQUIRED')
    job = facts['jobs'][0]
    queued_at, completed_at = job_chain(job, facts, now)
    job_request = json.loads(job['request'])
    checked(job_request)
    bindings = {key: job_request[key]
                for key in ('kind', 'model_id', 'data_version_id', 'source_revision')}
    require(job['id'] == job_request['job_id'] == signed(bindings)['evidence_sha256'][:32]
            and re.fullmatch('[a-f0-9]{40}', job_request['source_revision']) is not None,
            'VALIDATION_JOB_IDENTITY_INVALID')
    folder = base/'workflow/jobs'/job['id']
    require(document(folder/'request.json') == job_request, 'VALIDATION_JOB_REQUEST_CHANGED')
    result = json.loads(job['result'])
    validation_id = checked_identity(result['validation_id'])
    require(result == {'validation_id': validation_id, 'model_id': model_id,
                       'data_version_id': later_id}, 'VALIDATION_JOB_RESULT_CHANGED')
    proof_index = document(base/'online-research/control/validations'/(validation_id+'.json'))
    proof_root = local(base, proof_index['archive'])
    require(proof_root == folder/'output/validations'/validation_id,
            'VALIDATION_ARCHIVE_NOT_JOB_OUTPUT')
    proof = verify_validation(proof_root)
    checked(proof)
    checked(proof['contract'])
    require(document(folder/'output/result.json') == proof
            and proof_index['manifest_sha256'] == proof['evidence_sha256']
            and proof_index['model_id'] == model_id and proof['validation_id'] == validation_id
            and proof['model_run_id'] == summary['run_id']
            and proof['model_bundle_sha256'] == model_id, 'VALIDATION_PROOF_IDENTITY_CHANGED')
    registered = facts['validations'][0]
    require(registered['validation_id'] == validation_id
            and json.loads(registered['proof']) == proof, 'VALIDATION_REGISTRY_PROOF_CHANGED')
    generated_at = clock(proof['contract']['generated_at'], now)
    stage_times = [clock(row['at'], now) for row in trained_events[1:]]
    for row in trained_events[1:]:
        details = json.loads(row['payload'])['details']
        require(details['imported_verified_validation'] == validation_id,
                'VALIDATION_STAGE_EVENT_MISMATCH')
    require(stage_times == sorted(stage_times)
            and generated_at <= stage_times[0] <= stage_times[-1] <= completed_at,
            'VALIDATION_STAGE_CLOCK_INVALID')
    require(proof['contract'] == inference_contract(bundle, trained_data, current_data,
        later, calendar, now=generated_at), 'VALIDATION_HISTORICAL_CONTRACT_CHANGED')
    initial_contract = document(proof_root/'request.json')
    initial_at = clock(initial_contract['generated_at'], now)
    require(initial_contract == inference_contract(bundle, trained_data, current_data,
        later, calendar, now=initial_at)
        and approved_at <= queued_at <= initial_at <= generated_at <= completed_at
        and registered_at <= queued_at
        and clock(later['created_at'], now) <= queued_at,
        'VALIDATION_CLOCK_CHAIN_INVALID')
    require({'scripts/validate_qlib_forward.py', 'scripts/qlib_restore_bundle.py',
             'src/qtrade/research/forward_prediction.py'} <= set(proof['bindings']['code']),
            'VALIDATION_CODE_PROOF_MISSING')
    require(math.isfinite(proof['max_reference_score_difference'])
            and 0 <= proof['max_reference_score_difference'] <= 1e-10
            and proof['historical_reference_rows'] == bundle['reference_rows'],
            'VALIDATION_REFERENCE_PARITY_INVALID')
    validation_rows(proof_root, later_root, proof, bundle, current_data)
    for version in (trained, later):
        ledger = next((row for row in facts['versions']
                       if row['version_id'] == version['version_id']), None)
        require(ledger is not None and ledger['manifest_sha256'] == version['evidence_sha256']
                and ledger['actual_end'] == version['actual_end'], 'DATA_CATALOG_BINDING_CHANGED')
        admission = [row for row in facts['data_events']
                     if row['version_id'] == version['version_id']
                     and row['kind'] == 'QLIB_DATA_READY']
        require(len(admission) == 1, 'DATA_ADMISSION_EVENT_REQUIRED')
        payload = json.loads(admission[0]['payload'])
        checked(payload)
        require(payload['evidence_sha256'] == admission[0]['event_id']
                and payload['kind'] == 'QLIB_DATA_READY'
                and payload['version_id'] == version['version_id']
                and payload['actual_end'] == version['actual_end']
                and payload['manifest_sha256'] == version['evidence_sha256']
                and clock(version['created_at'], now) <= clock(payload['admitted_at'], now)
                <= (queued_at if version is later else training_at),
                'DATA_ADMISSION_CLOCK_OR_IDENTITY_INVALID')
    checks['later_automatic_validation_registered'] = True
    evidence.update(validation_job_id=job['id'], validation_id=validation_id,
        validation_sha256=proof['evidence_sha256'], validation_code=proof['bindings']['code'],
        validation_queued_at=queued_at.isoformat(),
        validation_completed_at=completed_at.isoformat())
    evidence['training_raw_day'] = raw_day(base, trained_root, trained, calendar, now)
    evidence['later_raw_day'] = raw_day(base, later_root, later, calendar, now)
    checks['raw_day_receipts_and_normalized_rows'] = True
    require(snapshot(models, workflow, data, model_id, trained_id, later_id) == facts,
            'CRITICAL_SNAPSHOT_CHANGED_DURING_REPLAY')
    checks['final_snapshot_unchanged'] = True


def audit(base: Path, models: Path, workflow: Path, data: Path, model_id: str,
          later_id: str, expected_active: str, expected_approval: str, *,
          calendar: Calendar | None = None, now: datetime | None = None) -> dict[str, Any]:
    stamp = now or datetime.now(UTC)
    require(stamp.tzinfo is not None, 'AWARE_CLOCK_REQUIRED')
    checks: dict[str, bool] = {}
    evidence: dict[str, Any] = {}
    errors = []
    try:
        require(os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() == 'false',
                'LIVE_TRADING_MUST_REMAIN_DISABLED')
        for identity in (model_id, later_id, expected_active, expected_approval):
            checked_identity(identity)
        verify(base, models, workflow, data, model_id, later_id, expected_active, expected_approval,
               calendar or Calendar(REPO/'configs/observer_calendar_2026.json'), stamp,
               evidence, checks)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        code = str(error)
        errors.append(code if re.fullmatch('CANDIDATE_AUDIT_[A-Z0-9_]{1,120}', code)
                      else 'CANDIDATE_AUDIT_INPUT_OR_ARCHIVE_VERIFICATION_FAILED')
    return signed({**AUTHORITY, 'schema_version': 'qtrade.candidate-transition-audit.v1',
        'state': 'BLOCKED' if errors else 'VERIFIED_LATER_INPUT_CANDIDATE_NOT_ACTIVATED',
        'checked_at': (now or datetime.now(UTC)).isoformat(), 'model_id': model_id,
        'clock_source': 'SYSTEM_UTC' if now is None else 'CONTROLLED_TEST_ONLY',
        'later_version_id': later_id, 'expected_active': expected_active,
        'expected_approval_sha256': expected_approval, 'checks': checks, 'evidence': evidence,
        'errors': errors, 'read_only': True, 'validation_executed_by_auditor': False,
        'auditor_sha256': file_hash(Path(__file__)),
        'model_activated_by_auditor': False, 'automatic_training_accepted': False,
        'whole_step1_accepted': False, 'historical_pit_certified': False,
        'current_serving_admission_verified': False,
        'paper_forward_ledger_verified': False, 'production_publication_verified': False,
        'browser_verified': False, 'limitations': [
            'ARCHIVE_HASH_AND_RECORDED_PROOF_REPLAY_NOT_A_NEW_INFERENCE_OR_TRAINING_RUN',
            'RAW_DAY_RECEIPTS_REPLAYED_HISTORICAL_PREFIX_BYTES_BOUND_NOT_FULL_PIT_CERTIFICATION',
            'VERIFIED_SIGNED_DATASET_ANCESTRY_DECLARATION_NOT_INTERMEDIATE_DIRECTORY_CHAIN_REPLAY',
            'SPECIFIED_LATER_HISTORICAL_VALIDATION_NOT_CURRENT_DATA_HEAD_SERVING_ADMISSION',
            'SQLITE_FACT_SNAPSHOTS_ARE_READ_ONLY_AND_RECHECKED_NOT_A_DISTRIBUTED_WRITE_LOCK',
            'CANDIDATE_VALIDATION_DOES_NOT_AUTHORIZE_REPLACING_ACTIVE_OR_EXECUTION']})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    for name in ('model-database', 'workflow-database', 'data-database', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    for name in ('model-id', 'later-version', 'expected-active', 'expected-approval'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args(argv)
    value = audit(args.base, args.model_database, args.workflow_database, args.data_database,
                  args.model_id, args.later_version, args.expected_active, args.expected_approval)
    require(not args.output.resolve().is_relative_to(args.base.resolve()),
            'OUTPUT_MUST_BE_INDEPENDENT')
    write_json_immutable(args.output, value)
    print(json.dumps(value, ensure_ascii=False, allow_nan=False))
    return 0 if not value['errors'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
