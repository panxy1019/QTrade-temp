from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from hashlib import sha256

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qtrade.api import unified_data
from qtrade.monitoring.data_freshness import signed
from qtrade.research.data_quality import assess_scope, classify_task
from scripts import build_data_quality_summary as builder
from scripts.publish_research_snapshot import quality_publication

NOW = datetime(2026, 10, 28, 2, 0, tzinfo=UTC)


def task(symbol='002827', frequency='day', day='2026-10-27', state='FAILED'):
    end = day + 'T07:00:00+00:00'
    return {'symbol': symbol, 'frequency': frequency, 'trading_day': day,
            'state': state, 'desired_end': end,
            'completed_end': end if state == 'SUCCEEDED' else None,
            'last_error': 'SOURCE_MISSING' if state == 'FAILED' else None}


def observation(flag='0', days=('2026-10-26', '2026-10-27')):
    return signed({'source': 'BaoStock raw daily status backfill', 'symbol': '002827',
                   'observed_at': NOW.isoformat(), 'historical_available_at': None,
                   'certified_at_decision': False,
                   'rows': [{'date': day, 'code': 'sz.002827', 'tradestatus': flag, 'isST': '0'}
                            for day in days]})


def issuer():
    return {'symbol': '002827', 'effective_start_date': '2026-10-26',
            'claim': 'ISSUER_ANNOUNCED_SUSPENSION_START', 'raw_sha256': 'a'*64,
            'observed_at': NOW.isoformat()}


def test_issuer_start_alone_never_infers_an_open_ended_suspension():
    assert classify_task(task(), [], [issuer()], now=NOW)['classification'] == 'UNKNOWN'
    result = classify_task(task(day='2026-10-28'), [observation()], [issuer()], now=NOW)
    assert result['classification'] == 'UNKNOWN'
    assert result['retry_recommendation'] == 'KEEP_EXISTING_RETRY'


def test_exact_vendor_day_and_issuer_start_explain_but_do_not_rewrite_failure():
    original = task()
    before = dict(original)
    result = classify_task(original, [observation()], [issuer()], now=NOW)
    assert result['classification'] == 'SUSPENDED'
    assert result['retry_recommendation'] == 'DEFER_PENDING_APPROVAL'
    assert result['production_admission_effect'] == 'NONE'
    assert result['execution_admitted'] is False
    assert result['historical_available_at'] is None
    assert original == before


@pytest.mark.parametrize('state', ['SUCCEEDED', 'FAILED'])
def test_suspension_price_conflict_is_unknown_and_never_suppresses_collection(state):
    row = task(state=state)
    row['completed_end'] = row['desired_end'] if state == 'SUCCEEDED' else '2026-10-27T01:55:00Z'
    result = classify_task(row, [observation()], [issuer()], now=NOW)
    assert result['classification'] == 'UNKNOWN'
    assert result['retry_recommendation'] == 'KEEP_EXISTING_RETRY'


def test_partial_minute_window_does_not_block_the_same_etfs_complete_daily_research():
    minute = task('512580', '5min')
    minute['completed_end'] = '2026-10-27T01:55:00+00:00'
    daily = task('512580', state='SUCCEEDED')
    cases = [classify_task(row, [], [], now=NOW) for row in [minute, daily]]
    assert cases[0]['classification'] == 'DATA_GAP'
    required = [{k: daily[k] for k in ['symbol', 'frequency', 'trading_day']}]
    result = assess_scope(cases, required, purpose='DAILY_RESEARCH')
    assert result['qualified_count'] == 1 and result['blocked_count'] == 0
    required[0]['frequency'] = '5min'
    assert assess_scope(cases, required, purpose='MINUTE_RESEARCH')['blocked_count'] == 1


def test_model_validation_and_paper_constraints_are_not_replaced_by_quality_labels():
    row = task('600036', state='SUCCEEDED')
    cases = [classify_task(row, [], [], now=NOW)]
    required = [{k: row[k] for k in ['symbol', 'frequency', 'trading_day']}]
    assert assess_scope(cases, required, purpose='PREDICTION')['blocked_count'] == 1
    required[0]['existing_model_input_validated'] = True
    assert assess_scope(cases, required, purpose='PREDICTION')['qualified_count'] == 1
    required[0]['existing_constraint_block'] = 'UNKNOWN_TRADING_CONSTRAINTS'
    result = assess_scope(cases, required, purpose='PAPER')
    assert result['blocked_count'] == 1 and result['execution_admitted'] is False
    assert result['scope'][0]['reason'] == 'UNKNOWN_TRADING_CONSTRAINTS'


def test_wrong_date_cannot_supply_current_model_inputs_or_market_status():
    case = classify_task(task(), [observation(days=('2026-10-26',))], [issuer()], now=NOW)
    assert case['classification'] == 'UNKNOWN'
    old = task('600036', day='2026-10-26', state='SUCCEEDED')
    cases = [classify_task(old, [], [], now=NOW)]
    need = [{'symbol': '600036', 'frequency': 'day', 'trading_day': '2026-10-27',
             'existing_model_input_validated': True}]
    assert assess_scope(cases, need, purpose='PREDICTION')['blocked_count'] == 1


def test_conflicting_day_flags_do_not_select_the_convenient_source():
    case = classify_task(task(), [observation('0'), observation('1')], [issuer()], now=NOW)
    assert case['classification'] == 'UNKNOWN'
    assert case['market_state'] == 'UNKNOWN'


@pytest.fixture
def quality_fixture(tmp_path, monkeypatch):
    def write(path, value, **kwargs):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    monkeypatch.setattr(builder, 'write_json_atomic', write)
    monkeypatch.setattr(builder, 'write_json_immutable', write)
    monkeypatch.setattr(builder, 'current_universe', lambda _: 'b'*64)
    database = tmp_path / 'queue.sqlite'
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE collection_tasks '
                     '(universe_version,symbol,frequency,trading_day,state,'
                     'desired_end,completed_end,last_error)')
        row = task(day='2026-10-27')
        conn.execute('INSERT INTO collection_tasks VALUES(?,?,?,?,?,?,?,?)',
                     ('b'*64, *tuple(row.values())))
    value = builder.build(tmp_path, database, revision='a'*40)
    return tmp_path, database, value


def test_optional_missing_status_does_not_prevent_scoped_quality_or_mutate_queue(quality_fixture):
    base, database, value = quality_fixture
    assert value['cases'][0]['classification'] == 'UNKNOWN'
    assert value['production_admission_effect'] == 'NONE'
    with sqlite3.connect(database) as conn:
        assert conn.execute('SELECT state,last_error FROM collection_tasks').fetchone() == (
            'FAILED', 'SOURCE_MISSING')
    assert (base / 'data-quality/public/quality.json').exists()


def test_optional_corrupt_summary_has_no_verified_fallback_or_market_authority(tmp_path):
    path = tmp_path / 'quality.json'
    path.write_text('{}')
    result = quality_publication(path)
    assert result['state'] == 'NO_VERIFIED_QUALITY_PUBLICATION'
    assert result['live_trading_enabled'] is False


def test_quality_api_has_independent_failure_scope_and_binds_public_revision(
    quality_fixture, monkeypatch,
):
    _, _, value = quality_fixture
    raw = json.dumps(value).encode()
    unified_data._load_quality.cache_clear()
    monkeypatch.setattr(unified_data, 'download', lambda name, limit: (raw, 'b'*40))
    app = FastAPI()
    app.include_router(unified_data.router)
    with TestClient(app) as client:
        response = client.get('/unified/quality')
        assert response.status_code == 200
        result = response.json()
        assert result['read_only'] is True and result['source']['commit_sha'] == 'b'*40
        assert result['source']['sha256'] == sha256(raw).hexdigest()
        assert result['publication']['production_admission_effect'] == 'NONE'
        assert client.post('/unified/quality', json={}).status_code == 405
    unified_data._load_quality.cache_clear()
