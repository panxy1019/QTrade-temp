"""Isolated ASGI/local-Git integration; never reaches the production queue."""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import re
import secrets
import socket
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
NETWORK_EVENTS = []


def network_guard(event, args):
    if event == "socket.connect":
        address = args[1]
        assert isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}, (
            "ONLY_LOOPBACK_HTTP_ALLOWED", address)
        NETWORK_EVENTS.append({"host": address[0], "port": address[1]})


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def junit(path, name, cases, error=None):
    root = ET.Element("testsuite", name=name, tests=str(len(cases) + bool(error)),
                      failures=str(bool(error)), errors="0", skipped="0")
    for case in cases:
        ET.SubElement(root, "testcase", classname=name, name=case)
    if error:
        item = ET.SubElement(root, "testcase", classname=name, name="integration_aborted")
        ET.SubElement(item, "failure", type=type(error).__name__).text = str(error)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def source_inventory(root):
    return {p.relative_to(root).as_posix(): {"sha256": file_hash(p), "bytes": p.stat().st_size}
            for p in root.rglob("*") if p.is_file()}


def load_modules(source):
    sys.path[:0] = [str(source / "src"), str(source)]
    from dulwich import porcelain
    from dulwich.client import LocalGitClient
    from dulwich.repo import Repo
    from qtrade.research.workflow_queue import GitQueue, seal, unseal
    return porcelain, LocalGitClient, Repo, GitQueue, seal, unseal


def local_queue(root, source):
    porcelain, LocalGitClient, Repo, GitQueue, _, _ = load_modules(source)
    root.mkdir()
    bare_path, seed_path = root / "queue.git", root / "seed"
    bare = Repo.init_bare(str(bare_path), mkdir=True)
    bare.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")
    bare.close()
    seed = Repo.init(str(seed_path), mkdir=True)
    seed.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")
    (seed_path / "README").write_text("ISOLATED LOCAL QUEUE; NO GITHUB TRANSPORT\n")
    porcelain.add(seed, paths=["README"])
    porcelain.commit(seed, message=b"Initialize isolated queue", author=b"Local test <test@invalid>")
    client = LocalGitClient()
    client.send_pack(str(bare_path), lambda refs: {b"refs/heads/main": seed.head()},
        generate_pack_data=lambda have, want, **kwargs:
        seed.generate_pack_data(set(have), set(want), **kwargs))
    seed.close()

    class Transport:
        before_send = None

        def clone(self, remote, destination, **kwargs):
            assert remote == "/panxy1019/QTrade-research-queue.git"
            return client.clone(str(bare_path), destination, **kwargs)

        def send_pack(self, remote, update_refs, **kwargs):
            assert remote == b"/panxy1019/QTrade-research-queue.git"
            if self.before_send is not None:
                callback, self.before_send = self.before_send, None
                callback()
            return client.send_pack(str(bare_path), update_refs, **kwargs)

    transport = Transport()

    class LocalQueue(GitQueue):
        def _client(self):
            return transport

        def _vendor(self):
            raise AssertionError("SSH_VENDOR_FORBIDDEN_IN_LOCAL_INTEGRATION")

    queue = LocalQueue("TEST_ONLY_UNPARSED_LOCAL_TRANSPORT", "ssh-ed25519 TEST_ONLY_UNUSED")

    def head():
        with Repo(str(bare_path)) as repo:
            return repo.head().decode()

    return queue, transport, head


def protocol_negative(root, source):
    _, _, _, _, seal, unseal = load_modules(source)
    queue, transport, head = local_queue(root / "protocol", source)
    secret = secrets.token_hex(32)
    identity = "a" * 32
    value = seal({"job_id": identity, "kind": "LOCAL_PROTOCOL_FIXTURE"}, secret)
    result = []
    first = head()
    queue.append("requests", identity, value)
    appended = head()
    assert appended != first
    assert queue.read() == {"requests/" + identity: value}
    assert unseal(queue.read()["requests/" + identity], secret)["job_id"] == identity
    result.append("actual_clone_append_read_hmac")
    queue.append("requests", identity, value)
    assert head() == appended
    result.append("identical_append_has_no_second_commit")
    try:
        queue.append("requests", identity, {**value, "kind": "CHANGED"})
    except ValueError as error:
        assert str(error) == "IMMUTABLE_JOB_ID_CONFLICT"
    else:
        raise AssertionError("CONFLICT_MUST_REJECT")
    assert head() == appended
    result.append("same_id_changed_payload_rejected_without_ref_change")
    for section, job in [("other", identity), ("requests", "../" + identity)]:
        try:
            queue.append(section, job, value)
        except ValueError:
            pass
        else:
            raise AssertionError("PATH_MUST_REJECT")
    result.append("section_and_identity_path_guards")
    competitor = "b" * 32
    pending = "c" * 32
    transport.before_send = lambda: queue.append("requests", competitor,
        seal({"job_id": competitor, "kind": "LOCAL_PROTOCOL_FIXTURE"}, secret))
    try:
        queue.append("requests", pending,
            seal({"job_id": pending, "kind": "LOCAL_PROTOCOL_FIXTURE"}, secret))
    except ValueError as error:
        assert str(error) == "QUEUE_CONCURRENT_UPDATE_RETRY"
    else:
        raise AssertionError("NONFORCE_CAS_MUST_REJECT")
    records = queue.read()
    assert "requests/" + competitor in records and "requests/" + pending not in records
    result.append("real_local_ref_CAS_loses_to_concurrent_append_without_overwrite")
    queue.append("requests", pending,
        seal({"job_id": pending, "kind": "LOCAL_PROTOCOL_FIXTURE"}, secret))
    assert "requests/" + pending in queue.read()
    result.append("explicit_CAS_retry_preserves_both_commits")
    altered = {**value, "kind": "TAMPERED"}
    try:
        unseal(altered, secret)
    except ValueError as error:
        assert str(error) == "QUEUE_SIGNATURE_MISMATCH"
    else:
        raise AssertionError("HMAC_MUST_REJECT")
    result.append("HMAC_rejects_modified_payload")
    receipt = {"state": "PASS_LOCAL_PROTOCOL_ONLY", "scope": "SYNTHETIC_PROTOCOL_PAYLOADS",
        "cases": result, "bare_head": head(), "production_queue_accessed": False,
        "ssh_vendor_used": False}
    save(root / "protocol-negative.json", receipt)
    return receipt


@contextmanager
def static_server(root):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port) + "/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def real_e2e(args):
    assert sys.platform == "linux", "REAL_WORKER_REQUIRES_LINUX"
    assert args.base.is_dir() and args.database.parent == args.owned / "db"
    assert args.archive.is_dir() and args.database.name == "workflow.sqlite"
    assert not args.database.exists(), "NEW_WORKFLOW_DATABASE_REQUIRED"
    manifest = json.loads(args.source_manifest.read_bytes())
    assert manifest["baseline_commit"] == args.revision and not manifest.get("overlays")
    assert source_inventory(args.source) == manifest["expected_files"]
    save(args.owned / "source-before.json", source_inventory(args.source))
    assert (args.database.parent / "qlib-versions.sqlite").is_file()
    for name in ["strategies.sqlite", "online-models.sqlite"]:
        assert not (args.database.parent / name).exists(), "FRESH_ISOLATED_DATABASES_REQUIRED"
    import shutil
    assert shutil.disk_usage(args.base).free >= 100 * 1024**3
    assert shutil.disk_usage(args.owned).free >= 2 * 1024**3
    load_modules(args.source)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from qtrade.api import research_workflow as api
    from qtrade.monitoring.data_freshness import AUTHORITY, signed
    from qtrade.research.workflow import digest
    from qtrade.research.workflow_queue import GitQueue, seal, unseal
    from scripts.run_qlib_worker import Worker
    queue, _, head = local_queue(args.owned / "http-queue", args.source)
    password, session_key, queue_secret = (secrets.token_urlsafe(48) for _ in range(3))
    salt = secrets.token_hex(16)
    encoded = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 100000).hex()
    configuration = {"LIVE_TRADING_ENABLED": "false",
        "QTRADE_RESEARCH_USER": "local-e2e-research",
        "QTRADE_RESEARCH_ORIGIN": "https://factor-e2e.invalid",
        "QTRADE_RESEARCH_PASSWORD_HASH": "$".join(("pbkdf2_sha256", "100000", salt, encoded)),
        "QTRADE_RESEARCH_SESSION_KEY": session_key,
        "QTRADE_RESEARCH_QUEUE_SECRET": queue_secret,
        "QTRADE_RESEARCH_QUEUE_KEY": "TEST_ONLY_UNUSED_LOCAL_TRANSPORT",
        "QTRADE_RESEARCH_GITHUB_HOST_KEY": "ssh-ed25519 TEST_ONLY_UNUSED"}
    exchanges, checks = [], []

    def request(http, method, path, body=None, **kwargs):
        response = http.request(method, path, json=body, **kwargs)
        record = {"method": method, "path": path, "status": response.status_code,
                  "response": response.json()}
        if body is not None:
            record["request"] = ({"user": body.get("user"), "password": "LOCAL_EPHEMERAL_REDACTED"}
                                  if path.endswith("/login") else body)
        record["headers"] = {name: response.headers[name] for name in
            ["cache-control", "content-type"] if name in response.headers}
        exchanges.append(record)
        save(args.owned / "http-exchanges.json", exchanges)
        return response

    started = time.perf_counter()
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, configuration))
        stack.enter_context(patch.object(GitQueue, "from_environment", classmethod(lambda cls: queue)))
        worker = Worker(args.base, args.database, sys.executable, args.revision, args.archive)
        stack.callback(worker.db.close)
        worker.publish("IDLE")
        assert worker.factor_contracts is not None
        public = worker.public
        url = stack.enter_context(static_server(public))
        stack.enter_context(patch.object(api, "PUBLIC_ROOT", url))
        app = FastAPI()
        app.include_router(api.router)
        http = stack.enter_context(TestClient(app, base_url="https://factor-e2e.invalid"))
        http.headers["origin"] = configuration["QTRADE_RESEARCH_ORIGIN"]
        catalog_response = request(http, "GET", "/research/factor-strategies/catalog")
        assert catalog_response.status_code == 200
        catalog = catalog_response.json()
        assert len(catalog["templates"]) == 4
        assert catalog["worker"]["source_revision"] == args.revision
        spec = deepcopy(catalog["templates"][0]["spec"])
        spec["name"] = "step2-http-gitqueue-owned-worker"
        assert request(http, "POST", "/research/factor-strategies/specification",
                       {"spec": spec}).status_code == 401
        assert request(http, "POST", "/research/login", {"user": "local-e2e-research",
            "password": password}, headers={"origin": "https://untrusted.invalid"}).status_code == 403
        login = request(http, "POST", "/research/login", {"user": "local-e2e-research",
                        "password": password})
        assert login.status_code == 200
        cookie = login.headers["set-cookie"].lower()
        assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
        checks.append("real_ASGI_auth_origin_secure_cookie")
        preview = request(http, "POST", "/research/factor-strategies/specification", {"spec": spec})
        assert preview.status_code == 200
        preview = preview.json()
        definition = preview["strategy_definition"]
        assert definition["source"]["revision"] == args.revision
        job = secrets.token_hex(16)
        body = {"spec": spec, "request_id": job,
            "expected_strategy_version": preview["expected_strategy_version"],
            "expected_catalog_sha256": preview["expected_catalog_sha256"]}
        before = head()
        wrong = {**body, "expected_catalog_sha256": "f" * 64}
        assert request(http, "POST", "/research/factor-strategies/jobs", wrong).status_code == 409
        invalid = deepcopy(body)
        invalid["spec"]["execution"]["cost_callable"] = "untrusted"
        assert request(http, "POST", "/research/factor-strategies/jobs", invalid).status_code == 422
        oversized = request(http, "POST", "/research/factor-strategies/jobs", {"x": "x" * 16001})
        assert oversized.status_code == 413 and head() == before
        checks.append("HTTP_identity_fields_size_fail_before_Git_write")
        submitted = request(http, "POST", "/research/factor-strategies/jobs", body)
        assert submitted.status_code == 202 and submitted.json()["job_id"] == job
        committed = head()
        assert committed != before
        assert request(http, "POST", "/research/factor-strategies/jobs", body).status_code == 202
        assert head() == committed
        payload = unseal(queue.read()["requests/" + job], queue_secret)
        assert payload["factor_spec"] == spec and payload["factor_spec_sha256"] == digest(spec)
        assert payload["data_version_id"] == definition["evaluation_context"]["data_binding"][
            "data_version_id"]
        checks.append("HTTP_to_native_GitQueue_HMAC_and_idempotent_commit")
        alternate = deepcopy(spec)
        alternate["name"] += "-conflict"
        changed = request(http, "POST", "/research/factor-strategies/specification", {"spec": alternate})
        assert changed.status_code == 200
        conflicting = {**body, "spec": alternate,
            "expected_strategy_version": changed.json()["expected_strategy_version"]}
        assert request(http, "POST", "/research/factor-strategies/jobs", conflicting).status_code == 409
        assert head() == committed
        checks.append("HTTP_same_job_changed_spec_conflicts_without_mutation")
        bad_job = secrets.token_hex(16)
        forged = {**seal(payload, queue_secret), "job_id": bad_job}
        queue.append("requests", bad_job, forged)
        unknown_job = secrets.token_hex(16)
        unknown = {**payload, "job_id": unknown_job, "schema_version": "UNKNOWN"}
        queue.append("requests", unknown_job, seal(unknown, queue_secret))
        worker.once()
        rows = worker.db.execute("SELECT id,state,result FROM jobs").fetchall()
        assert len(rows) == 1 and rows[0][0:2] == (job, "COMPLETE"), rows
        checks.append("worker_rejects_forged_HMAC_and_unknown_schema")
        completion = request(http, "GET", "/research/factor-strategies/jobs/" + job)
        assert completion.status_code == 200
        receipt = completion.json()
        assert signed({k: v for k, v in receipt.items() if k != "evidence_sha256"}) == receipt
        assert receipt["strategy_version"] == definition["strategy_version"]
        assert receipt["contract_sha256"] == definition["evidence_sha256"]
        assert receipt["registration_only"] is True and receipt["status"] == "EXPERIMENT"
        assert receipt["role"] == "NONE" and receipt["model_activation"] is False
        record = worker.factor_contracts.registry.get(receipt["strategy_id"])
        assert record.status.value == "EXPERIMENT" and record.role.value == "NONE"
        assert worker.factor_contracts.registry.research_contract(receipt["strategy_id"]) == definition
        assert len(worker.factor_contracts.registry.research_jobs(receipt["strategy_id"])) == 1
        assert len(worker.factor_contracts.registry.transitions(receipt["strategy_id"])) == 1
        assert worker.online.registry.snapshot()["active_model"] is None
        assert worker.online.registry.snapshot()["models"] == []
        assert worker.rolling.current()["policy"]["mode"] == "OFF"
        checks.append("owned_child_actual_Registry_signed_detail_and_no_model_activation")
        assert request(http, "POST", "/research/factor-strategies/jobs", body).status_code == 409
        worker.ingest()
        assert worker.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert len(worker.factor_contracts.registry.research_jobs(receipt["strategy_id"])) == 1
        save(args.owned / "completion.json", receipt)
        save(args.owned / "definition.json", definition)
        save(args.owned / "queue-envelope.json", queue.read()["requests/" + job])
    assert source_inventory(args.source) == manifest["expected_files"]
    save(args.owned / "source-after.json", source_inventory(args.source))
    result = {"state": "PASS_ISOLATED_ASGI_LOCAL_GIT_OWNED_WORKER",
        "completed_at": datetime.now(UTC).isoformat(), "revision": args.revision,
        "source_files": len(manifest["expected_files"]), "elapsed_seconds": time.perf_counter() - started,
        "job_id": job, "strategy_version": receipt["strategy_version"], "checks": checks,
        "registration_receipt_sha256": receipt["evidence_sha256"], "git_head": head(),
        "http_exchange_count": len(exchanges), "source_before_after_exact": True,
        "parent_loopback_network_events": NETWORK_EVENTS,
        "production_queue_accessed": False, "github_transport_verified": False,
        "tls_or_public_delivery_verified": False, "model_activation": False}
    save(args.owned / "e2e-result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["negative", "real-e2e"], required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--owned", type=Path, required=True)
    parser.add_argument("--revision")
    for name in ["base", "database", "archive", "source-manifest"]:
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    assert args.owned.is_dir() and not any(p.is_symlink() for p in
        [args.owned, *args.owned.parents])
    (args.owned / "tmp").mkdir()
    tempfile.tempdir = str(args.owned / "tmp")
    os.environ["TMPDIR"] = str(args.owned / "tmp")
    os.environ["LIVE_TRADING_ENABLED"] = "false"
    sys.addaudithook(network_guard)
    try:
        if args.mode == "real-e2e":
            assert re.fullmatch(r"[a-f0-9]{40}", args.revision or "") is not None
            result = real_e2e(args)
        else:
            result = protocol_negative(args.owned, args.source)
    except Exception as error:
        junit(args.owned / "integration.xml", args.mode, [], error)
        save(args.owned / "failure.json", {"type": type(error).__name__, "message": str(error)})
        raise
    junit(args.owned / "integration.xml", args.mode, result.get("checks", result.get("cases", [])))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
