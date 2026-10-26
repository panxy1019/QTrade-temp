import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qtrade.api import research_workflow as api
from qtrade.research.pit_repair import causal_intervals, historical_blocks, known_state
from qtrade.research.workflow import build_config, default_spec, digest, validate_spec
from qtrade.research.workflow_queue import GitQueue, seal, unseal


def test_model_configs_are_allowlisted_and_fixed_exchange_is_preserved():
    baseline = json.loads(Path("configs/qlib_daily_baseline.json").read_text())
    linear = build_config(default_spec("linear"), baseline)
    tree = build_config(default_spec(), baseline)
    assert linear["model"]["class"] == "LinearModel"
    assert linear["fit"] == {}
    assert tree["model"]["class"] == "LGBModel"
    assert tree["exchange"]["volume_threshold"] == ["cum", "$capacity"]
    assert linear["infer_processors"] == tree["infer_processors"]
    assert tree["universe_policy"] == "supported_a_share"
    assert linear["source_constraint_policy"] == tree["source_constraint_policy"]
    assert baseline["name"] == "alpha158-lightgbm-daily-v1"


@pytest.mark.parametrize("field,value", [
    ("model", "os.system"), ("dataset_id", "../../secrets"),
    ("features", "eval"), ("name", "$(touch bad)"), ("topk", True),
    ("risk_degree", float("nan")), ("hold_thresh", 0),
    ("buy_cost_bps", -1), ("universe_policy", "future_best"),
])
def test_dangerous_or_invalid_spec_never_reaches_runner(field, value):
    spec = deepcopy(default_spec())
    spec[field] = value
    with pytest.raises(ValueError):
        validate_spec(spec)


def test_unknown_fields_and_labels_crossing_split_are_rejected():
    spec = deepcopy(default_spec())
    spec["command"] = "whoami"
    with pytest.raises(ValueError, match="FIELDS"):
        validate_spec(spec)
    del spec["command"]
    spec["segments"]["train"][1] = "2022-01-03"
    with pytest.raises(ValueError, match="PURGE"):
        validate_spec(spec)


def test_source_snapshot_spec_allows_only_a_content_hash():
    spec = default_spec()
    spec["source_snapshot_sha256"] = "a" * 64
    assert validate_spec(spec)["source_snapshot_sha256"] == "a" * 64
    for value in ("../../status", "main", None, True, "a" * 63):
        spec["source_snapshot_sha256"] = value
        with pytest.raises(ValueError, match="SOURCE_SNAPSHOT"):
            validate_spec(spec)


def test_queue_seal_detects_spec_or_cancel_tampering():
    key = "k" * 40
    original = {"spec": default_spec(), "job_id": "a" * 32}
    envelope = seal(original, key)
    assert unseal(envelope, key) == original
    envelope["spec"] = {**envelope["spec"], "topk": 99}
    with pytest.raises(ValueError, match="SIGNATURE"):
        unseal(envelope, key)


def test_queue_client_binds_the_pinned_vendor_without_system_ssh_fallback(monkeypatch):
    queue = GitQueue("test-only-key", "ssh-ed25519 test-only-host")
    vendor = object()
    monkeypatch.setattr(queue, "_vendor", lambda: vendor)
    client = queue._client()
    assert client.ssh_vendor is vendor
    assert (client.host, client.port, client.username) == ("ssh.github.com", 443, "git")


def test_malformed_queue_file_does_not_block_other_valid_jobs(monkeypatch):
    queue = GitQueue("test-only-key", "ssh-ed25519 test-only-host")

    def checkout(path):
        (path / "requests").mkdir(parents=True)
        for name, value in (("a", "[]"), ("b", "invalid json"), ("c", '{"valid":true}')):
            (path / "requests" / (name * 32 + ".json")).write_text(value)
        return type("Repo", (), {"close": lambda self: None})()

    monkeypatch.setattr(queue, "checkout", checkout)
    assert queue.read() == {"requests/" + "c" * 32: {"valid": True}}


def test_future_liquidity_cannot_rewrite_earlier_admission_and_star_is_excluded():
    calendar = ["2020-01-02", "2020-01-03", "2020-01-06"]
    stocks = {
        "600001": [{"date": day, "amount": 10} for day in calendar],
        "600002": [{"date": day, "amount": 1} for day in calendar],
        "688001": [{"date": day, "amount": 1000} for day in calendar],
    }
    before, audit = causal_intervals(
        stocks, calendar[:2], minimum_history=2, liquidity_window=2, top_n=1
    )
    stocks["600002"][-1]["amount"] = 1000000
    after, _ = causal_intervals(
        stocks, calendar, minimum_history=2, liquidity_window=2, top_n=1
    )
    assert before == {"600001": [("2020-01-03", "2020-01-03")]}
    assert after["600001"] == before["600001"]
    assert "688001" not in after
    assert audit["source_sample_bias_removed"] is False


def test_missing_and_future_state_cannot_be_called_certified_tradable():
    state = {
        "symbol": "600001", "date": "2020-01-02",
        "available_at": "2020-01-03T00:00:00+00:00",
        "observed_at": "2026-10-26T00:00:00+00:00",
        "source": "test", "source_sha256": "a" * 64,
    }
    with pytest.raises(ValueError, match="NOT_KNOWN"):
        known_state(
            state, symbol="600001", day="2020-01-02",
            decision_at=datetime(2020, 1, 2, tzinfo=UTC),
        )
    assert historical_blocks(
        raw_open=Decimal(10), state={"tradestatus": "1"}, side="BUY"
    ) == (True, "EXACT_LIMIT_PRICE_UNAVAILABLE")
    assert historical_blocks(
        raw_open=Decimal(11),
        state={"tradestatus": "1", "limit_up": 11}, side="BUY",
    )[0]
    assert historical_blocks(
        raw_open=Decimal(10), state={"tradestatus": "0"}, side="SELL"
    )[0]


@pytest.fixture
def client(monkeypatch):
    password = "test-only-long-password-for-research"
    salt = "01" * 16
    encoded = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 100000
    ).hex()
    monkeypatch.setenv(
        "QTRADE_RESEARCH_PASSWORD_HASH", "$".join(("pbkdf2_sha256", "100000", salt, encoded))
    )
    for name in ("SESSION_KEY", "QUEUE_SECRET", "QUEUE_KEY", "GITHUB_HOST_KEY"):
        monkeypatch.setenv("QTRADE_RESEARCH_" + name, "x" * 40)
    monkeypatch.setenv("QTRADE_RESEARCH_USER", "research-test")
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app, base_url="https://q-trade-temp.vercel.app") as value:
        value.headers["origin"] = api.ORIGIN
        yield value, password


def test_cookie_login_is_research_only_and_csrf_is_rejected(client):
    http, password = client
    response = http.post("/research/login", json={"user": "research-test", "password": password})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert http.get("/research/session").json()["scope"] == "RESEARCH_AND_MODEL_CONTROL"
    assert http.post(
        "/research/jobs", json={}, headers={"origin": "https://evil.invalid"}
    ).status_code == 403
    assert http.post("/research/logout", json={}).status_code == 200
    assert http.post("/research/jobs", json={}).status_code == 401


def test_anonymous_cannot_submit_even_a_valid_spec(client):
    http, _ = client
    assert http.post(
        "/research/jobs", json={"request_id": "a" * 32, "spec": default_spec()}
    ).status_code == 401


def test_authenticated_submission_binds_exact_spec_and_safe_id(client, monkeypatch):
    http, password = client
    http.post("/research/login", json={"user": "research-test", "password": password})
    monkeypatch.setattr(api, "read_public", lambda *_: {
        "worker": {"heartbeat_at": datetime.now(UTC).isoformat()}, "experiments": [],
    })
    captured = []

    class Queue:
        def append(self, section, identity, value):
            captured.append((section, identity, value))

    monkeypatch.setattr(api.GitQueue, "from_environment", lambda: Queue())
    response = http.post(
        "/research/jobs", json={"request_id": "a" * 32, "spec": default_spec()}
    )
    assert response.status_code == 202
    assert captured[0][0] == "requests"
    assert response.json()["spec_sha256"] == digest(default_spec())
    assert captured[0][2]["live_trading_enabled"] is False
    assert http.post(
        "/research/jobs", json={"request_id": "../bad", "spec": default_spec()}
    ).status_code == 422


def test_queue_failure_logs_type_without_credentials_or_traceback(client, monkeypatch, caplog):
    http, password = client
    http.post("/research/login", json={"user": "research-test", "password": password})
    monkeypatch.setattr(api, "read_public", lambda *_: {
        "worker": {"heartbeat_at": datetime.now(UTC).isoformat()}, "experiments": [],
    })

    def fail():
        raise ModuleNotFoundError("test-only-credential-never-log")

    monkeypatch.setattr(api.GitQueue, "from_environment", fail)
    response = http.post(
        "/research/jobs", json={"request_id": "d" * 32, "spec": default_spec()}
    )
    assert response.status_code == 503
    assert "error_type=ModuleNotFoundError" in caplog.text
    assert "test-only-credential-never-log" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    assert "test-only-credential-never-log" not in response.text


def test_unavailable_worker_never_silently_accepts_tasks(client, monkeypatch):
    http, password = client
    http.post("/research/login", json={"user": "research-test", "password": password})
    monkeypatch.setattr(api, "read_public", lambda *_: {"worker": {}, "experiments": []})
    assert http.post(
        "/research/jobs", json={"request_id": "b" * 32, "spec": default_spec()}
    ).status_code == 503


def test_catalog_exposes_stale_worker_without_hiding_immutable_results(client, monkeypatch):
    http, _ = client
    monkeypatch.setattr(api, "read_public", lambda *_: {
        "schema_version": api.SCHEMA, "worker": {"heartbeat_at": "2020-01-01T00:00:00+00:00"},
        "experiments": [{"experiment_id": "preserved", "state": "COMPLETE"}],
    })
    value = http.get("/research/catalog").json()
    assert value["publication_available"] is True
    assert value["worker_fresh"] is False
    assert value["experiments"][0]["experiment_id"] == "preserved"


def test_result_cannot_be_misbound_to_another_experiment(client, monkeypatch):
    http, _ = client
    listing = {"schema_version": api.SCHEMA, "experiments": [{
        "experiment_id": "requested", "state": "COMPLETE",
        "detail_file": "workflow-results/" + "a" * 64 + ".json", "detail_sha256": "a" * 64,
    }]}
    result = {"evidence_sha256": "a" * 64, "schema_version": "qtrade.workflow-result.v1",
              "experiment_id": "another", "engine": "QLIB", "state": "COMPLETE"}
    monkeypatch.setattr(api, "read_public", lambda path: listing if path == "workflow.json"
                        else result)
    assert http.get("/research/experiments/requested").status_code == 503


def test_market_clock_exposes_frozen_data_lag_without_relabeling_results(client, monkeypatch):
    http, _ = client
    monkeypatch.setattr(api, "research_clock", lambda: "2026-10-26")
    monkeypatch.setattr(api, "read_public", lambda _: {
        "schema_version": api.SCHEMA, "worker": {"heartbeat_at": datetime.now(UTC).isoformat()},
        "datasets": [{"end": "2026-10-23"}], "experiments": [{"state": "COMPLETE"}],
    })
    value = http.get("/research/catalog").json()
    assert value["worker_fresh"] is True
    assert value["frozen_data_current"] is False
    assert value["expected_complete_day"] == "2026-10-26"
    assert value["datasets"][0]["end"] == "2026-10-23"
    assert value["experiments"][0]["state"] == "COMPLETE"


def test_vercel_rewrite_keeps_research_login_separate_from_control_authority(client, monkeypatch):
    from urllib.parse import quote

    from qtrade.api.vercel import app

    _, password = client
    monkeypatch.setattr(api, "read_public", lambda _: {
        "worker": {"heartbeat_at": datetime.now(UTC).isoformat()}, "experiments": [],
    })
    captured = []
    queue = type("Queue", (), {"append": lambda self, *args: captured.append(args)})()
    monkeypatch.setattr(api.GitQueue, "from_environment", lambda: queue)
    with TestClient(app, base_url=api.ORIGIN) as http:
        http.headers["origin"] = api.ORIGIN
        path = "/api?route=" + quote("/research/login", safe="")
        response = http.post(path, json={"user": "research-test", "password": password})
        assert response.status_code == 200
        assert "public" not in response.headers.get("cache-control", "")
        response = http.post("/api?route=" + quote("/research/jobs", safe=""), json={
            "spec": default_spec(), "request_id": "c" * 32,
        })
        assert response.status_code == 202
        assert len(captured) == 1
        assert http.post("/api?route=" + quote("/control/start", safe=""), json={}).status_code in {
            401, 403, 404, 503,
        }
