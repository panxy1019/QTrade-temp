from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.session_acceptance import assess_transition


def doc(**values):
    return signed({**AUTHORITY, **values})


@pytest.fixture
def transition():
    previous = doc(version_id='a'*64, actual_end='2026-10-26')
    current = doc(version_id='b'*64, actual_end='2026-10-27',
                  bindings={'preparation_sha256': 'c'*64, 'prepared_heads': {'600000': '2'*64}},
                  created_at='2026-10-27T10:00:00+00:00')
    admission = doc(kind='QLIB_DATA_READY', version_id='b'*64,
                    manifest_sha256=current['evidence_sha256'],
                    admitted_at='2026-10-27T10:00:30+00:00')
    prepared = signed({'request': 'c'*64, 'ready': {'600000': '2'*64}})['evidence_sha256']
    completed = doc(prepared_identity=prepared, completed_at='2026-10-27T10:01:00+00:00',
                    result={'version_id': 'b'*64, 'actual_end': '2026-10-27',
                            'state': 'RESEARCH_READY_QUALIFIED_SUBSET'})
    prediction = doc(prediction_id='d'*64, state='SEALED_ACTIVE_MODEL_PREDICTION',
        bindings={'data_version_id': 'b'*64, 'data_end': '2026-10-27',
                  'data_event_id': admission['evidence_sha256'],
                  'model_id': 'e'*64, 'approval_sha256': 'f'*64},
        contract={'data_version_id': 'b'*64, 'data_end': '2026-10-27',
                  'generated_at': '2026-10-27T10:02:00+00:00',
                  'expected_execution_at': '2026-10-28T09:30:00+08:00'})
    signals = doc(signal_family_id='1'*64, data_end='2026-10-27',
        state='SIGNALS_AND_INDICATIVE_TARGETS_SEALED',
        bindings={'data_version_id': 'b'*64, 'data_event_id': admission['evidence_sha256']},
        generated_at='2026-10-27T10:03:00+00:00',
        expected_execution_at='2026-10-28T09:30:00+08:00')
    return [previous, current, completed, prediction, signals, admission]


def assess(values, now=datetime(2026, 10, 27, 11, tzinfo=UTC)):
    return assess_transition(*values, Calendar(Path(__file__).resolve().parents[1]/
        'configs/observer_calendar_2026.json'), now=now)


def test_observed_next_session_lineage_is_narrow_not_full_step1(transition):
    result = assess(transition)
    assert result['state'] == 'VERIFIED_VERSION_PREDICTION_SIGNAL_TRANSITION'
    for key in ('whole_step1_accepted', 'production_publication_verified', 'browser_verified',
                'paper_forward_ledger_verified', 'historical_pit_certified',
                'live_trading_enabled'):
        assert result[key] is False


def test_next_day_cannot_be_accepted_before_actual_complete_session(transition):
    with pytest.raises(ValueError, match='NEXT_SESSION_NOT_COMPLETE'):
        assess(transition, datetime(2026, 10, 27, 0, tzinfo=UTC))


@pytest.mark.parametrize(('index', 'field', 'replacement'), [
    (1, 'actual_end', '2026-10-28'),
    (1, 'version_id', 'a'*64),
    (2, 'completed_at', '2026-10-28T11:00:00+00:00'),
    (2, 'completed_at', '2026-10-27T11:00:00'),
    (4, 'generated_at', '2026-10-27T09:59:00+00:00'),
    (4, 'expected_execution_at', '2026-10-29T09:30:00+08:00'),
    (4, 'data_end', '2026-10-26'),
    (4, 'bindings', {'data_version_id': 'a'*64}),
    (3, 'state', 'FAILED'),
    (2, 'prepared_identity', '0'*64),
    (5, 'kind', 'QLIB_DATA_ROLLBACK'),
])
def test_wrong_versions_dates_clocks_and_states_fail_closed(transition, index, field,
                                                           replacement):
    values = deepcopy(transition)
    values[index][field] = replacement
    values[index] = signed({k: v for k, v in values[index].items() if k != 'evidence_sha256'})
    with pytest.raises(ValueError):
        assess(values)


def test_corrupt_completion_cannot_certify_automatic_cycle(transition):
    transition[2]['prepared_identity'] = '0'*64
    with pytest.raises(ValueError):
        assess(transition)
