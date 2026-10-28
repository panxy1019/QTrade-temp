"""Pure synthetic wrapper and RPC/lifecycle mocks. No private wrapper or SSH I/O."""
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


h = load("handover_v4", "publisher_handover_v4_20261028.py")
b = load("bridge_v4", "publisher_handover_bridge_v4_20261028.py")
legacy = load("legacy_operator_tests", "test_publisher_handover_draft_20261028.py")
legacy_bridge = load("legacy_bridge_tests", "test_publisher_handover_bridge_safe_module_v3_20261028.py")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def old_fixture(*, assigned=False, crlf=False):
    path = str(h.BASE / "releases" / h.OLD_REVISION)
    before = ("# 合成交接样例；没有真实wrapper字节\nimport os\nROOT = '" + path
              + "'\nPYTHON = '/fixture/python'\n")
    argv = "[PYTHON, '-B', '-m', 'scripts.publish_research_snapshot', '--existing', '45',]"
    body = ("argv = " + argv + "\nos.execv(PYTHON, argv)\n" if assigned else
            "os.execv(PYTHON, " + argv + ")\n")
    return (before + body).replace("\n", "\r\n" if crlf else "\n").encode()


def binding(old, candidate):
    return {"new_revision": "a" * 40, "wrapper_old_sha256": sha(old),
            "wrapper_new_sha256": sha(candidate)}


@pytest.mark.parametrize("assigned,crlf", [(False, False), (True, False), (False, True), (True, True)])
def test_exact_tail_append_preserves_every_other_byte(assigned, crlf):
    old = old_fixture(assigned=assigned, crlf=crlf)
    new = h.wrapper_expected_bytes(old, "a" * 40)
    assert h.wrapper_candidate_bytes(old, new, binding(old, new)) == new
    replacement = old.replace(h.OLD_REVISION.encode(), b"a" * 40)
    assert new.replace(h.FIXED_WRAPPER_APPEND, b"", 1) == replacement
    argv = h._publisher_argv_list(ast.parse(new))
    assert [value.value for value in argv.elts[-4:]] == [
        "--existing", "45", "--unified-market-state-root", "/root/qtrade-local/market-publication",
    ]


@pytest.mark.parametrize("change", [
    "wrong_root", "nfs_root", "relative_root", "extra_flag", "duplicate_flag", "equals_flag",
    "wrong_revision", "other_argument", "added_statement", "other_whitespace", "wrong_position",
])
def test_reject_any_change_beyond_the_two_approved_edits(change):
    old = old_fixture()
    candidate = h.wrapper_expected_bytes(old, "a" * 40)
    if change == "wrong_root":
        candidate = candidate.replace(b"/root/qtrade-local/market-publication", b"/tmp/market")
    elif change == "nfs_root":
        candidate = candidate.replace(b"/root/qtrade-local/market-publication", str(h.BASE).encode())
    elif change == "relative_root":
        candidate = candidate.replace(b"/root/qtrade-local/market-publication", b"market")
    elif change == "extra_flag":
        candidate = candidate.replace(b"'--existing'", b"'--unified-market-seconds', '120', '--existing'")
    elif change == "duplicate_flag":
        candidate = candidate.replace(h.FIXED_WRAPPER_APPEND, h.FIXED_WRAPPER_APPEND * 2)
    elif change == "equals_flag":
        candidate = candidate.replace(h.FIXED_WRAPPER_APPEND,
            b', "--unified-market-state-root=/root/qtrade-local/market-publication"')
    elif change == "wrong_revision":
        candidate = candidate.replace(b"a" * 40, b"b" * 40)
    elif change == "other_argument":
        candidate = candidate.replace(b"'45'", b"'120'")
    elif change == "added_statement":
        candidate += b"print('injected')\n"
    elif change == "other_whitespace":
        candidate = candidate.replace(b"import os", b"import  os")
    elif change == "wrong_position":
        candidate = candidate.replace(h.FIXED_WRAPPER_APPEND, b"")
        candidate = candidate.replace(b"'-m'", b"'-m'" + h.FIXED_WRAPPER_APPEND)
    with pytest.raises(ValueError, match="EXACT_V4_WRAPPER_TRANSFORM_REQUIRED"):
        h.wrapper_candidate_bytes(old, candidate, binding(old, candidate))


@pytest.mark.parametrize("option", ["--unified-market-state-root", "--unified-market-state-root=/x",
                                   "--unified-market-state-r", "--unified-market-state=/x"])
def test_reject_old_full_or_abbreviated_state_flag(option):
    old = old_fixture().replace(b"'--existing'", repr(option).encode())
    with pytest.raises(ValueError, match="STATE_FLAG_ALREADY_PRESENT"):
        h.wrapper_expected_bytes(old, "a" * 40)


@pytest.mark.parametrize("revision", [h.OLD_REVISION, "a" * 7, "../releases/" + "a" * 40, "A" * 40])
def test_reject_nonexact_or_old_revision(revision):
    with pytest.raises(ValueError, match="EXACT_SOURCE_REQUIRED"):
        h.wrapper_expected_bytes(old_fixture(), revision)


@pytest.mark.parametrize("shape", ["missing_path", "duplicate_path", "dynamic_argv", "two_execs", "loop"])
def test_unsupported_old_wrapper_requires_review(shape):
    old = old_fixture()
    if shape == "missing_path":
        old = old.replace(h.OLD_REVISION.encode(), b"f" * 40)
    elif shape == "duplicate_path":
        old += ("# " + str(h.BASE / "releases" / h.OLD_REVISION)).encode()
    elif shape == "dynamic_argv":
        old = old.replace(b"'--existing'", b"os.environ['CLI']")
    elif shape == "two_execs":
        old += b"os.execv(PYTHON, [PYTHON, '-m', 'scripts.publish_research_snapshot'])\n"
    elif shape == "loop":
        old += b"while False: pass\n"
    with pytest.raises(ValueError):
        h.wrapper_expected_bytes(old, "a" * 40)


def test_hash_bindings_cannot_be_replaced_by_structural_success():
    old = old_fixture()
    new = h.wrapper_expected_bytes(old, "a" * 40)
    manifest = binding(old, new)
    manifest["wrapper_old_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="OLD_WRAPPER_CHANGED"):
        h.wrapper_candidate_bytes(old, new, manifest)
    manifest = binding(old, new)
    manifest["wrapper_new_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="EXACT_V4_WRAPPER_TRANSFORM_REQUIRED"):
        h.wrapper_candidate_bytes(old, new, manifest)


@pytest.mark.parametrize("tail", [
    [], [b"--unified-market-state-root", b"/tmp/x"],
    [b"--unified-market-state-root=/root/qtrade-local/market-publication"],
    [b"--unified-market-state-r", b"/root/qtrade-local/market-publication"],
    [b"--unified-market-state-root", b"/root/qtrade-local/market-publication", b"--injected"],
    [b"--unified-market-state-root", b"/root/qtrade-local/market-publication"] * 2,
])
def test_new_process_exact_state_pair_required(tail):
    args = [b"python", b"-m", b"scripts.publish_research_snapshot", *tail, b""]
    with pytest.raises(ValueError, match="EXACT_LOCAL_MARKET_STATE_ARGUMENTS_REQUIRED"):
        b.verify_market_state_argv(args, expected=True)


def test_old_and_new_argv_are_explicitly_distinct():
    old = [b"python", b"-m", b"scripts.publish_research_snapshot", b""]
    assert b.verify_market_state_argv(old, expected=False)["fixed_local_state_root"] is False
    new = old[:-1] + [b.FIXED_MARKET_STATE_FLAG, b.FIXED_MARKET_STATE_ROOT, b""]
    assert b.verify_market_state_argv(new, expected=True)["fixed_local_state_root"] is True
    with pytest.raises(ValueError, match="OLD_RELEASE_STATE_ARGUMENTS_FORBIDDEN"):
        b.verify_market_state_argv(new, expected=False)


def test_bridge_source_inventory_is_bound_without_demanding_new_modules_from_old_release():
    assert set(b.BASE_MODULES) < set(b.MODULES)
    assert b.MODULES["scripts.unified_market_checkpoint"] == "scripts/unified_market_checkpoint.py"
    assert b.MODULES["qtrade.research.market_validation_receipts"] == (
        "src/qtrade/research/market_validation_receipts.py")
    assert b.OPERATOR_SHA256 == sha((ROOT / "publisher_handover_v4_20261028.py").read_bytes())


@pytest.mark.parametrize("name", [
    "test_one_signal_drain_long_and_starting_pending_then_provisional",
    "test_response_receipt_failure_does_not_resignal", "test_changed_owner_is_not_retargeted",
])
def test_existing_lifecycle_contract_on_new_operator(monkeypatch, name):
    monkeypatch.setattr(legacy, "h", h)
    getattr(legacy, name)()


@pytest.mark.parametrize("failure", ["install_error", "signal_error"])
def test_ambiguous_mutation_never_replays(monkeypatch, failure):
    monkeypatch.setattr(legacy, "h", h)
    legacy.test_uncertain_operations_are_never_replayed(failure)


@pytest.mark.parametrize("phase", ["WRAPPER_INTENT", "TERM_INTENT"])
def test_crash_after_immutable_intent_never_replays(monkeypatch, phase):
    monkeypatch.setattr(legacy, "h", h)
    legacy.test_crash_after_durable_intent_does_not_replay(phase)


def test_bridge_rpc_response_loss_still_consumes_sole_term(monkeypatch):
    monkeypatch.setattr(legacy_bridge, "b", b)
    legacy_bridge.test_no_xmlrpc_implicit_term_retry_after_response_loss(monkeypatch)


def test_existing_lifecycle_gate_and_rpc_source_bytes_unchanged():
    original = (ROOT / "publisher_handover_draft_20261028.py").read_bytes()
    candidate = (ROOT / "publisher_handover_v4_20261028.py").read_bytes()
    assert original[original.index(b"def check_gates("):] == candidate[
        candidate.index(b"def check_gates("):]
    original_bridge = (ROOT / "publisher_handover_bridge_safe_module_v3_20261028.py").read_text()
    candidate_bridge = (ROOT / "publisher_handover_bridge_v4_20261028.py").read_text()
    assert original_bridge[original_bridge.index("class UnixPublisherRPC:"):original_bridge.index(
        "def runtime_probe(")] == candidate_bridge[candidate_bridge.index(
            "class UnixPublisherRPC:"):candidate_bridge.index("FIXED_MARKET_STATE_FLAG =")]
