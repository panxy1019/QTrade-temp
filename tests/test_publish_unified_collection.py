import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_unified_catalog_api import publication as catalog_fixture

from qtrade.api import unified_data as api
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts import publish_unified_collection as publisher
from scripts.unified_store import SCHEMA

publication = catalog_fixture


@pytest.fixture
def source(tmp_path, publication, monkeypatch):
    source_file = tmp_path / 'unified-data/catalog/latest.json'
    source_file.parent.mkdir(parents=True)
    source_file.write_text(json.dumps(publication['master']))
    identifier = 'b' * 32
    manifest = signed({**AUTHORITY, 'schema_version': SCHEMA,
                       'spec': {'frequency': 'day', 'universe_version': publication['master'][
                           'universe_version']},
                       'outcomes': {'600036': {'state': 'SUCCEEDED', 'bar_count': 1,
                                              'start': '2026-10-26T15:00:00+08:00',
                                              'end': '2026-10-26T15:00:00+08:00',
                                              'missing_amount_bars': 0}}})
    path = tmp_path / 'unified-data/lake/batches' / (identifier + '.json')
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(manifest))
    progress = {**AUTHORITY, 'universe_version': publication['master']['universe_version'],
                'tasks': [{'symbol': '600036', 'frequency': 'day', 'state': 'SUCCEEDED',
                           'last_success_batch': identifier,
                           'desired_end': '2026-10-26T07:00:00+00:00',
                           'completed_end': '2026-10-26T07:00:00+00:00', 'last_error': None,
                           'next_attempt_at': '2026-10-26T09:00:00+00:00'}]}

    def write(path, value, **kwargs):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    monkeypatch.setattr(publisher, 'write_json_atomic', write)
    return tmp_path, progress, source_file


def test_projection_keeps_historical_identity_and_exposes_current_scope(source, monkeypatch):
    root, progress, baseline = source
    original = baseline.read_bytes()
    value = publisher.publish_collection(root, progress)
    assert baseline.read_bytes() == original
    assert value['master']['incremental_counts'] == {
        'day': {'CONTENT_COVERAGE_VERIFIED': 1}, '5min': {'PLANNED': 1},
    }
    target = next(r for r in value['master']['securities'] if r['in_target'])
    assert target['incremental']['day']['bar_count'] == 1
    assert target['frequencies']['day']['state'] == 'PLANNED'
    assert 'evidence_sha256' not in value['master']['collection']
    api.validate_publication(value)
    monkeypatch.setattr(api, 'catalog', lambda: value)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        for coverage, quality, expected in [('historical', 'PLANNED', 1),
                                             ('incremental', 'PLANNED', 0),
                                             ('incremental', 'CONTENT_COVERAGE_VERIFIED', 1)]:
            url = f'/unified/securities?coverage={coverage}&quality={quality}'
            assert client.get(url).json()['total'] == expected


def test_publication_rejects_ledger_drift_and_false_certification(source):
    root, progress, _ = source
    changed = deepcopy(progress)
    changed['tasks'][0]['completed_end'] = '2026-10-26T06:55:00+00:00'
    with pytest.raises(ValueError, match='BINDING'):
        publisher.publish_collection(root, changed)
    changed = deepcopy(progress)
    changed['point_in_time'] = True
    with pytest.raises(ValueError, match='AUTHORITY'):
        publisher.publish_collection(root, changed)
    value = publisher.publish_collection(root, progress)
    target = next(r for r in value['master']['securities'] if r['in_target'])
    target['incremental']['day']['historical_pit_certified'] = True
    value['master'] = signed({k: v for k, v in value['master'].items() if k != 'evidence_sha256'})
    value = signed({k: v for k, v in value.items() if k != 'evidence_sha256'})
    with pytest.raises(ValueError, match='STATE'):
        api.validate_publication(value)


def test_collection_clock_becomes_stale_even_if_old_counts_stay_positive(source):
    root, progress, _ = source
    value = publisher.publish_collection(root, progress)
    value['built_at'] = '2026-10-26T09:00:00+00:00'
    now = datetime(2026, 10, 26, 9, 1, tzinfo=UTC)
    assert api.collection_freshness(value, now=now)['day']['status'] == 'FRESH'
    assert api.collection_freshness(value, now=now)['5min']['status'] == 'PARTIAL'
    assert api.collection_freshness(value, now=now.replace(hour=10))['day'][
        'status'] == 'STALE_PUBLICATION'
    now = now.replace(day=27)
    assert api.collection_freshness(value, now=now)['day']['ready_symbols'] == 0
