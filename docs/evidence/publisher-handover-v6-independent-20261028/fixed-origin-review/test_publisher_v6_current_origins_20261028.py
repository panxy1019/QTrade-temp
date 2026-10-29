"""Fresh origin/shadow failures in isolated filesystem fixtures only."""
import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location("v6_current_origin_operator",
    ROOT / "publisher_handover_v6_preparation_20261028.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


@pytest.fixture
def release(tmp_path, monkeypatch):
    base = tmp_path / "base"
    revision = "a" * 40
    root = base / "releases" / revision
    names = set(h.PROTECTED_MODULES.values()) | {"scripts/__init__.py", "src/qtrade/__init__.py",
        "src/qtrade/research/__init__.py", "src/qtrade/market_data/__init__.py"}
    inventory = {}
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = b"raise AssertionError('fixture publisher code must NEVER execute')\n"
        path.write_bytes(raw)
        inventory[name] = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(h, "BASE", base)
    real_run = subprocess.run
    calls = []

    def fixture_child(command, **kwargs):
        assert command[0] == str(base / "venv/bin/python") and command[1] == "-B"
        assert Path(kwargs["cwd"]) == root
        assert kwargs["env"]["LIVE_TRADING_ENABLED"] == "false"
        assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
        assert kwargs["env"]["PYTHONPATH"] == str(root / "src") + os.pathsep + str(root)
        calls.append(command)
        # Only this synthetic fixture substitutes the installed local Python.
        # The production function always invokes the fixed reviewed venv path.
        env = dict(os.environ)
        env.update(kwargs["env"])
        return real_run([sys.executable, *command[1:]], **{**kwargs, "env": env})

    monkeypatch.setattr(h.subprocess, "run", fixture_child)
    manifest = {"release_root": str(root), "new_revision": revision, "runtime_files": inventory}
    adapter = h.PublisherOnlyAdapter(manifest, None, None)
    return root, adapter, calls, fixture_child


def shadow(root):
    path = root / "scripts/unified_market_checkpoint/__init__.py"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"raise AssertionError('untracked shadow must never execute')\n")
    return path


def test_clean_current_origins_do_not_execute_modules_or_create_pycache(release):
    root, adapter, calls, _ = release
    adapter.check_source()
    assert len(calls) == 1
    assert not list(root.rglob("__pycache__")) and not list(root.rglob("*.pyc"))


def test_untracked_shadow_rejected_while_all_git_hashes_remain_equal(release):
    root, adapter, calls, _ = release
    shadow(root)
    assert all(hashlib.sha256((root / name).read_bytes()).hexdigest() == value
               for name, value in adapter.manifest["runtime_files"].items())
    with pytest.raises(ValueError, match="UNTRACKED_RELEASE_SOURCE_FORBIDDEN"):
        adapter.check_source()
    assert not calls


def test_shadow_inserted_after_inventory_scan_is_caught_by_fresh_pathfinder(release, monkeypatch):
    root, adapter, calls, real_fixture_child = release

    def insert_then_resolve(command, **kwargs):
        shadow(root)
        return real_fixture_child(command, **kwargs)

    monkeypatch.setattr(h.subprocess, "run", insert_then_resolve)
    with pytest.raises(subprocess.CalledProcessError):
        adapter.check_source()
    assert len(calls) == 1 and not list(root.rglob("__pycache__"))


def test_shadow_during_temporary_wrapper_fsync_blocks_replace(release, tmp_path, monkeypatch):
    root, adapter, _, _ = release
    target = tmp_path / "private/wrapper.py"
    target.parent.mkdir()
    old, new = b"SYNTHETIC old wrapper, never executed\n", b"SYNTHETIC candidate, never executed\n"
    target.write_bytes(old)
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    adapter.manifest.update(wrapper_target=str(target), wrapper_candidate=str(tmp_path / "candidate.py"),
        wrapper_old_sha256=hashlib.sha256(old).hexdigest(), wrapper_new_sha256=hashlib.sha256(new).hexdigest(),
        attempt_root=str(attempt))
    monkeypatch.setattr(h, "WRAPPER", target)
    monkeypatch.setattr(h, "durable_directory", lambda path, **kwargs: path.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(h, "fsync_directory", lambda _: None)
    monkeypatch.setattr(h.os, "O_NOFOLLOW", 0, raising=False)
    monkeypatch.setattr(h.os, "fchmod", lambda *args: None, raising=False)
    monkeypatch.setattr(h.os, "fchown", lambda *args: None, raising=False)
    monkeypatch.setattr(adapter, "_mutation_guard", lambda: None)  # Synthetic lifecycle leaf only.
    monkeypatch.setattr(adapter, "guard_old", lambda: None)
    monkeypatch.setattr(adapter, "guard_config", lambda: None)
    monkeypatch.setattr(adapter, "_wrapper_raw", lambda: (old, new))
    monkeypatch.setattr(adapter, "controls", lambda: {})
    original = h.os.fsync
    count = 0

    def insert_on_second_fsync(fd):
        nonlocal count
        count += 1
        original(fd)
        if count == 2:  # Backup, then the fully written temporary candidate.
            shadow(root)

    monkeypatch.setattr(h.os, "fsync", insert_on_second_fsync)
    with pytest.raises(ValueError, match="UNTRACKED_RELEASE_SOURCE_FORBIDDEN"):
        adapter.install_wrapper({}, attempt / "private/wrapper.old.py")
    assert count == 2 and target.read_bytes() == old


def test_shadow_after_initial_term_source_check_blocks_rpc(release, monkeypatch):
    root, adapter, _, _ = release
    adapter.manifest["wrapper_new_sha256"] = "f" * 64
    calls = []
    adapter.rpc = lambda *args: calls.append(args)
    monkeypatch.setattr(adapter, "_mutation_guard", lambda: None)  # Synthetic lifecycle leaf only.
    monkeypatch.setattr(adapter, "guard_config", lambda: None)
    monkeypatch.setattr(adapter, "guard_old", lambda: None)
    monkeypatch.setattr(adapter, "controls", lambda: {})

    def changed_after_first_check():
        shadow(root)
        return "f" * 64

    monkeypatch.setattr(adapter, "wrapper_hash", changed_after_first_check)
    with pytest.raises(ValueError, match="UNTRACKED_RELEASE_SOURCE_FORBIDDEN"):
        adapter.named_term_once({})
    assert not calls
