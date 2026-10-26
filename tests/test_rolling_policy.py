from copy import deepcopy
from datetime import date, timedelta

import pytest

from qtrade.research.rolling_policy import default_policy, due, plan, validate_policy, verify_plan


@pytest.fixture
def days():
    start = date(2017, 4, 5)
    # Intentionally irregular synthetic sessions: cadence must count entries, not weekdays.
    return [(start+timedelta(days=i*2)).isoformat() for i in range(1600)]


def make(days, policy=None):
    return plan(policy or default_policy(), days, days[-4],
                data_version_id='a'*64, source_revision='b'*40)


def test_windows_exclude_placeholders_and_purge_both_label_boundaries(days):
    value = make(days)
    segments = value['segments']
    assert segments['test'][1] == days[-6]
    for key in ('train', 'valid', 'test'):
        left, right = segments[key]
        assert days.index(right)-days.index(left)+1 == value['policy'][key+'_sessions']
    for left, right in (('train', 'valid'), ('valid', 'test')):
        assert days.index(segments[left][1])+2 < days.index(segments[right][0])
    assert value['reference_end'] == days[-4]
    assert value['historical_pit_certified'] is False
    assert value['forward_return_observed'] is False
    assert verify_plan(value, days) == value


@pytest.mark.parametrize('mode,interval', [('DAILY', 1), ('WEEKLY', 5), ('MONTHLY', 20),
                                         ('CUSTOM', 10)])
def test_cadence_uses_last_attempt_and_never_calendar_days(days, mode, interval):
    policy = {**default_policy(), 'mode': mode,
              'custom_sessions': interval if mode == 'CUSTOM' else None}
    assert due(policy, days, days[-1], None)
    assert not due(policy, days, days[-1], days[-interval])
    assert due(policy, days, days[-1], days[-interval-1])
    assert not due(policy, days, days[-1], days[-1])
    with pytest.raises(ValueError, match='ATTEMPT_OUTSIDE'):
        due(policy, days, days[-3], days[-1])


def test_off_never_schedules_but_can_plan_operator_acceptance(days):
    assert not due(default_policy(), days, days[-1], None)
    assert make(days)['policy']['mode'] == 'OFF'


@pytest.mark.parametrize('change', [
    {'mode': 'AUTO_BEST'}, {'model': 'custom.py'}, {'seed': True}, {'train_sessions': True},
    {'test_sessions': 100000}, {'custom_sessions': 5},
    {'mode': 'CUSTOM', 'custom_sessions': 3}, {'extra': 'arbitrary'},
])
def test_only_allowlisted_policy_is_accepted(change):
    with pytest.raises(ValueError):
        validate_policy({**default_policy(), **change})


@pytest.mark.parametrize('damage', ['label', 'split', 'version', 'calendar'])
def test_plan_replay_binds_maturity_data_and_calendar(days, damage):
    value = deepcopy(make(days))
    if damage == 'label':
        value['last_mature_label_day'] = value['data_end']
    elif damage == 'split':
        value['segments']['train'][1] = value['segments']['valid'][0]
    elif damage == 'version':
        value['data_version_id'] = 'c'*64
    else:
        days = days[:100]+days[101:]
    with pytest.raises(ValueError, match='BINDING_CHANGED'):
        verify_plan(value, days)


def test_insufficient_history_and_unverified_calendar_fail_closed(days):
    with pytest.raises(ValueError, match='INSUFFICIENT'):
        make(days[:100])
    with pytest.raises(ValueError, match='CALENDAR'):
        make([*days, days[-1]])
