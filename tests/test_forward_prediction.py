import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_versions import file_hash
from qtrade.research.forward_prediction import (
    expected_codes,
    inference_contract,
    rank_scores,
    verify_validation,
)


@pytest.fixture
def inputs():
    model = {'reference_end': '2026-10-23', 'feature_dtypes': ['float64']*158,
             'evidence_sha256': 'a'*64, 'data_sha256': 'b'*64}
    trained = {'end': '2026-10-23', 'evidence_sha256': 'c'*64}
    current = {'end': '2026-10-26', 'parent_dataset_sha256': 'c'*64, 'incremental': {
        'observation_universe_version': 'd'*64, 'current_symbols': ['600036'],
        'blocked_symbols': ['000001']}}
    version = {'actual_end': '2026-10-26', 'version_id': 'e'*64}
    calendar = Calendar({'valid_from': '2026-10-01', 'valid_through': '2026-10-31',
                         'weekday_closures': []})
    return model, trained, current, version, calendar


def test_fresh_contract_binds_lineage_real_end_and_next_open_without_claiming_returns(inputs):
    result = inference_contract(*inputs, now=datetime(2026, 10, 26, 12, tzinfo=UTC))
    assert result['expected_execution_at'] == '2026-10-27T09:30:00+08:00'
    assert result['forward_return_observed'] is False
    assert result['current_input_symbols'] == ['600036']
    assert result['blocked_input_symbols'] == ['000001']
    assert result['external_order_transmission'] is False


def test_old_frozen_model_can_follow_a_verified_chain_of_new_daily_versions(inputs):
    model, trained, current, data_version, calendar = inputs
    current['parent_dataset_sha256'] = 'f'*64
    current['ancestor_dataset_sha256'] = ['f'*64, trained['evidence_sha256']]
    result = inference_contract(model, trained, current, data_version, calendar,
                                now=datetime(2026, 10, 26, 12, tzinfo=UTC))
    assert result['model_bundle_sha256'] == model['evidence_sha256']


@pytest.mark.parametrize('change', ['ancestor', 'precision', 'date', 'training_end'])
def test_wrong_lineage_or_old_precision_cannot_produce_a_forward_contract(inputs, change):
    model, trained, current, data_version, calendar = deepcopy(inputs)
    if change == 'ancestor':
        current['parent_dataset_sha256'] = 'f'*64
    elif change == 'precision':
        model['feature_dtypes'][0] = 'float32'
    elif change == 'date':
        current['end'] = '2026-10-23'
    else:
        model['reference_end'] = '2026-10-26'
    with pytest.raises(ValueError, match='LINEAGE_OR_PRECISION'):
        inference_contract(model, trained, current, data_version, calendar,
                           now=datetime(2026, 10, 26, 12, tzinfo=UTC))


@pytest.mark.parametrize('clock,error', [
    (datetime(2026, 10, 26, 7, tzinfo=UTC), 'LATEST_COMPLETE'),
    (datetime(2026, 10, 27, 12, tzinfo=UTC), 'LATEST_COMPLETE'),
    (datetime(2026, 10, 27, 2, tzinfo=UTC), 'EXECUTION_ALREADY_PASSED'),
    (datetime(2026, 10, 26, 12), 'TIMEZONE'),
])
def test_no_premature_stale_or_backdated_execution(inputs, clock, error):
    with pytest.raises(ValueError, match=error):
        inference_contract(*inputs, now=clock)


def test_daily_eligibility_intersects_current_inputs_and_historical_intervals():
    lines = ['SH600036\t2026-01-01\t2026-10-26',
             'SZ000001\t2026-01-01\t2026-10-23',
             'SZ000002\t2026-01-01\t2026-10-26']
    assert expected_codes(lines, ['600036', '000001'], '2026-10-26') == ['SH600036']
    with pytest.raises(ValueError, match='NO_QUALIFIED'):
        expected_codes(lines, ['000001'], '2026-10-26')


@pytest.mark.parametrize('rows', [[], [{'instrument': 'SH600036', 'score': float('nan')}],
    [{'instrument': 'SH600036', 'score': float('inf')}],
    [{'instrument': 'SZ000001', 'score': 1}],
    [{'instrument': 'SH600036', 'score': 1}, {'instrument': 'SH600036', 'score': 2}],
])
def test_missing_extra_duplicate_and_nonfinite_scores_are_rejected(rows):
    with pytest.raises(ValueError, match='COVERAGE_OR_VALUES'):
        rank_scores(rows, ['SH600036'])


def test_equal_scores_have_deterministic_code_tiebreak_and_full_coverage():
    result = rank_scores([{'instrument': 'SH600036', 'score': 0.2},
                          {'instrument': 'SZ000001', 'score': 0.2}], ['SZ000001', 'SH600036'])
    assert [r['instrument'] for r in result] == ['SH600036', 'SZ000001']
    assert [r['rank'] for r in result] == [1, 2]


@pytest.fixture
def snapshot(inputs, tmp_path):
    root = tmp_path/'validation'
    root.mkdir()
    contract = inference_contract(*inputs, now=datetime(2026, 10, 26, 12, tzinfo=UTC))
    for name in ['request.json', 'scores.parquet', 'features.parquet', 'source/inference.py']:
        path = root/name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(name.encode())
    bindings = {'model': contract['model_bundle_sha256'], 'data': contract['data_version_id'],
                'code': {'inference.py': file_hash(root/'source/inference.py')}}
    value = signed({**AUTHORITY, 'schema_version': 'qtrade.qlib-forward-validation.v1',
                    'state': 'FORWARD_INPUT_VALIDATED_NOT_ACTIVATED', 'contract': contract,
                    'bindings': bindings, 'validation_id': signed(bindings)['evidence_sha256'],
                    'model_activated': False, 'auto_predict_enabled': False,
                    'fitted_processors_unchanged': True, 'fit_methods_forbidden': True,
                    'score_count': 1, 'feature_count': 158,
                    'files': {p.relative_to(root).as_posix(): file_hash(p)
                              for p in root.rglob('*') if p.is_file()}})
    (root/'manifest.json').write_text(json.dumps(value))
    return root, value


def test_prediction_archive_binds_exact_code_and_files(snapshot):
    root, value = snapshot
    assert verify_validation(root) == value
    (root/'scores.parquet').write_bytes(b'changed')
    with pytest.raises(ValueError, match='FILE_CHANGED'):
        verify_validation(root)


@pytest.mark.parametrize('change', ['source', 'extra', 'activation', 'contract'])
def test_rehashed_or_unbound_validation_cannot_be_reused(snapshot, change):
    root, value = snapshot
    if change == 'source':
        value['bindings']['code']['inference.py'] = 'f'*64
        value['validation_id'] = signed(value['bindings'])['evidence_sha256']
    elif change == 'extra':
        (root/'extra.json').write_text('not bound')
    elif change == 'activation':
        value['model_activated'] = True
    else:
        value['contract']['data_version_id'] = 'f'*64
        value['contract'] = signed({k: v for k, v in value['contract'].items()
                                     if k != 'evidence_sha256'})
    (root/'manifest.json').write_text(json.dumps(signed({k: v for k, v in value.items()
                                                      if k != 'evidence_sha256'})))
    with pytest.raises(ValueError):
        verify_validation(root)
