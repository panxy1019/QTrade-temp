"""Pure local faults/mocks. No cluster transport or real production adapter I/O."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).with_name("publisher_handover_draft_20261028.py")
spec = importlib.util.spec_from_file_location("publisher_handover_draft", SOURCE)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def fixture():
    revision = "a" * 40
    attempt = str(h.EVIDENCE_PARENT / "publisher-handover-MOCK-20261028")
    freeze = {
        "freeze_sha256": sha("freeze"),
        "ledger_sha256": sha("ledger"),
        "catalog_sha256": sha("catalog"),
        "chunks": 104218,
        "batches": 4200,
    }
    filesystem = {
        "fstype": "nfs4",
        "production_equivalent": True,
        "mountinfo_sha256": sha("mount"),
        "production_mount_evidence_sha256": sha("mountproof"),
        "mount_id": "MOCK-mount",
        "device": "MOCK-device",
    }
    files = {
        "scripts/publish_unified_market.py": sha("exporter"),
        "scripts/unified_store.py": sha("parser"),
    }
    old = {
        "pid": 123,
        "start_ticks": "456",
        "ppid": 100,
        "cmdline_sha256": sha("old command"),
        "cwd_sha256": sha("old cwd"),
        "live_trading_enabled": False,
        "pythonpath_sha256": sha("old path"),
    }
    manifest = {
        "program": h.PROGRAM,
        "old_revision": h.OLD_REVISION,
        "new_revision": revision,
        "runtime_files": files,
        "required_freeze": freeze,
        "required_production_filesystem": filesystem,
        "unchanged_runtime_policy": h.RUNTIME_POLICY.copy(),
        "unchanged_runtime_policy_sha256": h.digest(h.RUNTIME_POLICY),
        "required_target_entries": 3000,
        "required_reviewed_files": files.copy(),
        "signal_contract": "ONE_NAMED_PROGRAM_TERM_NONATOMIC_NO_REPLAY",
        "named_rpc_replacement_race_acknowledged": True,
        "attempt_root": attempt,
        "wrapper_target": str(h.WRAPPER),
        "wrapper_candidate": attempt + "/wrapper.candidate.py",
        "release_root": str(h.BASE / "releases" / revision),
        "wrapper_old_sha256": sha("old wrapper"),
        "wrapper_new_sha256": sha("new wrapper"),
        "old_owner": old,
        "supervisor_config_files": {
            "/etc/supervisor/supervisord.conf": sha("mock main config"),
            "/etc/supervisor/conf.d/qtrade-research-publication.conf": sha("mock program config"),
        },
    }
    gates = {
        "source_revision": revision,
        "source_inventory_sha256": h.digest(files),
        "freeze": freeze.copy(),
    }
    for phase in ("cold", "warm"):
        gates[phase] = {
            "state": "SUCCEEDED",
            "source_revision": revision,
            "freeze_sha256": freeze["freeze_sha256"],
            "elapsed_seconds": 40.0,
            "wall_seconds": 40.5,
            "profiled": False,
            "runtime_policy_sha256": h.digest(h.RUNTIME_POLICY),
            "filesystem": filesystem.copy(),
            "output_isolated_from_production": True,
            "output_directory_sha256": sha("isolated output"),
            "output_index_sha256": sha(phase + "index"),
            "output_objects_manifest_sha256": sha(phase + "objects"),
            "measured": {
                "source_bytes_read": 450000000,
                "cache_peak_rows": 80000,
                "cache_peak_estimated_bytes": 120000000,
            },
        }
    gates["cold"].update(
        {
            "new_process": True,
            "initial_object_count": 0,
            "initial_decode_cache_entries": 0,
            "initial_decode_cache_estimated_bytes": 0,
            "prior_manual_prewarm": False,
        }
    )
    gates["current_oracle"] = {
        "state": "PASS",
        "complete": True,
        "source_revision": revision,
        "freeze_sha256": freeze["freeze_sha256"],
        "target_entries": 3000,
        "all_raw_normalized_shards_lineage_verified": True,
        "audit_manifest_sha256": sha("audit"),
    }
    for phase in ("cold", "warm"):
        prefix = "cold_" if phase == "cold" else ""
        for key in ("output_index_sha256", "output_objects_manifest_sha256"):
            gates["current_oracle"][prefix + key] = gates[phase][key]
    for name in ("tests", "source_import_audit", "independent_review"):
        gates[name] = {"state": "PASS", "source_revision": revision, "evidence_sha256": sha(name)}
    gates["independent_review"]["reviewed_files"] = files.copy()
    gates["supervisor_semantics"] = {
        "state": "PASS",
        "binary_sha256": h.BINARY_SHA256,
        "method": "supervisor.signalProcess",
        "expected_pid_cas": False,
        "evidence_sha256": sha("semantics"),
    }
    gates["supervisor_runtime"] = {
        "binary_sha256": h.BINARY_SHA256,
        "settings": h.SUPERVISOR_SETTINGS.copy(),
        "config_files": manifest["supervisor_config_files"].copy(),
        "live_trading_enabled": False,
        "evidence_sha256": sha("mock current supervisor config evidence"),
    }
    rebind(manifest, gates)
    return manifest, gates


def rebind(manifest, gates):
    manifest["gate_bundle_sha256"] = h.digest(gates)


def put(target, path, value):
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value


class MemoryJournal:
    def __init__(self, manifest):
        self.root = Path(manifest["attempt_root"])
        self.rows = []
        self.fail_after = None
        self.fail_before = None

    def read(self):
        return copy.deepcopy(self.rows)

    def append(self, phase, payload):
        if phase == self.fail_before:
            raise OSError("mock durable write failed before record")
        assert not any(row["phase"] == phase for row in self.rows)
        self.rows.append(copy.deepcopy({"phase": phase, "payload": payload}))
        if phase == self.fail_after:
            raise OSError("mock interrupt after durable record")


def controls():
    return {
        "owners": {
            "publisher": {"pid": 123, "start_ticks": "456"},
            "workflow": {"pid": 900, "start_ticks": "901"},
        },
        "context": {
            "active": sha("model"),
            "data_version": sha("data"),
            "model_states": {"mock": "HALTED"},
        },
        "frozen_controls": {
            "books": {"mock": sha("books")},
            "rolling": ["OFF"],
            "forward_instance_id": "MOCK",
            "legacy_status": {},
        },
    }


class MockAdapter:
    def __init__(self):
        self.installs = 0
        self.signals = 0
        self.guards = 0
        self.state = "DRAIN_PENDING"
        self.install_error = False
        self.signal_error = False
        self.changed_owner = False
        self.before = controls()

    def guard_old(self):
        self.guards += 1
        h.require(not self.changed_owner, "ORIGINAL_OWNER_CHANGED")

    def check_release(self):
        pass

    def controls(self):
        return copy.deepcopy(self.before)

    def install_wrapper(self, before, backup):
        assert before == self.before and backup.name == "wrapper.old.py"
        self.installs += 1
        if self.install_error:
            raise OSError("mock uncertain replace")
        return {"installed_sha256": sha("installed")}

    def named_term_once(self, before):
        assert before == self.before
        self.signals += 1
        if self.signal_error:
            raise TimeoutError("mock RPC response lost after possible delivery")
        return {"reply_is_delivery_proof": False, "atomic_expected_pid_cas": False}

    def observe(self, before):
        assert before == self.before
        return {"state": self.state}


def step(manifest, gates, adapter, journal):
    return h.advance(
        manifest, gates, adapter, journal, backup=journal.root / "private/wrapper.old.py"
    )


def test_pass_is_preparation_only():
    manifest, gates = fixture()
    result = h.check_gates(manifest, gates)
    assert result["state"] == "GATES_PASS_PREPARATION_ONLY"
    assert result["production_apply_authorized_by_check"] is False
    assert result["live_trading_enabled"] is False


@pytest.mark.parametrize(
    "where,path,value",
    [
        ("m", ("new_revision",), "uncommitted:123"),
        ("m", ("new_revision",), "a" * 7),
        ("m", ("new_revision",), h.OLD_REVISION),
        ("m", ("program",), "all"),
        ("m", ("wrapper_target",), "/tmp/other-wrapper"),
        ("m", ("release_root",), "/tmp/release"),
        ("m", ("attempt_root",), "/root/qtrade-local/operator-evidence/old-attempt"),
        ("m", ("unchanged_runtime_policy", "export_seconds"), 46),
        ("m", ("unchanged_runtime_policy", "source_byte_budget"), 512 * 1024 * 1024),
        ("m", ("unchanged_runtime_policy", "git_seconds"), 91),
        ("m", ("unchanged_runtime_policy_sha256",), "arbitrary"),
        ("m", ("required_production_filesystem", "fstype"), "overlay"),
        ("g", ("cold", "state"), "FAILED"),
        ("g", ("warm", "state"), "PENDING"),
        ("g", ("cold", "elapsed_seconds"), 45.0001),
        ("g", ("cold", "wall_seconds"), 45.0001),
        ("g", ("warm", "wall_seconds"), True),
        ("g", ("cold", "profiled"), True),
        ("g", ("cold", "filesystem", "mount_id"), "wrong-mount"),
        ("g", ("warm", "output_isolated_from_production"), False),
        ("g", ("cold", "new_process"), False),
        ("g", ("cold", "initial_object_count"), 1),
        ("g", ("cold", "initial_decode_cache_entries"), 1),
        ("g", ("cold", "initial_decode_cache_estimated_bytes"), 1),
        ("g", ("cold", "prior_manual_prewarm"), True),
        ("g", ("warm", "output_directory_sha256"), sha("other-destination")),
        ("g", ("cold", "measured", "source_bytes_read"), 512000001),
        ("g", ("cold", "measured", "cache_peak_rows"), 100001),
        ("g", ("warm", "measured", "cache_peak_estimated_bytes"), 128000001),
        ("g", ("current_oracle", "complete"), False),
        ("g", ("current_oracle", "freeze_sha256"), sha("old-2694-oracle")),
        ("g", ("current_oracle", "target_entries"), 2694),
        ("g", ("current_oracle", "output_index_sha256"), sha("old-5986-index")),
        ("g", ("current_oracle", "cold_output_objects_manifest_sha256"), sha("other-objects")),
        ("g", ("current_oracle", "all_raw_normalized_shards_lineage_verified"), False),
        ("g", ("independent_review", "reviewed_files"), {}),
        ("g", ("source_import_audit", "source_revision"), "b" * 40),
        ("g", ("supervisor_semantics", "expected_pid_cas"), True),
        ("g", ("supervisor_runtime", "settings", "autorestart"), False),
        ("g", ("supervisor_runtime", "settings", "stopwaitsecs"), 111),
        ("g", ("supervisor_runtime", "live_trading_enabled"), True),
        ("m", ("named_rpc_replacement_race_acknowledged",), False),
    ],
)
def test_reject_gate_fault_before_actions(where, path, value):
    manifest, gates = fixture()
    put(manifest if where == "m" else gates, path, value)
    rebind(manifest, gates)
    adapter = MockAdapter()
    with pytest.raises((ValueError, KeyError)):
        step(manifest, gates, adapter, MemoryJournal(manifest))
    assert adapter.installs == adapter.signals == 0


def test_gate_bundle_tamper_is_rejected():
    manifest, gates = fixture()
    gates["cold"]["wall_seconds"] = 20
    with pytest.raises(ValueError, match="GATE_BUNDLE_CHANGED"):
        h.check_gates(manifest, gates)


def test_live_switch_rejected(monkeypatch):
    manifest, gates = fixture()
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(ValueError, match="LIVE_DISABLED_REQUIRED"):
        h.check_gates(manifest, gates)


def test_one_signal_drain_long_and_starting_pending_then_provisional():
    manifest, gates = fixture()
    adapter, journal = MockAdapter(), MemoryJournal(manifest)
    for state in ["DRAIN_PENDING"] * 3 + ["OLD_EXIT_OBSERVED", "PROVISIONAL_RUNTIME_READY"]:
        adapter.state = state
        result = step(manifest, gates, adapter, journal)
        assert result["state"] == state
        assert result["before_controls_restored_or_rewritten"] is False
        assert result["functional_acceptance"].startswith("SEPARATE_")
    assert adapter.signals == adapter.installs == 1
    assert [row["phase"] for row in journal.rows].count("TERM_INTENT") == 1


@pytest.mark.parametrize("failure", ["install_error", "signal_error"])
def test_uncertain_operations_are_never_replayed(failure):
    manifest, gates = fixture()
    adapter, journal = MockAdapter(), MemoryJournal(manifest)
    setattr(adapter, failure, True)
    for _ in range(3):
        assert "UNCERTAIN" in step(manifest, gates, adapter, journal)["state"]
    assert adapter.installs == 1
    assert adapter.signals == (1 if failure == "signal_error" else 0)


@pytest.mark.parametrize("phase", ["WRAPPER_INTENT", "TERM_INTENT"])
def test_crash_after_durable_intent_does_not_replay(phase):
    manifest, gates = fixture()
    adapter, journal = MockAdapter(), MemoryJournal(manifest)
    journal.fail_after = phase
    with pytest.raises(OSError):
        step(manifest, gates, adapter, journal)
    journal.fail_after = None
    assert "UNCERTAIN" in step(manifest, gates, adapter, journal)["state"]
    assert adapter.signals == 0
    assert adapter.installs == (1 if phase == "TERM_INTENT" else 0)


def test_response_receipt_failure_does_not_resignal():
    manifest, gates = fixture()
    adapter, journal = MockAdapter(), MemoryJournal(manifest)
    journal.fail_before = "TERM_RESULT"
    with pytest.raises(OSError):
        step(manifest, gates, adapter, journal)
    journal.fail_before = None
    assert step(manifest, gates, adapter, journal)["state"] == "TERM_DELIVERY_UNCERTAIN_NO_REPLAY"
    assert adapter.signals == 1


def test_changed_owner_is_not_retargeted():
    manifest, gates = fixture()
    adapter, journal = MockAdapter(), MemoryJournal(manifest)
    adapter.changed_owner = True
    with pytest.raises(ValueError, match="ORIGINAL_OWNER_CHANGED"):
        step(manifest, gates, adapter, journal)
    assert not journal.rows and adapter.signals == adapter.installs == 0


def test_existing_attempt_cannot_bind_different_evidence():
    manifest, gates = fixture()
    adapter, journal = MockAdapter(), MemoryJournal(manifest)
    step(manifest, gates, adapter, journal)
    gates["cold"]["wall_seconds"] = 40.25
    rebind(manifest, gates)
    with pytest.raises(ValueError, match="ATTEMPT_BINDING_CHANGED"):
        step(manifest, gates, adapter, journal)
    assert adapter.signals == 1


def wrapper_fixture():
    manifest, _ = fixture()
    old_path = str(h.BASE / "releases" / h.OLD_REVISION)
    old = (
        "import os\nROOT = '" + old_path + "'\nos.execv('/mock/python', ['python', '-m', "
        "'scripts.publish_research_snapshot'])\n"
    ).encode()
    new = old.replace(old_path.encode(), manifest["release_root"].encode())
    manifest.update(
        wrapper_old_sha256=hashlib.sha256(old).hexdigest(),
        wrapper_new_sha256=hashlib.sha256(new).hexdigest(),
    )
    return manifest, old, new


def test_exact_wrapper_bytes_allow_only_one_path_change():
    manifest, old, new = wrapper_fixture()
    assert h.wrapper_candidate_bytes(old, new, manifest) == new


@pytest.mark.parametrize(
    "kind", ["append", "policy", "multiple_old_paths", "wrong_old_hash", "wrong_new_hash"]
)
def test_wrapper_byte_fault_rejected_even_if_new_hash_rebound(kind):
    manifest, old, new = wrapper_fixture()
    if kind == "append":
        new += b"# unrelated change\n"
    elif kind == "policy":
        new = new.replace(b"scripts.publish_research_snapshot", b"scripts.other")
    elif kind == "multiple_old_paths":
        old += b"# " + str(h.BASE / "releases" / h.OLD_REVISION).encode()
        manifest["wrapper_old_sha256"] = hashlib.sha256(old).hexdigest()
    elif kind == "wrong_old_hash":
        manifest["wrapper_old_sha256"] = sha("wrong")
    if kind != "wrong_new_hash":
        manifest["wrapper_new_sha256"] = hashlib.sha256(new).hexdigest()
    else:
        manifest["wrapper_new_sha256"] = sha("wrong")
    with pytest.raises(ValueError):
        h.wrapper_candidate_bytes(old, new, manifest)


def test_comparison_preserves_natural_changes_without_rollback():
    before = controls()
    after = copy.deepcopy(before)
    new = {"pid": 124, "start_ticks": "457"}
    after["owners"]["publisher"] = new.copy()
    after["context"]["data_version"] = sha("natural closing update")
    after["frozen_controls"]["books"]["mock"] = sha("natural book update")
    untouched = copy.deepcopy(after)
    result = h.compare_controls(before, after, new)
    assert result["unrelated_owners_equal"] and result["publisher_matches_observed_new"]
    assert not result["context_equal"] and not result["books_equal"]
    assert result["independent_review_required"] and not result["automatic_restore_or_rewrite"]
    assert after == untouched


def mock_real_adapter(monkeypatch):
    """Only injected in-memory callbacks; never run real file/proc/config operations."""
    manifest, _ = fixture()
    reader = SimpleNamespace()
    calls = []
    native = {"name": h.PROGRAM, "pid": 124, "state": 20, "statename": "RUNNING"}
    new = {**manifest["old_owner"], "pid": 124, "start_ticks": "457"}
    proof = {
        "pid": 124,
        "start_ticks": "457",
        "source_revision": manifest["new_revision"],
        "runtime_files": manifest["runtime_files"],
        "cwd_and_pythonpath_match": True,
        "imports_match": True,
        "live_trading_enabled": False,
    }

    def rpc(method, args):
        calls.append((method, args))
        assert method == "supervisor.getProcessInfo"
        return native.copy()

    adapter = h.PublisherOnlyAdapter(manifest, reader, rpc, running_probe=lambda pid: proof.copy())
    monkeypatch.setattr(adapter, "owner", lambda pid: new.copy() if pid == 124 else None)
    monkeypatch.setattr(adapter, "guard_config", lambda: None)
    monkeypatch.setattr(adapter, "wrapper_hash", lambda: manifest["wrapper_new_sha256"])
    after = controls()
    after["owners"]["publisher"] = {"pid": 124, "start_ticks": "457"}
    monkeypatch.setattr(adapter, "controls", lambda: copy.deepcopy(after))
    return adapter, manifest, native, new, proof, calls


@pytest.mark.parametrize("status,pid", [("STARTING", 124), ("RUNNING", 0)])
def test_native_starting_never_runtime_ready(monkeypatch, status, pid):
    adapter, _, native, _, _, _ = mock_real_adapter(monkeypatch)
    native.update(state=10 if status == "STARTING" else 20, statename=status, pid=pid)
    assert adapter.observe(controls())["state"] == "OLD_EXIT_OBSERVED"


def test_source_proof_and_postprobe_identity_required(monkeypatch):
    adapter, _, _, new, proof, _ = mock_real_adapter(monkeypatch)
    proof["imports_match"] = False
    with pytest.raises(ValueError, match="ACTUAL_NEW_RUNTIME_PROOF_REQUIRED"):
        adapter.observe(controls())
    proof["imports_match"] = True

    def changed_during_probe(pid):
        new["start_ticks"] = "reused"
        return proof.copy()

    adapter.running_probe = changed_during_probe
    with pytest.raises(ValueError, match="NEW_OWNER_CHANGED_DURING_PROBE"):
        adapter.observe(controls())


def test_valid_runtime_is_explicitly_provisional(monkeypatch):
    adapter, _, _, _, _, calls = mock_real_adapter(monkeypatch)
    result = adapter.observe(controls())
    assert result["state"] == "PROVISIONAL_RUNTIME_READY"
    assert result["extra_restart_not_excluded_by_single_snapshot"] is True
    assert "old_natural_exit_disposition" in result["required_evidence"]
    assert result["handover_acceptance"] == "PENDING_INDEPENDENT_PRODUCTION_EVIDENCE"
    assert len(calls) == 2


def test_actual_new_live_must_agree_with_callback(monkeypatch):
    adapter, _, _, new, proof, _ = mock_real_adapter(monkeypatch)
    new["live_trading_enabled"] = True
    assert proof["live_trading_enabled"] is False
    with pytest.raises(ValueError, match="ACTUAL_NEW_RUNTIME_PROOF_REQUIRED"):
        adapter.observe(controls())


def test_old_guard_rechecks_native_running_and_identity(monkeypatch):
    adapter, manifest, native, _, _, _ = mock_real_adapter(monkeypatch)
    native["pid"] = manifest["old_owner"]["pid"]
    monkeypatch.setattr(adapter, "owner", lambda pid: manifest["old_owner"].copy())
    adapter.guard_old()
    native["state"] = 10
    native["statename"] = "STARTING"
    with pytest.raises(ValueError, match="ORIGINAL_NATIVE_RUNNING_REQUIRED"):
        adapter.guard_old()
    native.update(state=20, statename="RUNNING", pid=999)
    with pytest.raises(ValueError, match="ORIGINAL_NATIVE_RUNNING_REQUIRED"):
        adapter.guard_old()


def test_default_mutation_denial_and_literal_rpc_only(monkeypatch):
    adapter, _, _, _, _, _ = mock_real_adapter(monkeypatch)
    with pytest.raises(ValueError, match="MUTATION_STAGE_NOT_APPROVED"):
        adapter.named_term_once(controls())
    adapter.mutation_stage_approved = True
    monkeypatch.setattr(adapter, "guard_old", lambda: None)
    monkeypatch.setattr(adapter, "check_source", lambda: None)
    monkeypatch.setattr(adapter, "controls", controls)
    calls = []
    adapter.rpc = lambda method, args: calls.append((method, args)) or {"Success": True}
    receipt = adapter.named_term_once(controls())
    assert calls == [("supervisor.signalProcess", (h.PROGRAM, "TERM"))]
    assert (
        receipt["reply_is_delivery_proof"] is False and receipt["atomic_expected_pid_cas"] is False
    )


def test_durable_directory_syncs_all_ancestors_before_use(monkeypatch):
    class Directory:
        def __init__(self, name):
            self.name = name
            self.parents = ()

        def is_absolute(self):
            return True

        def is_symlink(self):
            return False

        def is_dir(self):
            return True

        def stat(self):
            return SimpleNamespace(st_mode=0o40700)

        def mkdir(self, **kwargs):
            events.append(("mkdir", self.name, kwargs))

    root, parent, grandparent = Directory("attempt"), Directory("parent"), Directory("root")
    root.parents = (parent, grandparent)
    events = []
    monkeypatch.setattr(h, "fsync_directory", lambda path: events.append(("fsync", path.name)))
    h.durable_directory(root, create=True)
    assert events[0][0] == "mkdir"
    assert events[1:] == [("fsync", "attempt"), ("fsync", "parent"), ("fsync", "root")]

    def fail(path):
        raise OSError("directory sync failure")

    monkeypatch.setattr(h, "fsync_directory", fail)
    with pytest.raises(OSError):
        h.JsonPhaseJournal(root)


def test_local_journal_immutable_and_hash_check(tmp_path, monkeypatch):
    # Local throwaway phase files; Linux directory-fsync leaf replaced by recorder.
    syncs = []
    monkeypatch.setattr(h, "durable_directory", lambda path, **kwargs: path.mkdir())
    monkeypatch.setattr(h, "fsync_directory", lambda path: syncs.append(str(path)))
    monkeypatch.setattr(h.os, "O_NOFOLLOW", 0, raising=False)
    journal = h.JsonPhaseJournal(tmp_path / "attempt")
    journal.append("PREPARED", {"mock": True})
    assert syncs and journal.read()[0]["phase"] == "PREPARED"
    with pytest.raises(ValueError, match="PHASE_IMMUTABLE"):
        journal.append("PREPARED", {})
    path = next(journal.root.glob("*.json"))
    raw = json.loads(path.read_bytes())
    raw["body"]["payload"]["mock"] = False
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="JOURNAL_CHANGED"):
        journal.read()


def test_cli_check_only_has_no_apply(tmp_path):
    manifest, gates = fixture()
    first, second = tmp_path / "manifest.json", tmp_path / "gates.json"
    first.write_text(json.dumps(manifest))
    second.write_text(json.dumps(gates))
    command = [
        sys.executable,
        str(SOURCE),
        "--check",
        "--manifest",
        str(first),
        "--gates",
        str(second),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["production_apply_authorized_by_check"] is False
    result = subprocess.run(command + ["--apply"], capture_output=True, text=True, timeout=10)
    assert result.returncode != 0 and "unrecognized arguments" in result.stderr
