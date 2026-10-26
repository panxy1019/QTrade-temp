from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_etf_history_comparison import inputs  # noqa: F401
from test_etf_history_publication import publication, reseal  # noqa: F401

from qtrade.api import online_research as api
from qtrade.research.etf_history_publication import MAXIMUM_BYTES
from scripts.publish_research_snapshot import etf_history_publication


@pytest.fixture
def channel(publication, monkeypatch):  # noqa: F811
    _, value = publication
    reads = []

    def read(name, *, maximum):
        reads.append((name, maximum))
        return value

    monkeypatch.setattr(api, "read_public", read)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        yield client, value, reads


def test_read_only_public_history_requires_no_login_and_preserves_full_artifacts(channel):
    client, value, reads = channel
    original = deepcopy(value)
    response = client.get("/research/online/etf-history")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["publication_available"] is True and result["comparison"] == value
    assert len(result["comparison"]["artifacts"]) == 6
    assert result["comparison"]["raw_source_replay_performed_by_public_validator"] is False
    assert reads == [("etf-history.json", 3_000_000)]
    assert value == original
    assert client.post("/research/online/etf-history", json={}).status_code == 405


@pytest.mark.parametrize("damage", ["missing", "execution", "ledger", "nan"])
def test_invalid_history_is_unavailable_without_fallback_metrics(channel, damage):
    client, value, _ = channel
    if damage == "missing":
        value.clear()
    elif damage == "execution":
        value["report"]["execution_admitted"] = True
        value.update(reseal(value))
    elif damage == "ledger":
        artifact = next(document for name, document in value["artifacts"].items()
                        if name.endswith("ledger.json"))
        artifact["records"][-1]["net_asset_value"] = "99999999"
        value.update(reseal(value))
    else:
        value["report"]["strategies"]["positive20_top3_daily"]["total_return"] = "NaN"
        value.update(reseal(value))
    response = client.get("/research/online/etf-history")
    assert response.status_code == 200
    assert response.json()["publication_available"] is False
    assert response.json()["comparison"] is None


def test_optional_publisher_checks_bounds_missing_and_invalid_history(publication, tmp_path):  # noqa: F811
    _, value = publication
    path = tmp_path/"history.json"
    path.write_text(json.dumps(value))
    assert etf_history_publication(path) == value
    value["report"]["paper_admission"] = "ADMITTED"
    path.write_text(json.dumps(reseal(value)))
    assert etf_history_publication(path)["state"] == "NO_VERIFIED_ETF_HISTORY_PUBLICATION"
    path.write_bytes(b" "*(MAXIMUM_BYTES+1))
    assert etf_history_publication(path)["state"] == "NO_VERIFIED_ETF_HISTORY_PUBLICATION"
    assert etf_history_publication(tmp_path/"absent.json")["state"] == (
        "NO_VERIFIED_ETF_HISTORY_PUBLICATION")


def test_actual_serverless_history_route_without_cluster_scripts(publication, tmp_path):  # noqa: F811
    _, value = publication
    path = tmp_path/"history.json"
    path.write_text(json.dumps(value))
    script = '''
import importlib.abc, json, sys
class ExcludeScripts(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "scripts" or fullname.startswith("scripts."):
            raise ModuleNotFoundError("DEPLOY_EXCLUDED_SCRIPT_IMPORT:"+fullname)
sys.meta_path.insert(0, ExcludeScripts())
from fastapi.testclient import TestClient
from qtrade.api.vercel import app
from qtrade.api import online_research as api
with open(sys.argv[1]) as stream:
    value = json.load(stream)
api.read_public = lambda *args, **kwargs: value
with TestClient(app) as client:
    response = client.get("/api?route=/research/online/etf-history")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["publication_available"] is True, result
    assert result["comparison"] == value
assert not any(name == "scripts" or name.startswith("scripts.") for name in sys.modules)
print("SERVERLESS_ETF_HISTORY_ROUTE_VERIFIED_WITHOUT_SCRIPTS")
'''
    process = subprocess.run([sys.executable, "-c", script, str(path)],
        env={k: v for k, v in os.environ.items() if not k.startswith("QTRADE_RESEARCH_")},
        capture_output=True, text=True, timeout=30, check=False)
    assert process.returncode == 0, process.stdout+process.stderr
    assert "SERVERLESS_ETF_HISTORY_ROUTE_VERIFIED_WITHOUT_SCRIPTS" in process.stdout
