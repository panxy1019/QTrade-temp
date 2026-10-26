from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

import pytest
from test_etf_research_contract import OBSERVED, REVISION, environment, freeze  # noqa: F401

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research import etf_research_contract as contract
from qtrade.research.etf_shadow_publication import (
    LIMITATIONS,
    SCHEMA,
    STRATEGY,
    validate_etf_shadow_publication,
)

NOW = OBSERVED+timedelta(minutes=1)


def resign(value):
    return signed({k: v for k, v in value.items() if k != 'evidence_sha256'})


@pytest.fixture
def publication(environment):  # noqa: F811
    _, calendar, archive = environment
    source, path, target = freeze(environment)
    observation = contract.observe_etf_shadow(path, source, archive(27),
                                               calendar=calendar, now=OBSERVED)
    return signed({**AUTHORITY, 'schema_version': SCHEMA, 'security_type': 'ETF',
        'mode': 'SHADOW', 'strategy_id': STRATEGY, 'contract_id': target['bindings']['contract_id'],
        'contract': target['contract'], 'generated_at': NOW.isoformat(),
        'heartbeat_at': NOW.isoformat(), 'source_revision': REVISION, 'state': 'RUNNING',
        'entries': [{'target': target, 'observation': observation}], 'errors': [],
        'limitations': LIMITATIONS.copy(), 'execution_admitted': False,
        'paper_admission': {'state': 'BLOCKED', 'reasons': contract.BLOCKERS.copy()},
        'orders': [], 'fills': [], 'holdings': [], 'cash': None, 'net_asset_value': None,
        'forward_return': None})


def test_actual_module_target_and_observation_validate_without_any_execution(publication):
    before = deepcopy(publication)
    assert validate_etf_shadow_publication(publication, now=NOW) == publication
    assert publication == before
    assert all(publication[key] == [] for key in ('orders', 'fills', 'holdings'))
    assert all(publication[key] is None for key in ('cash', 'net_asset_value', 'forward_return'))


def test_pending_and_empty_shadow_publication_preserve_unknown_observations(publication):
    publication['entries'][0]['observation'] = None
    assert validate_etf_shadow_publication(resign(publication), now=NOW)['entries'][0][
        'observation'] is None
    publication['entries'] = []
    assert validate_etf_shadow_publication(resign(publication), now=NOW)['entries'] == []


def test_archived_publication_remains_valid_without_a_false_freshness_field(publication):
    value = validate_etf_shadow_publication(publication, now=NOW+timedelta(days=1))
    assert value['heartbeat_at'] == NOW.isoformat()
    assert 'current_input' not in value


def test_historical_targets_keep_their_actual_implementation_revision(publication):
    publication['source_revision'] = 'd'*40
    value = validate_etf_shadow_publication(resign(publication), now=NOW)
    assert value['entries'][0]['target']['bindings']['implementation_revision'] == REVISION


@pytest.mark.parametrize('state,errors', [('DEGRADED', ['ETF_SOURCE_UNAVAILABLE']),
                                        ('STOPPED', []), ('STOPPED', ['ETF_STOPPED_AFTER_ERROR'])])
def test_safe_service_states_are_not_execution_or_performance_claims(publication, state, errors):
    publication.update(state=state, errors=errors)
    assert validate_etf_shadow_publication(resign(publication), now=NOW)['state'] == state


@pytest.mark.parametrize('change', ['numeric_authority', 'live', 'paper', 'execution',
    'orders', 'fills', 'holdings', 'cash', 'nav', 'return', 'paper_ready', 'paper_reason',
    'security_type', 'stock_schema', 'strategy', 'revision', 'contract_id', 'contract_fee',
    'contract_risk_number', 'contract_risk_nonnumber', 'limits', 'errors_path', 'errors_count',
    'errors_object', 'errors_state', 'state_without_errors', 'future_generated',
    'future_heartbeat', 'heartbeat_before_generated', 'naive_generated', 'extra_current_flag',
    'duplicate_target', 'entry_bound', 'entry_extra_field', 'signature', 'size'])
def test_invalid_resigned_service_publications_fail_closed(publication, change):
    value = deepcopy(publication)
    if change == 'numeric_authority':
        value['point_in_time'] = 0
    elif change == 'live':
        value['live_trading_enabled'] = True
    elif change == 'paper':
        value['mode'] = 'PAPER'
    elif change == 'execution':
        value['execution_admitted'] = True
    elif change in ('orders', 'fills', 'holdings'):
        value[change] = [{'instrument': 'SH510300'}]
    elif change in ('cash', 'nav', 'return'):
        value[{'cash': 'cash', 'nav': 'net_asset_value', 'return': 'forward_return'}[change]] = '0'
    elif change == 'paper_ready':
        value['paper_admission']['state'] = 'READY'
    elif change == 'paper_reason':
        value['paper_admission']['reasons'] = []
    elif change == 'security_type':
        value['security_type'] = 'STOCK'
    elif change == 'stock_schema':
        value['schema_version'] = 'qtrade.forward-service.v1'
    elif change == 'strategy':
        value['strategy_id'] = 'momentum20'
    elif change == 'revision':
        value['source_revision'] = 'short'
    elif change == 'contract_id':
        value['contract_id'] = '0'*64
    elif change == 'contract_fee':
        value['contract']['execution']['fee_policy'] = 'stock-fee-default'
        value['contract_id'] = signed(value['contract'])['evidence_sha256']
    elif change == 'contract_risk_number':
        value['contract']['portfolio']['risk_degree'] = .95
        value['contract_id'] = signed(value['contract'])['evidence_sha256']
    elif change == 'contract_risk_nonnumber':
        value['contract']['portfolio']['risk_degree'] = 'not-a-number'
        value['contract_id'] = signed(value['contract'])['evidence_sha256']
    elif change == 'limits':
        value['limitations'].remove('FULL_PIT_NOT_CERTIFIED')
    elif change == 'errors_path':
        value.update(state='DEGRADED', errors=['/private/source/path'])
    elif change == 'errors_count':
        value.update(state='DEGRADED', errors=['ETF_ERROR']*33)
    elif change == 'errors_object':
        value.update(state='DEGRADED', errors=[{'code': 'ETF_ERROR'}])
    elif change == 'errors_state':
        value['errors'] = ['ETF_ERROR']
    elif change == 'state_without_errors':
        value['state'] = 'DEGRADED'
    elif change == 'future_generated':
        value['generated_at'] = (NOW+timedelta(seconds=1)).isoformat()
    elif change == 'future_heartbeat':
        value['heartbeat_at'] = (NOW+timedelta(seconds=1)).isoformat()
    elif change == 'heartbeat_before_generated':
        value['heartbeat_at'] = (NOW-timedelta(seconds=1)).isoformat()
    elif change == 'naive_generated':
        value['generated_at'] = NOW.replace(tzinfo=None).isoformat()
    elif change == 'extra_current_flag':
        value['current_input'] = True
    elif change == 'duplicate_target':
        value['entries'].append(deepcopy(value['entries'][0]))
    elif change == 'entry_bound':
        value['entries'] *= 31
    elif change == 'entry_extra_field':
        value['entries'][0]['forward_return'] = .1
    elif change == 'signature':
        value['evidence_sha256'] = '0'*64
        with pytest.raises(ValueError):
            validate_etf_shadow_publication(value, now=NOW)
        return
    else:
        value['padding'] = 'x'*1_000_000
    with pytest.raises(ValueError):
        validate_etf_shadow_publication(resign(value), now=NOW)


def test_event_processed_after_publication_clock_is_rejected(publication):
    publication['generated_at'] = (OBSERVED-timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError):
        validate_etf_shadow_publication(resign(publication), now=NOW)


def test_other_canonical_contract_cannot_be_mixed_into_service_entries(publication):
    other = contract.etf_contract(contract.EtfAllocationPolicy(top_k=1))
    publication.update(contract=other, contract_id=signed(other)['evidence_sha256'])
    with pytest.raises(ValueError, match='TARGET_CONTEXT_INVALID'):
        validate_etf_shadow_publication(resign(publication), now=NOW)


def test_nan_or_naive_now_never_reaches_publication(publication):
    publication['cash'] = float('nan')
    with pytest.raises(ValueError):
        validate_etf_shadow_publication(resign(publication), now=NOW)
    with pytest.raises(ValueError, match='AWARE_CLOCK_REQUIRED'):
        validate_etf_shadow_publication({}, now=NOW.replace(tzinfo=None))
