"""Validate a frozen candidate on a new data version without activating it."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_versions import file_hash, verify_version
from qtrade.research.forward_prediction import (
    expected_codes,
    inference_contract,
    rank_scores,
    verify_validation,
)
from qtrade.research.qlib_publication import verify_archive
from scripts.atomic_json import write_json_immutable
from scripts.prepare_qlib_incremental import load_signed
from scripts.qlib_restore_bundle import PACKAGES, FrozenRecorder, processor_hash, verify_bundle

REPO = Path(__file__).resolve().parents[1]


def validate(archive: Path, data_root: Path, output: Path) -> dict[str, Any]:
    import numpy as np
    pd = import_module('pandas')

    summary = verify_archive(archive)
    model = verify_bundle(archive/'online-restore', archive/'provider')
    data_version = verify_version(data_root)
    trained_data = load_signed(archive/'data-manifest.json')
    current_data = load_signed(data_root/'dataset.json')
    proof = load_signed(archive/'independent-restore.json')
    if (proof['state'] != 'PARITY_VERIFIED_NOT_ACTIVATED'
            or proof['bundle_sha256'] != model['evidence_sha256']
            or proof['fitted_processors_unchanged'] is not True
            or proof['fit_methods_forbidden'] is not True
            or model['data_sha256'] != file_hash(archive/'data-manifest.json')
            or {p: version(p) for p in PACKAGES} != model['packages']
            or version('pyqlib') != '0.9.7'):
        raise ValueError('FORWARD_MODEL_RESTORE_OR_ENVIRONMENT_NOT_ACCEPTED')
    calendar = Calendar(REPO/'configs/observer_calendar_2026.json')
    contract = inference_contract(model, trained_data, current_data, data_version, calendar,
                                  now=datetime.now(UTC))
    source_names = (
        'scripts/validate_qlib_forward.py', 'scripts/qlib_restore_bundle.py',
        'scripts/build_qlib_daily_version.py', 'scripts/prepare_qlib_incremental.py',
        'scripts/atomic_json.py', 'src/qtrade/research/forward_prediction.py',
        'src/qtrade/research/data_versions.py', 'src/qtrade/research/qlib_publication.py',
        'src/qtrade/research/qlib_processors.py', 'src/qtrade/research/feature_precision.py',
        'src/qtrade/monitoring/data_freshness.py', 'configs/observer_calendar_2026.json',
    )
    bindings = {'model': model['evidence_sha256'], 'data': data_version['version_id'],
                'code': {name: file_hash(REPO/name) for name in source_names}}
    identity = signed(bindings)['evidence_sha256']
    target = output/identity
    if (target/'manifest.json').exists():
        result = verify_validation(target)
        if result['validation_id'] != identity:
            raise ValueError('FORWARD_VALIDATION_ARCHIVE_CHANGED')
        return result
    stage = output/(identity+'.'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')+'.partial')
    stage.mkdir(parents=True, exist_ok=False)
    write_json_immutable(stage/'request.json', contract)
    for name in source_names:
        destination = stage/'source'/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((REPO/name).read_bytes())
        if file_hash(destination) != bindings['code'][name]:
            raise ValueError('FORWARD_CODE_CHANGED_DURING_CAPTURE')
    qlib = import_module('qlib')
    DataHandlerLP = import_module('qlib.data.dataset.handler').DataHandlerLP
    RMDLoader = import_module('qlib.workflow.online.update').RMDLoader
    qlib.init(provider_uri=str(data_root/'provider'), region='cn', kernels=2,
              exp_manager={'class': 'MLflowExpManager', 'module_path': 'qlib.workflow.expm',
                           'kwargs': {'uri': (stage/'tracking').resolve().as_uri(),
                                      'default_exp_name': 'frozen-forward-validation'}})
    loader = RMDLoader(FrozenRecorder(archive/'online-restore'))
    unprepared = loader.rec.load_object('dataset')
    before = processor_hash(unprepared)
    if before != model['processor_sha256']:
        raise ValueError('FORWARD_FITTED_PROCESSORS_CHANGED')

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError('REFIT_FORBIDDEN_DURING_FORWARD_INFERENCE')

    DataHandlerLP.fit = forbidden
    DataHandlerLP.fit_process_data = forbidden
    data = loader.get_dataset(model['handler_start'], contract['data_end'],
        segments={'test': (model['reference_start'], contract['data_end'])},
        unprepared_dataset=unprepared)
    features = data.prepare('test', col_set='feature', data_key='infer')
    scores = loader.get_model().predict(data, segment='test').to_frame('score')
    reference_features = pd.read_parquet(archive/'online-restore/reference-features.parquet')
    reference_scores = pd.read_parquet(archive/'online-restore/reference-scores.parquet')
    historical = features.loc[features.index.get_level_values('datetime') <= pd.Timestamp(
        model['reference_end'])]
    if (features.columns.tolist() != model['feature_order']
            or [str(d) for d in features.dtypes] != model['feature_dtypes']
            or not historical.index.equals(reference_features.index)
            or not features.index.equals(scores.index)
            or not features.index.is_unique
            or not np.allclose(historical, reference_features, atol=1e-12, rtol=1e-12,
                               equal_nan=True)
            or not np.allclose(scores.loc[reference_scores.index], reference_scores,
                               atol=1e-10, rtol=1e-10, equal_nan=False)
            or processor_hash(data) != before):
        raise ValueError('FORWARD_APPEND_CHANGED_FROZEN_REFERENCE_OR_PROCESSORS')
    latest = scores.loc[scores.index.get_level_values('datetime') == pd.Timestamp(
        contract['data_end'])].droplevel('datetime')
    expected = expected_codes((data_root/'provider/instruments/workflow_supported.txt').read_text(
        ).splitlines(), current_data['incremental']['current_symbols'], contract['data_end'])
    ranked = rank_scores([{'instrument': code, 'score': float(row['score'])}
                          for code, row in latest.iterrows()], expected)
    if len(ranked) < 2*summary['portfolio']['topk']:
        raise ValueError('FORWARD_INSUFFICIENT_COVERAGE_FOR_FROZEN_PORTFOLIO')
    generated = inference_contract(model, trained_data, current_data, data_version, calendar,
                                   now=datetime.now(UTC))
    pd.DataFrame(ranked).to_parquet(stage/'scores.parquet', index=False)
    latest_features = features.loc[features.index.get_level_values('datetime') == pd.Timestamp(
        contract['data_end'])]
    latest_features.to_parquet(stage/'features.parquet')
    for name in ('scores.parquet', 'features.parquet'):
        with (stage/name).open('rb') as file:
            os.fsync(file.fileno())
    result = signed({
        **AUTHORITY, 'schema_version': 'qtrade.qlib-forward-validation.v1',
        'state': 'FORWARD_INPUT_VALIDATED_NOT_ACTIVATED', 'validation_id': identity,
        'bindings': bindings,
        'model_run_id': summary['run_id'], 'model_bundle_sha256': model['evidence_sha256'],
        'contract': generated, 'score_count': len(ranked), 'feature_count': 158,
        'historical_reference_rows': len(reference_scores),
        'fitted_processors_unchanged': True, 'fit_methods_forbidden': True,
        'max_reference_score_difference': float(np.abs(
            scores.loc[reference_scores.index]-reference_scores).to_numpy().max()),
        'model_activated': False, 'auto_predict_enabled': False,
        'files': {p.relative_to(stage).as_posix(): file_hash(p)
                  for p in stage.rglob('*') if p.is_file()},
        'limitations': list(dict.fromkeys(summary['limitations'] + current_data['limitations'])) + [
            'Current liquidity observation universe has survivorship and selection bias',
            'Candidate validation only; human activation and automatic event worker pending',
            'No observed forward returns; scores are not orders or expected returns',
        ],
    })
    write_json_immutable(stage/'manifest.json', result)
    verify_validation(stage)
    from scripts.build_qlib_daily_version import move_durable, sync_directory

    for name in result['files']:
        with (stage/name).open('rb') as handle:
            os.fsync(handle.fileno())
    for folder in sorted((p for p in stage.rglob('*') if p.is_dir()),
                          key=lambda p: len(p.parts), reverse=True):
        sync_directory(folder)
    sync_directory(stage)

    move_durable(stage, target)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ('archive', 'data-root', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
        raise RuntimeError('LIVE_TRADING_MUST_REMAIN_DISABLED')
    print(json.dumps(validate(args.archive, args.data_root, args.output)))


if __name__ == '__main__':
    main()
