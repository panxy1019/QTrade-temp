from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_etf_research_contract import FROZEN, environment  # noqa: F401
from test_etf_shadow_publication import NOW, publication, resign  # noqa: F401

from qtrade.api import online_research as api
from qtrade.research import etf_shadow_publication as validator
from scripts.publish_research_snapshot import etf_shadow_publication


@pytest.fixture
def channel(publication, monkeypatch):  # noqa: F811
    clock = [NOW]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz or UTC)

    monkeypatch.setattr(api, 'datetime', Clock)
    monkeypatch.setattr(validator, 'datetime', Clock)
    monkeypatch.setattr(api, 'research_clock', lambda: '2026-10-26')
    reads = []

    def read(name, *, maximum):
        reads.append((name, maximum))
        return resign(publication)

    monkeypatch.setattr(api, 'read_public', read)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        yield client, publication, clock, reads


def test_public_etf_channel_retains_observed_receipts_without_execution(channel):
    client, _, _, reads = channel
    response = client.get('/research/online/etf-forward')
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    value = response.json()
    assert value['publication_available'] and value['service_fresh']
    assert value['current_target'] is False
    assert value['publication']['entries'][0]['observation']['state'] == 'SHADOW_OBSERVED'
    assert value['publication']['forward_return'] is None
    assert reads == [('etf-forward.json', 1_000_000)]


def test_current_target_requires_fresh_heartbeat_and_still_future_open(channel):
    client, value, clock, _ = channel
    value['entries'][0]['observation'] = None
    clock[0] = FROZEN+timedelta(minutes=1)
    value['generated_at'] = value['heartbeat_at'] = clock[0].isoformat()
    assert client.get('/research/online/etf-forward').json()['current_target'] is True
    clock[0] += timedelta(minutes=13)
    stale = client.get('/research/online/etf-forward').json()
    assert stale['publication_available'] is True
    assert stale['service_fresh'] is False and stale['current_target'] is False
    assert stale['publication']['entries']


@pytest.mark.parametrize('change', ['stopped', 'missing', 'execution'])
def test_stopped_or_invalid_etf_channel_cannot_advertise_current_targets(channel, change):
    client, value, _, _ = channel
    if change == 'stopped':
        value['state'] = 'STOPPED'
    elif change == 'missing':
        value.clear()
    else:
        value['orders'] = [{'symbol': 'SH510300'}]
    result = client.get('/research/online/etf-forward').json()
    assert result['service_fresh'] is False and result['current_target'] is False
    assert result['publication_available'] is (change == 'stopped')


def test_publisher_carries_only_valid_bounded_etf_shadow_bytes(channel, tmp_path):
    _, value, _, _ = channel
    path = tmp_path/'etf-forward.json'
    path.write_text(json.dumps(resign(value)))
    assert etf_shadow_publication(path) == resign(value)
    value['cash'] = '100'
    path.write_text(json.dumps(resign(value)))
    assert etf_shadow_publication(path)['state'] == 'NO_VERIFIED_ETF_SHADOW_PUBLICATION'
    path.write_bytes(b' '*1_000_001)
    assert etf_shadow_publication(path)['state'] == 'NO_VERIFIED_ETF_SHADOW_PUBLICATION'
    assert etf_shadow_publication(tmp_path/'missing.json')['state'] == (
        'NO_VERIFIED_ETF_SHADOW_PUBLICATION')


def test_serverless_route_loads_and_validates_when_cluster_scripts_are_excluded(publication, tmp_path):  # noqa: F811
    path = tmp_path/'public.json'
    path.write_text(json.dumps(publication))
    code = '''
import importlib.abc, json, sys
from datetime import datetime
class ExcludeClusterScripts(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'scripts' or fullname.startswith('scripts.'):
            raise ModuleNotFoundError('Excluded from Vercel bundle: '+fullname)
sys.meta_path.insert(0, ExcludeClusterScripts())
from fastapi.testclient import TestClient
from qtrade.api.vercel import app
from qtrade.api import online_research as api
with open(sys.argv[1]) as stream:
    publication = json.load(stream)
class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.fromisoformat(publication['heartbeat_at']).astimezone(tz)
api.datetime = Clock
api.read_public = lambda *args, **kwargs: publication
api.research_clock = lambda: '2026-10-27'
with TestClient(app) as client:
    response = client.get('/api?route=/research/online/etf-forward')
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['publication_available'] and result['service_fresh'], result
    assert result['publication'] == publication
assert not any(name == 'scripts' or name.startswith('scripts.') for name in sys.modules)
print('SERVERLESS_ETF_ROUTE_VERIFIED_WITHOUT_CLUSTER_SCRIPTS')
'''
    process = subprocess.run([sys.executable, '-c', code, str(path)],
        env={k: v for k, v in os.environ.items() if not k.startswith('QTRADE_RESEARCH_')},
        capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stdout+process.stderr
    assert 'SERVERLESS_ETF_ROUTE_VERIFIED_WITHOUT_CLUSTER_SCRIPTS' in process.stdout
