"""Bounded rolling research windows on real sessions, never calendar placeholders."""

from __future__ import annotations

import re
from datetime import date
from hashlib import sha256
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.workflow import canonical

INTERVALS = {'OFF': 0, 'DAILY': 1, 'WEEKLY': 5, 'MONTHLY': 20}
CUSTOM_INTERVALS = (2, 5, 10, 20, 40, 60)


def default_policy() -> dict[str, Any]:
    return {'mode': 'OFF', 'custom_sessions': None, 'model': 'lightgbm',
            'train_sessions': 1260, 'valid_sessions': 126, 'test_sessions': 60,
            'seed': 20261026}


def validate_policy(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(default_policy()):
        raise ValueError('ROLLING_POLICY_FIELDS_INVALID')
    if value['mode'] not in (*INTERVALS, 'CUSTOM') or value['model'] not in (
            'linear', 'lightgbm'):
        raise ValueError('ROLLING_MODE_OR_MODEL_INVALID')
    if value['mode'] == 'CUSTOM':
        if type(value['custom_sessions']) is not int or value['custom_sessions'] not in (
                CUSTOM_INTERVALS):
            raise ValueError('ROLLING_CUSTOM_INTERVAL_NOT_ALLOWLISTED')
    elif value['custom_sessions'] is not None:
        raise ValueError('ROLLING_UNUSED_CUSTOM_INTERVAL')
    for key, choices in (('train_sessions', (252, 504, 756, 1260)),
                         ('valid_sessions', (60, 126, 252)), ('test_sessions', (20, 60, 126))):
        if type(value[key]) is not int or value[key] not in choices:
            raise ValueError('ROLLING_WINDOW_NOT_ALLOWLISTED')
    if type(value['seed']) is not int or not 0 <= value['seed'] <= 2**31-1:
        raise ValueError('ROLLING_SEED_INVALID')
    return dict(value)


def real_sessions(calendar: list[str], data_end: str) -> list[str]:
    if (not calendar or len(calendar) > 10_000 or calendar != sorted(set(calendar))
            or any(date.fromisoformat(d).isoformat() != d for d in calendar)
            or data_end not in calendar):
        raise ValueError('ROLLING_VERIFIED_CALENDAR_REQUIRED')
    return [d for d in calendar if d <= data_end]


def due(policy: dict[str, Any], calendar: list[str], data_end: str,
        last_attempt_day: str | None) -> bool:
    policy = validate_policy(policy)
    days = real_sessions(calendar, data_end)
    if policy['mode'] == 'OFF':
        return False
    if last_attempt_day is None:
        return True
    if last_attempt_day not in days:
        raise ValueError('ROLLING_ATTEMPT_OUTSIDE_CURRENT_CALENDAR')
    interval = (policy['custom_sessions'] if policy['mode'] == 'CUSTOM'
                else INTERVALS[policy['mode']])
    return bool(len(days)-1-days.index(last_attempt_day) >= interval)


def plan(policy: dict[str, Any], calendar: list[str], data_end: str, *,
         data_version_id: str, source_revision: str) -> dict[str, Any]:
    from qtrade.research.model_registry import checked_identity

    policy = validate_policy(policy)
    checked_identity(data_version_id)
    if re.fullmatch(r'[a-f0-9]{40}', source_revision) is None:
        raise ValueError('ROLLING_SOURCE_REVISION_REQUIRED')
    days = real_sessions(calendar, data_end)
    # Ref(open,-2)/Ref(open,-1)-1 is mature only two actual sessions later.
    end = len(days)-3
    segments: dict[str, list[str]] = {}
    for key in ('test', 'valid', 'train'):
        start = end-policy[key+'_sessions']+1
        if start < 0:
            raise ValueError('ROLLING_INSUFFICIENT_MATURE_HISTORY')
        segments[key] = [days[start], days[end]]
        end = start-3  # Two purged feature dates before the next segment.
    if segments['train'][0] < '2017-04-05':
        raise ValueError('ROLLING_ALPHA158_WARMUP_NOT_AVAILABLE')
    return signed({**AUTHORITY, 'schema_version': 'qtrade.rolling-plan.v1',
        'policy': policy, 'data_version_id': data_version_id, 'source_revision': source_revision,
        'calendar_sha256': sha256(canonical(days)).hexdigest(),
        'data_end': data_end, 'label': 'Ref($open,-2)/Ref($open,-1)-1',
        'label_horizon_sessions': 2, 'purge_sessions': 2, 'segments': segments,
        'last_mature_label_day': days[-3], 'reference_end': data_end,
        'candidate_activation': 'EXPLICIT_HUMAN_APPROVAL_REQUIRED',
        'historical_pit_certified': False, 'forward_return_observed': False})


def verify_plan(value: dict[str, Any], calendar: list[str]) -> dict[str, Any]:
    expected = plan(value['policy'], calendar, value['data_end'],
                    data_version_id=value['data_version_id'],
                    source_revision=value['source_revision'])
    if value != expected:
        raise ValueError('ROLLING_PLAN_BINDING_CHANGED')
    return expected
