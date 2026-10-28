"""Independent synthetic /proc guard tests. No RPC, network or production inputs."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path("E:/QTrade-temp/publisher_handover_bridge_safe_module_v3_20261028.py")
spec = importlib.util.spec_from_file_location("independent_safe_module_v3", SOURCE)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


@pytest.fixture
def fixture_runtime(monkeypatch, tmp_path):
    root = tmp_path / "releases" / "synthetic-revision"
    root.mkdir(parents=True)
    files = {}
    for relative in b.MODULES.values():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"# synthetic fixture only\n")
        files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    interpreter = str(tmp_path / "venv/bin/python")
    state = {"prefix": [], "pythonpath": "src", "safe": None, "cwd": str(root),
        "module": "scripts.publish_research_snapshot", "calls": []}

    class Proc:
        def __truediv__(self, name):
            if name.isdigit():
                return self
            if name == "cmdline":
                args = [interpreter, *state["prefix"], "-m", state["module"], "--synthetic"]
                return SimpleNamespace(read_bytes=lambda: b"\0".join(x.encode() for x in args) + b"\0")
            if name == "environ":
                rows = [b"LIVE_TRADING_ENABLED=false"]
                if state["pythonpath"] is not None:
                    rows.append(b"PYTHONPATH=" + state["pythonpath"].encode())
                if state["safe"] is not None:
                    rows.append(b"PYTHONSAFEPATH=" + state["safe"].encode())
                return SimpleNamespace(read_bytes=lambda: b"\0".join(rows) + b"\0")
            if name == "cwd":
                return "SYNTHETIC_CWD"
            raise AssertionError(name)

    def fake_path(value):
        return Proc() if str(value) == "/proc" else Path(value)

    def resolver(*args, **kwargs):
        state["calls"].append((args, kwargs))
        return SimpleNamespace(stdout=json.dumps({name: str(root / relative) for name, relative in b.MODULES.items()}))

    monkeypatch.setattr(b, "BASE", tmp_path)
    monkeypatch.setattr(b, "Path", fake_path)
    monkeypatch.setattr(b.os, "readlink", lambda path: state["cwd"])
    monkeypatch.setattr(b.subprocess, "run", resolver)
    owner = {"live_trading_enabled": False, "start_ticks": "SYNTHETIC_ONLY"}
    operator = SimpleNamespace(PublisherOnlyAdapter=SimpleNamespace(owner=lambda pid: owner.copy()))
    return state, root, lambda: b.runtime_probe(operator, 123, "synthetic-revision", files)


@pytest.mark.parametrize("prefix", [[], ["-B"]])
@pytest.mark.parametrize("pythonpath", ["src", ".:src", "src:."])
def test_legitimate_module_cwd_with_src_only_or_explicit_root(fixture_runtime, prefix, pythonpath):
    state, root, run = fixture_runtime
    state.update(prefix=prefix, pythonpath=pythonpath)
    proof = run()
    assert proof["cwd_and_pythonpath_match"] and proof["imports_match"]
    assert proof["import_proof_scope"] == "READONLY_PATHFINDER_RESOLUTION_REPLAY_NOT_LIVE_SYS_MODULES"
    assert len(state["calls"]) == 1
    command = state["calls"][0][0][0]
    assert command[1:3] == ["-B", "-c"]
    assert state["calls"][0][1]["env"]["LIVE_TRADING_ENABLED"] == "false"


@pytest.mark.parametrize("prefix", [["-P"], ["-I"], ["-c", "pass"], ["-BP"], ["-IB"],
    ["-B", "-P"], ["-P", "-B"], ["-B", "-I"], ["-E"], ["-S"], ["-u"], ["-X", "dev"]])
def test_flags_that_can_change_import_mode_are_denied_before_resolver(fixture_runtime, prefix):
    state, root, run = fixture_runtime
    state["prefix"] = prefix
    with pytest.raises(ValueError, match="IMPLICIT_MODULE_CWD_NOT_AVAILABLE"):
        run()
    assert state["calls"] == []


@pytest.mark.parametrize("safe", ["1", "0", "false", " "])
def test_any_nonempty_python_safe_path_is_denied(fixture_runtime, safe):
    state, root, run = fixture_runtime
    state["safe"] = safe
    with pytest.raises(ValueError, match="IMPLICIT_MODULE_CWD_NOT_AVAILABLE"):
        run()
    assert state["calls"] == []


def test_empty_python_safe_path_does_not_disable_cwd(fixture_runtime):
    state, root, run = fixture_runtime
    state["safe"] = ""
    assert run()["imports_match"]


@pytest.mark.parametrize("pythonpath", [None, "", ".", "..", "src:..", "src:external"])
def test_missing_src_or_outside_path_is_denied_before_resolver(fixture_runtime, pythonpath):
    state, root, run = fixture_runtime
    state["pythonpath"] = pythonpath
    with pytest.raises(ValueError, match="ACTUAL_PYTHONPATH_REQUIRED"):
        run()
    assert state["calls"] == []


def test_wrong_cwd_is_denied_before_resolver(fixture_runtime):
    state, root, run = fixture_runtime
    state["cwd"] = str(root.parent)
    with pytest.raises(ValueError, match="ACTUAL_CWD_REQUIRED"):
        run()
    assert state["calls"] == []


def test_wrong_module_is_denied_before_resolver(fixture_runtime):
    state, root, run = fixture_runtime
    state["module"] = "synthetic.other"
    with pytest.raises(ValueError, match="ACTUAL_PUBLISHER_MODULE_REQUIRED"):
        run()
    assert state["calls"] == []
