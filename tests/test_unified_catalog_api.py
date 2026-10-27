import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha1, sha256

import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient

from qtrade.api import unified_data as api
from qtrade.instruments.observed import build_observed_master
from qtrade.monitoring.data_freshness import AUTHORITY, signed

REVISION = 'c'*40


@pytest.fixture(autouse=True)
def clear_catalog_cache():
    api._load.cache_clear()
    yield
    api._load.cache_clear()


@pytest.fixture
def publication():
    now = datetime.now(UTC)
    master = build_observed_master(
        vendor_rows=[{"code": "sh.600036", "code_name": "招商银行", "type": "1", "status": "1"},
                     {"code": "sz.000002", "type": "1", "status": "0"}],
        vendor_sha256="a" * 64, source_groups=[{
            "source_id": "daily1000", "role": "legacy", "sha256": "b" * 64,
            "rows": [{"symbol": "600036", "security_type": "STOCK"}],
        }], observed_at=now, target_count=1,
    )
    for row in master["securities"]:
        row["frequencies"] = {freq: {"planned": row["in_target"],
                                     "state": "PLANNED" if row["in_target"] else "NOT_PLANNED"}
                              for freq in ("day", "5min")}
    master = signed({**{k: v for k, v in master.items() if k != "evidence_sha256"},
                     "frequency_counts": {freq: {"PLANNED": 1} for freq in ("day", "5min")}})
    return signed({**AUTHORITY, "schema_version": "qtrade.unified-catalog.v1",
                   "built_at": now.isoformat(), "master": master})


def test_unified_query_is_bounded_filtered_readonly_and_preserves_inactive(
    publication, monkeypatch,
):
    api._load.cache_clear()
    monkeypatch.setattr(api, "download", lambda name, limit: (
        json.dumps(publication).encode(), REVISION))
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        summary = client.get('/unified/summary')
        assert summary.status_code == 200 and summary.headers["cache-control"] == "no-store"
        assert "securities" not in summary.json()["master"]
        rows = client.get('/unified/securities?query=招商&frequency=5min').json()
        assert rows["total"] == 1 and rows["rows"][0]["instrument_id"] == "SSE.600036"
        assert rows["read_only"] and rows["point_in_time"] is False
        assert client.get('/unified/securities?target_only=false').json()["total"] == 2
        assert client.get('/unified/securities?quality=QUARANTINED').json()["total"] == 0
        assert client.get('/unified/securities?limit=101').status_code == 422
        assert client.get('/unified/securities?offset=-1').status_code == 422
        assert client.get('/unified/securities?frequency=1min').status_code == 422
        assert client.post('/unified/securities', json={}).status_code == 405
    api._load.cache_clear()


@pytest.mark.parametrize("change", ["hash", "authority", "identity", "count", "available", "clock"])
def test_unified_catalog_rejects_inconsistent_evidence_even_if_rehashed(publication, change):
    value = deepcopy(publication)
    row = value["master"]["securities"][0]
    if change == "hash":
        value["master"]["target_actual"] += 1
        with pytest.raises(ValueError):
            api.validate_publication(value)
        return
    if change == "authority":
        value["point_in_time"] = True
    if change == "identity":
        row["exchange"] = "BAD"
    if change == "count":
        value["master"]["frequency_counts"]["day"]["PLANNED"] = 2
    if change == "available":
        row["available_at"] = "2016-01-01"
    if change == "clock":
        value["built_at"] = "2099-01-01T00:00:00+00:00"
    value["master"] = signed({k: v for k, v in value["master"].items() if k != "evidence_sha256"})
    value = signed({k: v for k, v in value.items() if k != "evidence_sha256"})
    with pytest.raises(ValueError):
        api.validate_publication(value)


def source_identity(raw, revision=REVISION):
    return {'commit_sha': revision,
            'git_blob_sha1': sha1(b'blob '+str(len(raw)).encode('ascii')+b'\0'+raw,
                                  usedforsecurity=False).hexdigest(),
            'sha256': sha256(raw).hexdigest(), 'bytes': len(raw)}


def test_summary_source_binds_exact_download_bytes_without_reencoding(publication, monkeypatch):
    compact = json.dumps(publication, ensure_ascii=False, separators=(',', ':')).encode()
    pretty = (json.dumps(publication, ensure_ascii=True, indent=4)+'\n').encode()
    assert json.loads(compact) == json.loads(pretty) and compact != pretty
    sources = []
    for raw in (compact, pretty):
        api._load.cache_clear()
        requests = []
        def download(name, limit, *, raw=raw, requests=requests):
            requests.append((name, limit))
            return raw, REVISION
        monkeypatch.setattr(api, 'download', download)
        response = Response()
        result = api.summary(response)
        assert result['catalog_source'] == source_identity(raw)
        assert result['evidence_sha256'] == publication['evidence_sha256']
        assert result['master'] == {key: value for key, value in publication['master'].items()
                                    if key != 'securities'}
        assert response.headers['cache-control'] == 'no-store'
        assert api.catalog() == publication
        assert requests == [('unified.json', 12_000_000)]
        sources.append(result['catalog_source'])
    assert sources[0]['git_blob_sha1'] != sources[1]['git_blob_sha1']
    assert sources[0]['sha256'] != sources[1]['sha256']


def test_minute_cache_keeps_document_and_raw_metadata_paired(publication, monkeypatch):
    clock = [datetime.now(UTC).replace(second=10, microsecond=0)+timedelta(minutes=1)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz or UTC)
    monkeypatch.setattr(api, 'datetime', Clock)
    first_raw = json.dumps(publication, indent=2).encode()
    newer = signed({**{key: value for key, value in publication.items()
                      if key != 'evidence_sha256'}, 'built_at': clock[0].isoformat()})
    second_raw = json.dumps(newer, separators=(',', ':')).encode()
    available = [(first_raw, REVISION)]
    requests = []
    def download(name, limit):
        requests.append((name, limit))
        return available[0]
    monkeypatch.setattr(api, 'download', download)
    first = api.summary(Response())
    available[0] = second_raw, 'd'*40
    clock[0] += timedelta(seconds=20)
    assert api.catalog() == publication
    cached = api.summary(Response())
    assert cached['evidence_sha256'] == first['evidence_sha256']
    assert cached['catalog_source'] == source_identity(first_raw)
    assert len(requests) == 1
    clock[0] += timedelta(minutes=1)
    refreshed = api.summary(Response())
    assert api.catalog() == newer
    assert refreshed['evidence_sha256'] == newer['evidence_sha256']
    assert refreshed['catalog_source'] == source_identity(second_raw, 'd'*40)
    assert len(requests) == 2


def test_source_claims_in_document_or_returned_summary_cannot_poison_provenance(
    publication, monkeypatch,
):
    forged = {'commit_sha': 'f'*40, 'git_blob_sha1': 'a'*40, 'sha256': 'b'*64, 'bytes': 1}
    value = signed({**{key: item for key, item in publication.items()
                      if key != 'evidence_sha256'}, 'catalog_source': forged})
    raw = json.dumps(value).encode()
    monkeypatch.setattr(api, 'download', lambda name, limit: (raw, REVISION))
    result = api.summary(Response())
    assert result['catalog_source'] == source_identity(raw)
    assert result['catalog_source'] != forged
    result['catalog_source'].update(forged)
    assert api.summary(Response())['catalog_source'] == source_identity(raw)


def test_invalid_raw_cannot_create_source_proof_or_poison_next_cache_load(publication, monkeypatch):
    invalid = deepcopy(publication)
    invalid['master']['target_actual'] += 1
    available = [(json.dumps(invalid).encode(), REVISION)]
    calls = []
    def download(name, limit):
        calls.append(name)
        return available[0]
    monkeypatch.setattr(api, 'download', download)
    with pytest.raises(HTTPException) as caught:
        api.summary(Response())
    assert caught.value.status_code == 503
    valid = json.dumps(publication).encode()
    available[0] = valid, REVISION
    assert api.summary(Response())['catalog_source'] == source_identity(valid)
    assert len(calls) == 2
