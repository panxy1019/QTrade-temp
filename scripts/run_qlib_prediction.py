"""Compute a fresh approved-model prediction through the accepted Qlib restore path."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.data_versions import file_hash
from qtrade.research.forward_prediction import verify_validation
from qtrade.research.model_registry import checked_document, checked_identity
from qtrade.research.prediction_registry import checked_bindings
from scripts.atomic_json import write_json_immutable
from scripts.build_qlib_daily_version import move_durable, sync_directory
from scripts.prepare_qlib_incremental import load_signed
from scripts.validate_qlib_forward import validate

REPO = Path(__file__).resolve().parents[1]


def verify_prediction(root: Path) -> dict[str, Any]:
    value = load_signed(root/'manifest.json')
    checked_document(value)
    request = load_signed(root/'request.json')
    checked_document(request)
    checked_document(request['approval'])
    bindings = checked_bindings(request['bindings'])
    identity = checked_identity(request['prediction_id'])
    if (identity != signed(bindings)['evidence_sha256']
            or request['schema_version'] != 'qtrade.daily-prediction-request.v1'
            or value['prediction_id'] != identity or value['bindings'] != bindings
            or value['request_sha256'] != request['evidence_sha256']
            or value['schema_version'] != 'qtrade.daily-prediction.v1'
            or value['state'] != 'SEALED_ACTIVE_MODEL_PREDICTION'
            or value['auto_predict_enabled'] is not True
            or request['approval']['state'] != 'APPLIED'
            or request['approval']['model_id'] != bindings['model_id']
            or request['approval']['active_model'] != bindings['model_id']):
        raise ValueError('DAILY_PREDICTION_IDENTITY_OR_AUTHORITY_INVALID')
    files = value['files']
    if not 5 <= len(files) <= 100 or not {
        'request.json', 'source/scripts/run_qlib_prediction.py',
        'source/src/qtrade/research/prediction_registry.py',
    } <= set(files):
        raise ValueError('DAILY_PREDICTION_INVENTORY_INVALID')
    for name, checksum in files.items():
        path = root/name
        if (path.is_symlink() or not path.resolve().is_relative_to(root.resolve())
                or not path.is_file() or file_hash(path) != checksum):
            raise ValueError('DAILY_PREDICTION_FILE_CHANGED')
    if {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()
        and p != root/'manifest.json'} != set(files):
        raise ValueError('DAILY_PREDICTION_UNBOUND_FILE')
    validation_id = checked_identity(value['calculation_validation_id'])
    calculation = verify_validation(root/'calculation'/validation_id)
    if (calculation['evidence_sha256'] != value['calculation_manifest_sha256']
            or calculation['model_bundle_sha256'] != bindings['model_id']
            or calculation['contract'] != value['contract']
            or calculation['score_count'] != value['score_count']
            or calculation['feature_count'] != value['feature_count']):
        raise ValueError('DAILY_PREDICTION_CALCULATION_CHANGED')
    generated = datetime.fromisoformat(value['contract']['generated_at'])
    claimed = datetime.fromisoformat(request['claimed_at'])
    approved = datetime.fromisoformat(request['approval']['at'])
    if (any(c.tzinfo is None for c in (generated, claimed, approved))
            or not approved <= claimed <= generated
            or request['approval']['evidence_sha256'] != bindings['approval_sha256']
            or value['contract']['data_version_id'] != bindings['data_version_id']
            or value['contract']['data_end'] != bindings['data_end']
            or value['contract']['observation_universe_version'] != bindings[
                'observation_universe_version']):
        raise ValueError('DAILY_PREDICTION_APPROVAL_OR_CLOCK_CHANGED')
    return value


def run(request_file: Path, archive: Path, data_root: Path,
        output: Path, *, revision: str) -> dict[str, Any]:
    request = load_signed(request_file)
    checked_document(request)
    bindings = checked_bindings(request['bindings'])
    identity = checked_identity(request['prediction_id'])
    if (request['schema_version'] != 'qtrade.daily-prediction-request.v1'
            or bindings['code_revision'] != revision
            or identity != signed(bindings)['evidence_sha256']):
        raise ValueError('DAILY_PREDICTION_REQUEST_CHANGED')
    checked_document(request['approval'])
    if (request['approval']['state'] != 'APPLIED'
            or request['approval']['active_model'] != bindings['model_id']
            or request['approval']['evidence_sha256'] != bindings['approval_sha256']):
        raise ValueError('DAILY_PREDICTION_APPROVAL_REQUIRED')
    target = output/identity
    if target.exists():
        value = verify_prediction(target)
        if value['request_sha256'] != request['evidence_sha256']:
            raise ValueError('DAILY_PREDICTION_REQUEST_REBINDING')
        return value
    # Preserve partial attempts. A restarted worker reconciles sealed output first.
    stage = output/(identity+'.'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')+'.partial')
    stage.mkdir(parents=True, exist_ok=False)
    write_json_immutable(stage/'request.json', request)
    for name in ('scripts/run_qlib_prediction.py',
                 'src/qtrade/research/prediction_registry.py'):
        destination = stage/'source'/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((REPO/name).read_bytes())
    # This computation verifies inputs without modifying model activation. Its
    # subordinate validation proof remains intact; the outer record binds approval.
    calculation = validate(archive, data_root, stage/'calculation')
    value = signed({**AUTHORITY, 'schema_version': 'qtrade.daily-prediction.v1',
        'state': 'SEALED_ACTIVE_MODEL_PREDICTION', 'prediction_id': identity,
        'bindings': bindings, 'request_sha256': request['evidence_sha256'],
        'contract': calculation['contract'], 'auto_predict_enabled': True,
        'calculation_validation_id': calculation['validation_id'],
        'calculation_manifest_sha256': calculation['evidence_sha256'],
        'score_count': calculation['score_count'], 'feature_count': calculation['feature_count'],
        'model_run_id': calculation['model_run_id'],
        'max_reference_score_difference': calculation['max_reference_score_difference'],
        'limitations': [r for r in calculation['limitations'] if not r.startswith(
            'Candidate validation only;')] + [
                'Automatic research scores after model approval; not orders or observed returns',
                'Current-signal eligibility requires final model and data head reconciliation'],
        'files': {p.relative_to(stage).as_posix(): file_hash(p)
                  for p in stage.rglob('*') if p.is_file()}})
    write_json_immutable(stage/'manifest.json', value)
    verify_prediction(stage)
    for path in stage.rglob('*'):
        if path.is_file():
            with path.open('rb') as stream:
                os.fsync(stream.fileno())
    for path in sorted((p for p in stage.rglob('*') if p.is_dir()),
                       key=lambda p: len(p.parts), reverse=True):
        sync_directory(path)
    sync_directory(stage)
    move_durable(stage, target)
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ('request', 'archive', 'data-root', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    args = parser.parse_args()
    if os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
        raise RuntimeError('LIVE_TRADING_MUST_REMAIN_DISABLED')
    value = run(args.request, args.archive, args.data_root, args.output,
                revision=args.source_revision)
    print(json.dumps({'prediction_id': value['prediction_id'], 'state': value['state']}))


if __name__ == '__main__':
    main()
