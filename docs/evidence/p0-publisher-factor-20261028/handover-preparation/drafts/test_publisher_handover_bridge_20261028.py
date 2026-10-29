"""Bridge fixtures are explicitly synthetic and never load a real gate bundle."""

import copy
import http.client
import importlib.util
import json
import stat
import subprocess
import sys
import xmlrpc.client
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).with_name("publisher_handover_bridge_draft_20261028.py")
spec = importlib.util.spec_from_file_location("publisher_readonly_bridge", SOURCE)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def mock_transport(monkeypatch, *, fail=False):
    events = []
    identity = {"device": 1, "inode": 2, "uid": 0}
    info = SimpleNamespace(st_mode=stat.S_IFSOCK, st_dev=1, st_ino=2, st_uid=0)
    monkeypatch.setattr(b, "SOCKET", SimpleNamespace(stat=lambda: info))
    monkeypatch.setattr(b.socket, "AF_UNIX", 1, raising=False)

    class Socket:
        def settimeout(self, seconds):
            events.append(("timeout", seconds))

        def connect(self, path):
            events.append(("connect", path))

        def close(self):
            pass

    class Connection:
        def __init__(self, *args, **kwargs):
            events.append(("connection", args))

        def request(self, method, path, body, headers):
            events.append(("request", method, path, xmlrpc.client.loads(body)))
            if fail:
                raise http.client.RemoteDisconnected("SYNTHETIC_RESPONSE_LOSS")

        def getresponse(self):
            return SimpleNamespace(
                status=200,
                read=lambda maximum: xmlrpc.client.dumps(
                    ({"name": b.PROGRAM, "pid": 123},), methodresponse=True
                ).encode(),
            )

        def close(self):
            events.append(("close",))

    monkeypatch.setattr(b.socket, "socket", lambda *args: Socket())
    monkeypatch.setattr(b.http.client, "HTTPConnection", Connection)
    return identity, events


def test_readonly_rpc_serializes_only_fixed_program(monkeypatch):
    identity, events = mock_transport(monkeypatch)
    result = b.UnixPublisherRPC(identity)("supervisor.getProcessInfo", (b.PROGRAM,))
    assert result == {"name": b.PROGRAM, "pid": 123}
    request = next(event for event in events if event[0] == "request")
    assert request[1:3] == ("POST", "/RPC2")
    assert request[3] == ((b.PROGRAM,), "supervisor.getProcessInfo")


@pytest.mark.parametrize(
    "method,args",
    [
        ("supervisor.stopProcess", (b.PROGRAM,)),
        ("supervisor.shutdown", ()),
        ("supervisor.getAllProcessInfo", ()),
        ("supervisor.getProcessInfo", ("all",)),
        ("supervisor.signalProcess", (b.PROGRAM, "TERM")),
    ],
)
def test_readonly_rpc_refuses_other_actions_before_io(monkeypatch, method, args):
    identity, events = mock_transport(monkeypatch)
    with pytest.raises(ValueError):
        b.UnixPublisherRPC(identity)(method, args)
    assert not events


def test_no_xmlrpc_implicit_term_retry_after_response_loss(monkeypatch):
    identity, events = mock_transport(monkeypatch, fail=True)
    rpc = b.UnixPublisherRPC(identity, allow_one_term=True)
    with pytest.raises(http.client.RemoteDisconnected):
        rpc("supervisor.signalProcess", (b.PROGRAM, "TERM"))
    with pytest.raises(ValueError, match="ONE_FIXED_TERM_NOT_AVAILABLE"):
        rpc("supervisor.signalProcess", (b.PROGRAM, "TERM"))
    assert sum(event[0] == "request" for event in events) == 1
    assert sum(event[0] == "connect" for event in events) == 1


def test_socket_identity_change_rejected_before_connect(monkeypatch):
    identity, events = mock_transport(monkeypatch)
    identity["inode"] = 3
    with pytest.raises(ValueError, match="UNIX_SOCKET_IDENTITY_CHANGED"):
        b.UnixPublisherRPC(identity)("supervisor.getProcessInfo", (b.PROGRAM,))
    assert not events


def test_incomplete_real_gates_stay_pending_and_preparation_is_json():
    old = {"pid": 123, "start_ticks": "SYNTHETIC_ONLY"}
    state = {
        "owners": {"publisher": old},
        "context": {"active": "HALTED"},
        "frozen_controls": {"books": {}, "rolling": "OFF"},
    }
    calls = []
    adapter = SimpleNamespace(
        guard_old=lambda: calls.append("guard_old"),
        controls=lambda: copy.deepcopy(state),
        owner=lambda pid: old.copy(),
    )

    def incomplete(*args):
        raise KeyError("new_revision")

    operator = SimpleNamespace(check_gates=incomplete)
    result = b.prepare_snapshot(operator, {"old_owner": old}, {}, adapter)
    assert result["state"] == "READONLY_PREPARATION_ONLY"
    assert result["gate_result"]["state"] == "PENDING_REAL_GATE_BUNDLE"
    assert (
        not result["wrapper_write"]
        and not result["signal_sent"]
        and not result["mutation_stage_approved"]
    )
    assert calls == ["guard_old"] and json.loads(json.dumps(result)) == result


def test_changed_actual_owner_rejects_preparation():
    old = {"pid": 123, "start_ticks": "SYNTHETIC_ONLY"}
    adapter = SimpleNamespace(
        guard_old=lambda: None,
        controls=lambda: {},
        owner=lambda pid: {"pid": 123, "start_ticks": "CHANGED"},
    )
    with pytest.raises(ValueError, match="PREPARATION_OWNER_CHANGED"):
        b.prepare_snapshot(None, {"old_owner": old}, {}, adapter, fixture_only=True)


def test_concrete_wiring_remains_readonly(monkeypatch):
    constructed = []

    class Adapter:
        def __init__(self, manifest, reader, rpc, **kwargs):
            constructed.append((manifest, reader, rpc, kwargs))

    operator = SimpleNamespace(PublisherOnlyAdapter=Adapter)
    reader = SimpleNamespace(
        controls=lambda: {}, frozen_controls=lambda: {}, h=SimpleNamespace(context=lambda: {})
    )
    monkeypatch.setattr(b, "readonly_reader", lambda: reader)
    bindings = {
        "helpers": {
            "reader": {"path": str(b.READER), "sha256": b.READER_SHA256},
            "context": {"path": str(b.CONTEXT), "sha256": b.CONTEXT_SHA256},
        },
        "socket": {
            "path": str(b.SOCKET),
            "is_unix_socket": True,
            "device": 1,
            "inode": 2,
            "uid": 0,
        },
    }
    b.make_adapter(operator, {"new_revision": "SYNTHETIC", "runtime_files": {}}, bindings)
    assert constructed[0][1] is reader
    assert constructed[0][3]["mutation_stage_approved"] is False
    assert constructed[0][2].allow_one_term is False


def test_cli_fixture_is_explicit_and_apply_is_absent():
    operator = SOURCE.with_name("publisher_handover_draft_20261028.py")
    command = [sys.executable, str(SOURCE), "--fixture", "--operator", str(operator)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)
    assert document["fixture_only"] and document["state"] == "MOCK_PREPARATION_ONLY"
    assert document["gate_result"]["state"] == "SYNTHETIC_FIXTURE_NO_REAL_GATE_VALIDATION"
    result = subprocess.run(command + ["--apply"], capture_output=True, text=True, timeout=10)
    assert result.returncode != 0 and "unrecognized arguments" in result.stderr
