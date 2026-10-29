"""Purpose-scoped diagnostic quality; this module grants no admission or execution."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from qtrade.market_data.akshare import SHANGHAI
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.pit_quality import status_quality
from qtrade.research.workflow import digest

SCHEMA = 'qtrade.data-quality-observation.v1'
CLASSIFICATIONS = {'COMPLETE', 'SUSPENDED', 'DATA_GAP', 'UNKNOWN'}
PURPOSES = {'DAILY_RESEARCH', 'MINUTE_RESEARCH', 'PREDICTION', 'PAPER'}


def retry_task_identity(task: dict[str, Any]) -> str:
    """Identity of the exact failed attempt/target, not merely its security code."""
    return digest({k: task.get(k) for k in (
        'universe_version', 'symbol', 'frequency', 'trading_day', 'state', 'desired_end',
        'completed_end', 'attempts', 'next_attempt_at', 'active_batch', 'last_success_batch',
        'last_error', 'consecutive_failures')})


def checked_observation(value: dict[str, Any], now: datetime) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError('QUALITY_STATUS_OBJECT_REQUIRED')
    if signed({k: v for k, v in value.items() if k != 'evidence_sha256'}) != value:
        raise ValueError('QUALITY_STATUS_HASH_INVALID')
    status_quality(value, value['symbol'], now)
    return value


def classify_task(
    task: dict[str, Any], observations: list[dict[str, Any]],
    issuer_starts: list[dict[str, Any]], *, now: datetime,
) -> dict[str, Any]:
    """A day-specific vendor state plus issuer start can explain missing bars, not trading.

    A start announcement alone never supplies an interval end. Present prices that
    conflict with suspension, conflicting flags, missing dates and stale evidence
    are explicit; original queue states/errors remain unchanged.
    """
    symbol, frequency, day = task['symbol'], task['frequency'], task['trading_day']
    if (now.tzinfo is None or re.fullmatch(r'[0-9]{6}', symbol) is None
            or frequency not in {'day', '5min'} or date.fromisoformat(day).isoformat() != day):
        raise ValueError('QUALITY_TASK_IDENTITY_INVALID')
    daily: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for observation in observations:
        record = checked_observation(observation, now)
        if record['symbol'] == symbol:
            daily.extend((row, record) for row in record['rows'] if row['date'] == day)
    flags = {row.get('tradestatus') for row, _ in daily}
    observed = max((datetime.fromisoformat(record['observed_at']) for _, record in daily),
                   default=None)
    starts = [r for r in issuer_starts if r['symbol'] == symbol
              and r['effective_start_date'] <= day
              and r['claim'] == 'ISSUER_ANNOUNCED_SUSPENSION_START'
              and datetime.fromisoformat(r['observed_at']) <= now]
    complete = bool(task['state'] == 'SUCCEEDED' and task.get('completed_end')
                    and task['completed_end'] == task['desired_end'])
    partial = bool(task.get('completed_end')) and not complete
    market_state = 'SUSPENDED' if flags == {'0'} else 'TRADING' if flags == {'1'} else 'UNKNOWN'
    classification = 'COMPLETE' if complete else 'DATA_GAP' if partial else 'UNKNOWN'
    reason = 'COMPLETE_PRICE_CONTENT_ONLY' if complete else 'DAILY_STATE_OR_PRICE_UNKNOWN'
    if flags - {'0', '1'} or len(flags) > 1:
        classification = 'UNKNOWN'
        market_state = 'UNKNOWN'
        reason = 'CONFLICTING_OR_UNKNOWN_DAY_STATE'
    elif flags == {'0'}:
        if complete or partial:
            classification, market_state = 'UNKNOWN', 'UNKNOWN'
            reason = 'SUSPENSION_CONFLICTS_WITH_ACCEPTED_PRICE_CONTENT'
        elif starts:
            classification, reason = 'SUSPENDED', 'DAY_VENDOR_STATE_WITH_ISSUER_START'
        else:
            reason = 'VENDOR_SUSPENSION_WITHOUT_INDEPENDENT_ISSUER_START'
    elif flags == {'1'} and not complete:
        classification, reason = 'DATA_GAP', 'DAY_REPORTED_TRADING_PRICE_INCOMPLETE'
    elif partial:
        reason = 'PARTIAL_PRICE_WINDOW'
    # This is a recommendation only. It cannot edit claims or grant new risk.
    expires = min(observed + timedelta(hours=24), now + timedelta(hours=6)) if observed else now
    defer = classification == 'SUSPENDED' and expires > now
    return {
        'symbol': symbol, 'frequency': frequency, 'trading_day': day,
        'universe_version': task.get('universe_version'),
        'task_snapshot_sha256': retry_task_identity(task),
        'classification': classification, 'market_state': market_state,
        'coverage': 'COMPLETE' if complete else 'PARTIAL' if partial else 'MISSING',
        'reason': reason, 'original_task_state': task['state'],
        'original_error': task.get('last_error'), 'desired_end': task['desired_end'],
        'completed_end': task.get('completed_end'),
        'source_observed_at': observed.isoformat() if observed else None,
        'historical_available_at': None, 'historical_pit_certified': False,
        'day_state_evidence': sorted({r['evidence_sha256'] for _, r in daily}),
        'issuer_start_evidence': sorted({r['raw_sha256'] for r in starts}),
        'retry_recommendation': 'DEFER_PENDING_APPROVAL' if defer else 'KEEP_EXISTING_RETRY',
        'proposed_recheck_at': expires.isoformat() if defer else None,
        'production_admission_effect': 'NONE', 'execution_admitted': False,
    }


def assess_scope(
    cases: list[dict[str, Any]], requirements: list[dict[str, Any]], *, purpose: str,
) -> dict[str, Any]:
    """Assess exact requested keys; unrelated symbols/frequencies/dates are irrelevant.

    Existing model-input validation and PAPER constraint engines remain mandatory.
    Outputs are diagnostic and cannot replace their signed bindings or permissions.
    """
    if purpose not in PURPOSES or not requirements:
        raise ValueError('QUALITY_PURPOSE_OR_REQUIREMENTS_INVALID')
    index = {(r['symbol'], r['frequency'], r['trading_day']): r for r in cases}
    keys = [(r['symbol'], r['frequency'], r['trading_day']) for r in requirements]
    if len(index) != len(cases) or len(set(keys)) != len(keys):
        raise ValueError('QUALITY_SCOPE_DUPLICATE')
    results = []
    for required, key in zip(requirements, keys, strict=True):
        row = index.get(key)
        allowed, reason = False, 'REQUIRED_DATA_MISSING'
        if row is not None and row['classification'] == 'COMPLETE':
            allowed, reason = True, 'REQUESTED_PRICE_CONTENT_COMPLETE'
        if purpose == 'DAILY_RESEARCH' and key[1] != 'day':
            allowed, reason = False, 'DAILY_REQUIREMENT_FREQUENCY_INVALID'
        if purpose == 'MINUTE_RESEARCH' and key[1] != '5min':
            allowed, reason = False, 'MINUTE_REQUIREMENT_FREQUENCY_INVALID'
        if purpose == 'PREDICTION' and (key[1] != 'day'
                or required.get('existing_model_input_validated') is not True):
            allowed, reason = False, 'EXISTING_MODEL_INPUT_VALIDATION_REQUIRED'
        if purpose == 'PAPER':
            # A diagnostic flag is not authority to approve an order or unhalt an account.
            allowed = False
            reason = required.get('existing_constraint_block') or 'EXISTING_PAPER_ENGINE_REQUIRED'
        results.append({'symbol': key[0], 'frequency': key[1], 'trading_day': key[2],
                        'diagnostic_allowed': allowed, 'reason': reason})
    return {**AUTHORITY, 'purpose': purpose, 'scope': results,
            'qualified_count': sum(r['diagnostic_allowed'] for r in results),
            'blocked_count': sum(not r['diagnostic_allowed'] for r in results),
            'production_admission_effect': 'NONE', 'execution_admitted': False}


def validate_quality(value: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError('QUALITY_PUBLICATION_OBJECT_REQUIRED')
    if (signed({k: v for k, v in value.items() if k != 'evidence_sha256'}) != value
            or any(type(value.get(k)) is not type(v) or value[k] != v
                   for k, v in AUTHORITY.items())
            or value.get('schema_version') != SCHEMA
            or value.get('production_admission_effect') != 'NONE'
            or value.get('execution_admitted') is not False
            or re.fullmatch('[a-f0-9]{40}', value.get('source_revision', '')) is None
            or re.fullmatch('[a-f0-9]{64}', value.get('universe_version', '')) is None):
        raise ValueError('QUALITY_PUBLICATION_AUTHORITY_OR_HASH_INVALID')
    at = datetime.fromisoformat(value['observed_at'])
    if at.tzinfo is None or now.tzinfo is None or at > now:
        raise ValueError('QUALITY_PUBLICATION_CLOCK_INVALID')
    cases = value['cases']
    if (not isinstance(cases, list) or len(cases) > 12_000
            or any(not isinstance(r, dict) for r in cases)):
        raise ValueError('QUALITY_PUBLICATION_BOUNDS_INVALID')
    issues = value.get('source_issues')
    if (not isinstance(issues, list) or len(issues) > 12_000
            or any(not isinstance(r, dict) or not isinstance(r.get('reason'), str)
                   or not r['reason'] or (r.get('symbol') is not None
                   and (not isinstance(r['symbol'], str)
                        or re.fullmatch('[0-9]{6}', r['symbol']) is None)) for r in issues)):
        raise ValueError('QUALITY_PUBLICATION_ISSUES_INVALID')
    keys = {(r['symbol'], r['frequency'], r['trading_day']) for r in cases}
    if len(keys) != len(cases):
        raise ValueError('QUALITY_PUBLICATION_DUPLICATE_CASE')
    if value.get('case_counts') != dict(Counter(r['classification'] for r in cases)):
        raise ValueError('QUALITY_PUBLICATION_COUNTS_INVALID')
    for row in cases:
        if date.fromisoformat(row['trading_day']).isoformat() != row['trading_day']:
            raise ValueError('QUALITY_PUBLICATION_CASE_DATE_INVALID')
        if (re.fullmatch('[0-9]{6}', row['symbol']) is None
                or row['frequency'] not in {'day', '5min'}
                or row['classification'] not in CLASSIFICATIONS
                or row['production_admission_effect'] != 'NONE'
                or row['execution_admitted'] is not False
                or row['historical_available_at'] is not None
                or row['historical_pit_certified'] is not False
                or row.get('universe_version') != value['universe_version']
                or re.fullmatch('[a-f0-9]{64}', row.get('task_snapshot_sha256', '')) is None
                or any(type(row[k]) is not type(v) or row[k] != v
                       for k, v in AUTHORITY.items() if k in row and k != 'classification')
                or row['coverage'] not in {'COMPLETE', 'PARTIAL', 'MISSING'}
                or row['market_state'] not in {'TRADING', 'SUSPENDED', 'UNKNOWN'}
                or not isinstance(row['reason'], str)
                or not isinstance(row['original_task_state'], str)
                or (row['original_error'] is not None
                    and not isinstance(row['original_error'], str))
                or row['retry_recommendation'] not in {
                    'DEFER_PENDING_APPROVAL', 'KEEP_EXISTING_RETRY'}
                or row['trading_day'] > now.astimezone(SHANGHAI).date().isoformat()):
            raise ValueError('QUALITY_PUBLICATION_CASE_INVALID')
        desired = datetime.fromisoformat(row['desired_end'])
        completed = (datetime.fromisoformat(row['completed_end'])
                     if row['completed_end'] is not None else None)
        observed = (datetime.fromisoformat(row['source_observed_at'])
                    if row['source_observed_at'] is not None else None)
        if (desired.tzinfo is None or desired > at
                or desired.astimezone(SHANGHAI).date().isoformat() != row['trading_day']
                or completed is not None and (completed.tzinfo is None or completed > desired)
                or observed is not None and (observed.tzinfo is None or observed > at)):
            raise ValueError('QUALITY_PUBLICATION_CASE_CLOCK_INVALID')
        if row['classification'] == 'COMPLETE' and (row['coverage'] != 'COMPLETE'
                or row['original_task_state'] != 'SUCCEEDED' or completed != desired):
            raise ValueError('QUALITY_PUBLICATION_COMPLETE_CONTRADICTION')
        if row['classification'] == 'SUSPENDED' and (row['coverage'] != 'MISSING'
                or row['market_state'] != 'SUSPENDED' or observed is None
                or not row['day_state_evidence'] or not row['issuer_start_evidence']):
            raise ValueError('QUALITY_PUBLICATION_SUSPENSION_EVIDENCE_REQUIRED')
        for key in ('day_state_evidence', 'issuer_start_evidence'):
            if (not isinstance(row[key], list) or any(not isinstance(h, str)
                    or re.fullmatch('[a-f0-9]{64}', h) is None for h in row[key])):
                raise ValueError('QUALITY_PUBLICATION_EVIDENCE_ID_INVALID')
        if row['proposed_recheck_at'] is not None:
            recheck = datetime.fromisoformat(row['proposed_recheck_at'])
            if (recheck.tzinfo is None or observed is None or recheck <= at
                    or recheck > min(at + timedelta(hours=6), observed + timedelta(hours=24))
                    or row['classification'] != 'SUSPENDED'):
                raise ValueError('QUALITY_PUBLICATION_RECHECK_INVALID')
    recent = value['recent_status']
    if (not isinstance(recent, dict) or type(recent.get('available')) is not bool
            or type(recent.get('content_verified_symbols')) is not int
            or recent['content_verified_symbols'] < 0):
        raise ValueError('QUALITY_RECENT_STATUS_SHAPE_INVALID')
    if recent['available']:
        observed = datetime.fromisoformat(recent['observed_at'])
        start = date.fromisoformat(recent['window_start'])
        end = date.fromisoformat(recent['window_end'])
        progress = recent['progress']
        if (observed.tzinfo is None or observed > at or start > end
                or end > at.astimezone(SHANGHAI).date()
                or any(type(progress[k]) is not int or progress[k] < 0 for k in (
                    'scope_symbols', 'completed_symbols', 'unresolved_symbols'))
                or progress['completed_symbols'] + progress['unresolved_symbols']
                != progress['scope_symbols']
                or not 0 <= recent['content_verified_symbols'] <= progress['completed_symbols']):
            raise ValueError('QUALITY_RECENT_STATUS_CLOCK_OR_COVERAGE_INVALID')
    return value
