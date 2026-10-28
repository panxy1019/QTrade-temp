"""Synthetic-only v5 trust-root fixtures. Never reads the actual private wrapper."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


h = load("handover_v5", "publisher_handover_v5_20261028.py")
b = load("bridge_v5", "publisher_handover_bridge_v5_20261028.py")
legacy = load("old_lifecycle_v5_test", "test_publisher_handover_draft_20261028.py")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def mock_old():
    path = str(h.BASE / "releases" / h.OLD_REVISION)
    return ("# SYNTHETIC fixture, no production body\nimport os\nROOT = '" + path + "'\n"
        "os.execv(str(PYTHON), [str(PYTHON), '-B', '-m', 'scripts.publish_research_snapshot', "
        "str(ROOT / 'public'), '--a', str(ROOT / 'data'), '--b', str(ROOT / 'catalog'), "
        "'--c', '45', '--d', '90',])\n").encode()


@pytest.fixture
def synthetic(monkeypatch):
    old = mock_old()
    # This test override is local to the mock. Production code hard-binds the
    # actual separately observed SHA and 623 bytes; no manifest can change it.
    monkeypatch.setattr(h, "TRUSTED_WRAPPER_OLD_SHA256", sha(old))
    monkeypatch.setattr(h, "TRUSTED_WRAPPER_OLD_BYTES", len(old))
    return old


def manifest(old, candidate):
    return {"new_revision": "a" * 40, "wrapper_old_sha256": sha(old),
            "wrapper_new_sha256": sha(candidate)}


def test_real_trust_root_is_fixed_metadata_only():
    assert h.TRUSTED_WRAPPER_OLD_SHA256 == (
        "b0dca3c97d79995f7c8a6ab988b9e5cc469f59b40ffb02147bf72e05565b5c8f")
    assert h.TRUSTED_WRAPPER_OLD_BYTES == 623
    with pytest.raises(ValueError, match="TRUSTED_OLD_WRAPPER_REQUIRED"):
        h.wrapper_expected_bytes(mock_old(), "a" * 40)


def test_original_calls_preserved_without_evaluation(synthetic):
    old = synthetic
    argv = h._publisher_argv_list(ast.parse(old))
    assert [type(node).__name__ for node in argv.elts] == [
        "Call", "Constant", "Constant", "Constant", "Call", "Constant", "Call", "Constant",
        "Call", "Constant", "Constant", "Constant", "Constant",
    ]
    candidate = h.wrapper_expected_bytes(old, "a" * 40)
    assert h.wrapper_candidate_bytes(old, candidate, manifest(old, candidate)) == candidate
    assert candidate.replace(h.FIXED_WRAPPER_APPEND, b"", 1) == old.replace(
        h.OLD_REVISION.encode(), b"a" * 40, 1)
    candidate_argv = h._publisher_argv_list(ast.parse(candidate))
    assert len(candidate_argv.elts) == 15
    assert [node.value for node in candidate_argv.elts[-2:]] == [
        "--unified-market-state-root", "/root/qtrade-local/market-publication",
    ]


@pytest.mark.parametrize("damage", ["unknown_call", "starred", "module", "extra_execv",
                                    "new_old_path", "comment", "whitespace"])
def test_unknown_old_bytes_cannot_rebind_trust_root(synthetic, damage):
    old = synthetic
    if damage == "unknown_call":
        changed = old.replace(b"str(ROOT / 'data')", b"unreviewed(ROOT)")
    elif damage == "starred":
        changed = old.replace(b"'--a'", b"*extra")
    elif damage == "module":
        changed = old.replace(b"scripts.publish_research_snapshot", b"scripts.other")
    elif damage == "extra_execv":
        changed = old + b"os.execv('python', ['python', '-m', 'scripts.other'])\n"
    elif damage == "new_old_path":
        changed = old.replace(h.OLD_REVISION.encode(), b"b" * 40)
    elif damage == "comment":
        changed = old + b"# change\n"
    else:
        changed = old.replace(b"import os", b"import  os")
    with pytest.raises(ValueError, match="TRUSTED_OLD_WRAPPER_REQUIRED"):
        h.wrapper_expected_bytes(changed, "a" * 40)
    # Even a caller who recomputes both manifest hashes cannot change the root.
    with pytest.raises(ValueError, match="OLD_WRAPPER_CHANGED"):
        h.wrapper_candidate_bytes(changed, changed, manifest(changed, changed))


@pytest.mark.parametrize("damage", ["unknown_call", "starred", "module", "extra_execv", "wrong_path",
    "duplicate_flag", "equals_flag", "extra_120", "reordered", "removed_argument", "root_nfs"])
def test_new_candidate_cannot_add_or_change_old_expressions(synthetic, damage):
    old = synthetic
    candidate = h.wrapper_expected_bytes(old, "a" * 40)
    if damage == "unknown_call":
        candidate = candidate.replace(b"str(ROOT / 'data')", b"unreviewed(ROOT)")
    elif damage == "starred":
        candidate = candidate.replace(b"'--a'", b"*extra")
    elif damage == "module":
        candidate = candidate.replace(b"scripts.publish_research_snapshot", b"scripts.other")
    elif damage == "extra_execv":
        candidate += b"os.execv('python', ['python', '-m', 'scripts.other'])\n"
    elif damage == "wrong_path":
        candidate = candidate.replace(b"a" * 40, b"b" * 40)
    elif damage == "duplicate_flag":
        candidate = candidate.replace(h.FIXED_WRAPPER_APPEND, h.FIXED_WRAPPER_APPEND * 2)
    elif damage == "equals_flag":
        candidate = candidate.replace(h.FIXED_WRAPPER_APPEND,
            b', "--unified-market-state-root=/root/qtrade-local/market-publication"')
    elif damage == "extra_120":
        candidate = candidate.replace(b"'45'", b"'120'")
    elif damage == "reordered":
        candidate = candidate.replace(b"'--c', '45', '--d', '90'", b"'--d', '90', '--c', '45'")
    elif damage == "removed_argument":
        candidate = candidate.replace(b"'--c', '45', ", b"")
    elif damage == "root_nfs":
        candidate = candidate.replace(b"/root/qtrade-local/market-publication", str(h.BASE).encode())
    with pytest.raises(ValueError, match="EXACT_V4_WRAPPER_TRANSFORM_REQUIRED"):
        h.wrapper_candidate_bytes(old, candidate, manifest(old, candidate))


def test_sole_term_and_ambiguous_delivery_contract_unchanged(monkeypatch):
    monkeypatch.setattr(legacy, "h", h)
    legacy.test_one_signal_drain_long_and_starting_pending_then_provisional()
    legacy.test_uncertain_operations_are_never_replayed("signal_error")
    legacy.test_response_receipt_failure_does_not_resignal()


def test_source_binds_v5_bridge_without_modifying_old_versions():
    assert b.OPERATOR_SHA256 == sha((ROOT / "publisher_handover_v5_20261028.py").read_bytes())
    new = (ROOT / "publisher_handover_v5_20261028.py").read_bytes()
    old = (ROOT / "publisher_handover_draft_20261028.py").read_bytes()
    assert new[new.index(b"def check_gates("):] == old[old.index(b"def check_gates("):]
    assert sha(old) == "eb2acf2cc2bad3103bb2478ff6317bca47ba085a275ec602bc372a9ea8545edc"
