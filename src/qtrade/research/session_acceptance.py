"""Read-only lineage checks for a real subsequent-session serving transition."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.forward_prediction import next_execution
from qtrade.research.model_registry import checked_document, checked_identity


def assess_transition(previous: dict[str, Any], current: dict[str, Any],
                      completion: dict[str, Any], prediction: dict[str, Any],
                      signals: dict[str, Any], admission: dict[str, Any], calendar: Calendar, *,
                      now: datetime) -> dict[str, Any]:
    """Inputs must additionally pass their full on-disk archive validators.

    This verifies C serving lineage only; it does not certify PAPER, browser,
    historical PIT, or the whole STEP1. Hashes are content integrity, not signatures.
    """
    if now.tzinfo is None:
        raise ValueError('SESSION_ACCEPTANCE_TIMEZONE_REQUIRED')
    for document in (previous, current, completion, prediction, signals, admission):
        checked_document(document)
    expected_day = next_execution(previous['actual_end'], calendar).date().isoformat()
    if calendar.expected(now, 'daily').date().isoformat() < expected_day:
        raise ValueError('SESSION_ACCEPTANCE_NEXT_SESSION_NOT_COMPLETE')
    if (previous['version_id'] == current['version_id']
            or current['actual_end'] != expected_day
            or completion['result']['version_id'] != current['version_id']
            or completion['result']['actual_end'] != expected_day
            or completion['result']['state'] != 'RESEARCH_READY_QUALIFIED_SUBSET'):
        raise ValueError('SESSION_ACCEPTANCE_AUTOMATIC_VERSION_MISMATCH')
    checked_identity(completion['prepared_identity'])
    prepared = signed({'request': current['bindings']['preparation_sha256'],
                       'ready': current['bindings']['prepared_heads']})['evidence_sha256']
    if completion['prepared_identity'] != prepared:
        raise ValueError('SESSION_ACCEPTANCE_PREPARATION_IDENTITY_MISMATCH')
    binding = prediction['bindings']
    signal_binding = signals['bindings']
    epoch = checked_identity(admission['evidence_sha256'])
    if (admission['kind'] != 'QLIB_DATA_READY'
            or admission['version_id'] != current['version_id']
            or admission['manifest_sha256'] != current['evidence_sha256']
            or binding.get('data_event_id') != epoch
            or signal_binding.get('data_event_id') != epoch):
        raise ValueError('SESSION_ACCEPTANCE_ADMISSION_EPOCH_MISMATCH')
    if (binding['data_version_id'] != current['version_id']
            or signal_binding['data_version_id'] != current['version_id']
            or binding['data_end'] != expected_day or signals['data_end'] != expected_day
            or prediction['contract']['data_end'] != expected_day
            or prediction['contract']['data_version_id'] != current['version_id']
            or prediction['state'] != 'SEALED_ACTIVE_MODEL_PREDICTION'
            or signals['state'] != 'SIGNALS_AND_INDICATIVE_TARGETS_SEALED'):
        raise ValueError('SESSION_ACCEPTANCE_OUTPUT_LINEAGE_MISMATCH')
    version_at = datetime.fromisoformat(current['created_at'])
    completed_at = datetime.fromisoformat(completion['completed_at'])
    admitted_at = datetime.fromisoformat(admission['admitted_at'])
    predicted_at = datetime.fromisoformat(prediction['contract']['generated_at'])
    signaled_at = datetime.fromisoformat(signals['generated_at'])
    execute_at = next_execution(expected_day, calendar)
    prediction_execute = datetime.fromisoformat(prediction['contract']['expected_execution_at'])
    signals_execute = datetime.fromisoformat(signals['expected_execution_at'])
    times = (version_at, completed_at, admitted_at, predicted_at, signaled_at,
             prediction_execute, signals_execute)
    if (any(t.tzinfo is None for t in times)
            or not version_at <= completed_at <= now
            or not version_at <= admitted_at <= min(predicted_at, signaled_at, completed_at)
            or not version_at <= predicted_at < execute_at
            or not version_at <= signaled_at < execute_at
            or predicted_at > now or signaled_at > now
            or prediction_execute != execute_at or signals_execute != execute_at):
        raise ValueError('SESSION_ACCEPTANCE_CLOCK_LINEAGE_INVALID')
    return signed({**AUTHORITY, 'schema_version': 'qtrade.session-transition.v1',
        'state': 'VERIFIED_VERSION_PREDICTION_SIGNAL_TRANSITION',
        'checked_at': now.isoformat(), 'previous_version_id': previous['version_id'],
        'version_id': current['version_id'], 'data_end': expected_day,
        'prediction_id': prediction['prediction_id'],
        'signal_family_id': signals['signal_family_id'],
        'approved_model_id': binding['model_id'],
        'approval_sha256': binding['approval_sha256'],
        'data_event_id': epoch,
        'automatic_completion_sha256': completion['evidence_sha256'],
        'prediction_sha256': prediction['evidence_sha256'],
        'signals_sha256': signals['evidence_sha256'],
        'expected_execution_at': execute_at.isoformat(),
        'whole_step1_accepted': False, 'production_publication_verified': False,
        'browser_verified': False, 'paper_forward_ledger_verified': False,
        'historical_pit_certified': False})
