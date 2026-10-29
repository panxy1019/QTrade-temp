"""Review draft: publisher-only handover. CLI is --check only, never apply.

Real adapter functions are provided for review but are never instantiated by the
CLI. Production use needs complete source/current-freeze gates,
reviewed reader/RPC bindings and review of
the remaining named-RPC check-to-call race. No pidfd or stop fallback exists.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import re
import stat
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

PROGRAM = "qtrade-research-publication"
OLD_REVISION = "b2386c3e53c766d9e9512e6ca0f5116583aa13c2"
BINARY_SHA256 = "c0e12d2e498d00a2285161372cbb0a46b795b94e9a42511351eba7980f408569"
BASE = PurePosixPath("/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade")
WRAPPER = BASE / "private/run-publisher-workflow.py"
EVIDENCE_PARENT = PurePosixPath("/root/qtrade-local/operator-evidence")
RUNTIME_POLICY = {
    "export_seconds": 45,
    "source_byte_budget": 512_000_000,
    "max_cache_rows": 100_000,
    "max_cache_estimated_bytes": 128_000_000,
    "git_seconds": 90,
    "git_cleanup_term_seconds": 2,
    "git_cleanup_kill_seconds": 2,
}
SUPERVISOR_SETTINGS = {
    "autostart": True,
    "autorestart": True,
    "stopsignal": "TERM",
    "stopwaitsecs": 110,
    "stopasgroup": True,
    "killasgroup": True,
    "publisher_interval_seconds": 60,
}
AUTHORITY = {
    "live_trading_enabled": False,
    "external_order_transmission": False,
    "paper_recovery": False,
    "production_apply_authorized_by_check": False,
}


def require(value: bool, code: str) -> None:
    if not value:
        raise ValueError("PUBLISHER_HANDOVER_" + code)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def file_sha(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def identity(value: Any, size: int = 64) -> bool:
    return isinstance(value, str) and re.fullmatch("[a-f0-9]{" + str(size) + "}", value) is not None


def live_off() -> None:
    require(
        os.environ.get("LIVE_TRADING_ENABLED", "false").lower() == "false", "LIVE_DISABLED_REQUIRED"
    )


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_directory(path: Path, *, create: bool = False) -> None:
    """Fsync new leaf plus each containing entry before any subsequent action."""
    require(
        path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)),
        "DIRECTORY_ALIAS",
    )
    if create:
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
    require(
        path.is_dir() and stat.S_IMODE(path.stat().st_mode) == 0o700, "PRIVATE_DIRECTORY_REQUIRED"
    )
    for directory in (path, *path.parents):
        fsync_directory(directory)


def wrapper_candidate_bytes(old: bytes, candidate: bytes, manifest: dict[str, Any]) -> bytes:
    """Pure check: retain every old byte except its one exact release pathname."""
    old_path = str(BASE / "releases" / OLD_REVISION).encode()
    new_path = str(BASE / "releases" / manifest["new_revision"]).encode()
    require(
        len(old) <= 65536 and hashlib.sha256(old).hexdigest() == manifest["wrapper_old_sha256"],
        "OLD_WRAPPER_CHANGED",
    )
    require(
        old.count(old_path) == 1
        and candidate == old.replace(old_path, new_path, 1)
        and hashlib.sha256(candidate).hexdigest() == manifest["wrapper_new_sha256"],
        "EXACT_WRAPPER_REPLACEMENT_REQUIRED",
    )
    tree = ast.parse(candidate)
    calls = [node.func for node in ast.walk(tree) if isinstance(node, ast.Call)]
    require(
        any(
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
            and node.attr == "execv"
            for node in calls
        )
        and not any(
            isinstance(node, (ast.For, ast.While, ast.AsyncFor)) for node in ast.walk(tree)
        ),
        "REVIEWED_EXEC_WRAPPER_REQUIRED",
    )
    strings = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)]
    require(
        "-m" in strings and "scripts.publish_research_snapshot" in strings,
        "REVIEWED_PUBLISHER_MODULE_REQUIRED",
    )
    return candidate


def check_gates(manifest: dict[str, Any], gates: dict[str, Any]) -> dict[str, Any]:
    """No defaults for source inventory, test count, required freeze or old oracles."""
    live_off()
    require(manifest["program"] == PROGRAM, "PUBLISHER_ONLY")
    revision = manifest["new_revision"]
    require(
        identity(revision, 40)
        and revision != OLD_REVISION
        and manifest["old_revision"] == OLD_REVISION,
        "EXACT_SOURCE_REQUIRED",
    )
    attempt = PurePosixPath(manifest["attempt_root"])
    require(
        attempt.is_absolute()
        and attempt.parent == EVIDENCE_PARENT
        and attempt.name.startswith("publisher-handover-")
        and ".." not in attempt.parts
        and PurePosixPath(manifest["wrapper_target"]) == WRAPPER
        and PurePosixPath(manifest["release_root"]) == BASE / "releases" / revision
        and PurePosixPath(manifest["wrapper_candidate"]) == attempt / "wrapper.candidate.py",
        "FIXED_PUBLISHER_PATHS_REQUIRED",
    )
    require(manifest["gate_bundle_sha256"] == digest(gates), "GATE_BUNDLE_CHANGED")
    inventory = manifest["runtime_files"]
    require(
        isinstance(inventory, dict)
        and bool(inventory)
        and all(
            isinstance(name, str)
            and not PurePosixPath(name).is_absolute()
            and ".." not in PurePosixPath(name).parts
            and "\\" not in name
            and ":" not in name
            and identity(sha)
            for name, sha in inventory.items()
        ),
        "SOURCE_INVENTORY_INVALID",
    )
    require(
        gates["source_revision"] == revision
        and gates["source_inventory_sha256"] == digest(inventory),
        "GATE_SOURCE_MISMATCH",
    )
    require(
        manifest["unchanged_runtime_policy"] == RUNTIME_POLICY
        and manifest["unchanged_runtime_policy_sha256"] == digest(RUNTIME_POLICY),
        "UNCHANGED_NUMERIC_POLICY_REQUIRED",
    )
    filesystem = manifest["required_production_filesystem"]
    require(
        filesystem["fstype"] in {"nfs", "nfs4"}
        and filesystem["production_equivalent"] is True
        and all(
            identity(filesystem[key])
            for key in ("mountinfo_sha256", "production_mount_evidence_sha256")
        )
        and isinstance(filesystem["mount_id"], str)
        and bool(filesystem["mount_id"])
        and isinstance(filesystem["device"], str)
        and bool(filesystem["device"]),
        "PRODUCTION_FILESYSTEM_EVIDENCE_REQUIRED",
    )
    freeze = manifest["required_freeze"]
    require(
        gates["freeze"] == freeze
        and all(
            identity(freeze[key]) for key in ("freeze_sha256", "ledger_sha256", "catalog_sha256")
        )
        and all(type(freeze[key]) is int and freeze[key] > 0 for key in ("chunks", "batches")),
        "CURRENT_FREEZE_MISMATCH",
    )
    for phase in ("cold", "warm"):
        value = gates[phase]
        times = [value["elapsed_seconds"], value["wall_seconds"]]
        require(
            value["state"] == "SUCCEEDED"
            and value["source_revision"] == revision
            and value["freeze_sha256"] == freeze["freeze_sha256"]
            and all(type(t) in (int, float) and math.isfinite(t) and 0 <= t <= 45 for t in times)
            and value["profiled"] is False
            and value["runtime_policy_sha256"] == manifest["unchanged_runtime_policy_sha256"],
            "BOTH_UNPROFILED_45S_GATES_REQUIRED",
        )
        require(
            value["filesystem"] == filesystem
            and value["output_isolated_from_production"] is True
            and identity(value["output_directory_sha256"])
            and identity(value["output_index_sha256"])
            and identity(value["output_objects_manifest_sha256"]),
            "PRODUCTION_FILESYSTEM_OUTPUT_REQUIRED",
        )
        measurements = value["measured"]
        for key, cap in (
            ("source_bytes_read", 512_000_000),
            ("cache_peak_rows", 100_000),
            ("cache_peak_estimated_bytes", 128_000_000),
        ):
            require(
                type(measurements[key]) is int and 0 <= measurements[key] <= cap,
                "MEASURED_BUDGET_EXCEEDED",
            )
    require(
        gates["cold"]["new_process"] is True
        and gates["cold"]["initial_object_count"] == 0
        and type(gates["cold"]["initial_object_count"]) is int
        and gates["cold"]["initial_decode_cache_entries"] == 0
        and type(gates["cold"]["initial_decode_cache_entries"]) is int
        and gates["cold"]["initial_decode_cache_estimated_bytes"] == 0
        and type(gates["cold"]["initial_decode_cache_estimated_bytes"]) is int
        and gates["cold"]["prior_manual_prewarm"] is False,
        "NEW_PROCESS_EMPTY_DESTINATION_AND_DECODE_REQUIRED",
    )
    require(
        gates["cold"]["output_directory_sha256"] == gates["warm"]["output_directory_sha256"],
        "BENCHMARK_DESTINATION_CHANGED",
    )
    audit = gates["current_oracle"]
    require(
        audit["state"] == "PASS"
        and audit["complete"] is True
        and audit["source_revision"] == revision
        and audit["freeze_sha256"] == freeze["freeze_sha256"]
        and audit["target_entries"] == manifest["required_target_entries"]
        and audit["all_raw_normalized_shards_lineage_verified"] is True
        and audit["output_index_sha256"] == gates["warm"]["output_index_sha256"]
        and audit["output_objects_manifest_sha256"]
        == gates["warm"]["output_objects_manifest_sha256"]
        and audit["cold_output_index_sha256"] == gates["cold"]["output_index_sha256"]
        and audit["cold_output_objects_manifest_sha256"]
        == gates["cold"]["output_objects_manifest_sha256"]
        and identity(audit["audit_manifest_sha256"]),
        "COMPLETE_CURRENT_ORACLE_REQUIRED",
    )
    require(
        type(manifest["required_target_entries"]) is int
        and manifest["required_target_entries"] > 0,
        "EXPLICIT_TARGET_ENTRIES_REQUIRED",
    )
    for name in ("tests", "source_import_audit", "independent_review"):
        value = gates[name]
        require(
            value["state"] == "PASS"
            and value["source_revision"] == revision
            and identity(value["evidence_sha256"]),
            "SOURCE_BOUND_CHECKS_REQUIRED",
        )
    require(
        gates["independent_review"]["reviewed_files"] == manifest["required_reviewed_files"]
        and all(
            inventory.get(path) == sha for path, sha in manifest["required_reviewed_files"].items()
        ),
        "REVIEWED_BYTES_MISMATCH",
    )
    semantics = gates["supervisor_semantics"]
    require(
        semantics["state"] == "PASS"
        and semantics["binary_sha256"] == BINARY_SHA256
        and semantics["method"] == "supervisor.signalProcess"
        and semantics["expected_pid_cas"] is False
        and identity(semantics["evidence_sha256"]),
        "REVIEWED_NAMED_RPC_SEMANTICS_REQUIRED",
    )
    observed = gates["supervisor_runtime"]
    require(
        observed["binary_sha256"] == BINARY_SHA256
        and observed["settings"] == SUPERVISOR_SETTINGS
        and observed["config_files"] == manifest["supervisor_config_files"]
        and set(observed["config_files"])
        == {
            "/etc/supervisor/supervisord.conf",
            "/etc/supervisor/conf.d/qtrade-research-publication.conf",
        }
        and all(identity(value) for value in observed["config_files"].values())
        and observed["live_trading_enabled"] is False
        and identity(observed["evidence_sha256"]),
        "UNCHANGED_OBSERVED_SUPERVISOR_SETTINGS_REQUIRED",
    )
    require(
        manifest["signal_contract"] == "ONE_NAMED_PROGRAM_TERM_NONATOMIC_NO_REPLAY"
        and manifest["named_rpc_replacement_race_acknowledged"] is True,
        "NAMED_RPC_NONATOMIC_LIMIT_REQUIRED",
    )
    return {"state": "GATES_PASS_PREPARATION_ONLY", **AUTHORITY, "source_revision": revision}


class JsonPhaseJournal:
    """Linux exclusive, flushed and directory-fsynced immutable phase files."""

    def __init__(self, root: Path):
        self.root = root
        durable_directory(root, create=True)

    def read(self) -> list[dict[str, Any]]:
        rows = []
        for path in sorted(self.root.glob("[0-9][0-9][0-9]-*.json")):
            require(not path.is_symlink() and path.stat().st_nlink == 1, "JOURNAL_ALIAS")
            row = json.loads(path.read_bytes())
            require(row["sha256"] == digest(row["body"]), "JOURNAL_CHANGED")
            rows.append(row["body"])
        return rows

    def append(self, phase: str, payload: dict[str, Any]) -> None:
        rows = self.read()
        require(not any(row["phase"] == phase for row in rows), "PHASE_IMMUTABLE")
        body = {
            "phase": phase,
            "at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 - Linux Python 3.10
            **AUTHORITY,
            "payload": payload,
        }
        path = self.root / f"{len(rows):03d}-{phase}.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {"body": body, "sha256": digest(body)}, handle, sort_keys=True, allow_nan=False
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        fsync_directory(self.root)


class PublisherOnlyAdapter:
    """Real leaf operations. Reader must expose only reviewed readonly functions.

    Reuse reader.controls(), reader.frozen_controls(), reader.h.context(); never
    call old initialize_owner_map/stage or enumerate a generic service kind.
    rpc is an explicitly reviewed existing transport; no credentials are loaded.
    running_probe is the reviewed existing source/import-origin probe, bound to
    actual new PID/start_ticks; omission leaves NEW_RUNNING pending.
    """

    def __init__(
        self,
        manifest: dict[str, Any],
        reader: Any,
        rpc: Callable[[str, tuple[Any, ...]], Any],
        *,
        running_probe: Callable[[int], dict[str, Any]] | None = None,
        mutation_stage_approved: bool = False,
    ):
        self.manifest, self.reader, self.rpc = manifest, reader, rpc
        self.running_probe, self.mutation_stage_approved = running_probe, mutation_stage_approved

    def controls(self) -> dict[str, Any]:
        return {
            "owners": self.reader.controls(),
            "context": self.reader.h.context(),
            "frozen_controls": self.reader.frozen_controls(),
        }

    @staticmethod
    def owner(pid: int) -> dict[str, Any] | None:
        root = Path("/proc") / str(pid)
        try:
            fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
            environment = dict(
                value.split(b"=", 1)
                for value in (root / "environ").read_bytes().split(b"\0")
                if b"=" in value
            )
            return {
                "pid": pid,
                "start_ticks": fields[19],
                "ppid": int(fields[1]),
                "cmdline_sha256": hashlib.sha256((root / "cmdline").read_bytes()).hexdigest(),
                "cwd_sha256": hashlib.sha256(os.readlink(root / "cwd").encode()).hexdigest(),
                "live_trading_enabled": environment.get(b"LIVE_TRADING_ENABLED") != b"false",
                "pythonpath_sha256": hashlib.sha256(
                    environment.get(b"PYTHONPATH", b"")
                ).hexdigest(),
            }
        except FileNotFoundError:
            return None

    def guard_old(self) -> None:
        live_off()
        expected = self.manifest["old_owner"]
        require(expected["live_trading_enabled"] is False, "ORIGINAL_LIVE_DISABLED_REQUIRED")
        require(self.owner(expected["pid"]) == expected, "ORIGINAL_OWNER_CHANGED")
        native = self.rpc("supervisor.getProcessInfo", (PROGRAM,))
        require(
            native["name"] == PROGRAM
            and native["state"] == 20
            and native["statename"].upper() == "RUNNING"
            and native["pid"] == expected["pid"],
            "ORIGINAL_NATIVE_RUNNING_REQUIRED",
        )
        require(self.owner(expected["pid"]) == expected, "ORIGINAL_OWNER_CHANGED")

    def guard_config(self) -> None:
        """Hash reviewed current manager/program config bytes, never copy their bodies."""
        expected = self.manifest["supervisor_config_files"]
        require(isinstance(expected, dict) and bool(expected), "CONFIG_INVENTORY_REQUIRED")
        for name, sha in expected.items():
            path = Path(name)
            require(
                path.is_absolute()
                and not any(p.is_symlink() for p in (path, *path.parents))
                and identity(sha)
                and file_sha(path) == sha,
                "SUPERVISOR_CONFIG_CHANGED",
            )
        require(
            file_sha(Path("/usr/bin/supervisord")) == BINARY_SHA256, "SUPERVISOR_BINARY_CHANGED"
        )

    def _wrapper_raw(self) -> tuple[bytes, bytes]:
        paths = [Path(self.manifest["wrapper_target"]), Path(self.manifest["wrapper_candidate"])]
        for path in paths:
            require(
                not any(p.is_symlink() for p in (path, *path.parents))
                and path.is_file()
                and path.stat().st_nlink == 1
                and path.stat().st_size <= 65536,
                "WRAPPER_PATH_ALIAS_OR_SIZE",
            )
        old, candidate = (path.read_bytes() for path in paths)
        return old, wrapper_candidate_bytes(old, candidate, self.manifest)

    def check_source(self) -> None:
        root = Path(self.manifest["release_root"])
        require(
            root == BASE / "releases" / self.manifest["new_revision"]
            and not any(p.is_symlink() for p in (root, *root.parents)),
            "IMMUTABLE_NEW_RELEASE_PATH_REQUIRED",
        )
        for name, sha in self.manifest["runtime_files"].items():
            path = root / name
            require(
                not path.is_symlink()
                and path.resolve().is_relative_to(root.resolve())
                and file_sha(path) == sha,
                "ACTUAL_RELEASE_SOURCE_MISMATCH",
            )

    def check_release(self) -> None:
        self.check_source()
        self._wrapper_raw()
        self.guard_config()

    def wrapper_hash(self) -> str:
        path = Path(self.manifest["wrapper_target"])
        require(
            path == WRAPPER
            and not any(p.is_symlink() for p in (path, *path.parents))
            and path.is_file()
            and path.stat().st_nlink == 1,
            "WRAPPER_PATH_ALIAS",
        )
        return file_sha(path)

    def _mutation_guard(self) -> None:
        live_off()
        require(self.mutation_stage_approved, "MUTATION_STAGE_NOT_APPROVED")

    def install_wrapper(self, before: dict[str, Any], backup: Path) -> dict[str, Any]:
        self._mutation_guard()
        self.guard_old()
        self.check_release()
        require(self.controls() == before, "BEFORE_CONTROLS_CHANGED")
        target = Path(self.manifest["wrapper_target"])
        require(
            target == WRAPPER and not any(p.is_symlink() for p in (target, *target.parents)),
            "WRAPPER_PATH_ALIAS",
        )
        metadata = target.stat()
        require(
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_nlink == 1
            and self.wrapper_hash() == self.manifest["wrapper_old_sha256"],
            "OLD_WRAPPER_CHANGED",
        )
        require(
            backup == Path(self.manifest["attempt_root"]) / "private/wrapper.old.py",
            "BOUND_PRIVATE_BACKUP_REQUIRED",
        )
        durable_directory(backup.parent, create=True)
        old_raw, raw = self._wrapper_raw()
        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(old_raw)
            handle.flush()
            os.fsync(handle.fileno())
        require(
            backup.stat().st_size == len(old_raw)
            and file_sha(backup) == self.manifest["wrapper_old_sha256"],
            "BACKUP_BYTES_CHANGED",
        )
        fsync_directory(backup.parent)
        descriptor, temporary = tempfile.mkstemp(prefix=".publisher-wrapper-", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                os.fchmod(handle.fileno(), stat.S_IMODE(metadata.st_mode))
                os.fchown(handle.fileno(), metadata.st_uid, metadata.st_gid)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            require(
                file_sha(Path(temporary)) == self.manifest["wrapper_new_sha256"],
                "ACTUAL_WRITTEN_CANDIDATE_CHANGED",
            )
            self.guard_old()
            self.guard_config()
            require(
                self.controls() == before and target.read_bytes() == old_raw,
                "BEFORE_REPLACE_CHANGED",
            )
            os.replace(temporary, target)
            fsync_directory(target.parent)
        finally:
            if Path(temporary).exists():
                Path(temporary).unlink()
        require(
            self.wrapper_hash() == self.manifest["wrapper_new_sha256"], "INSTALLED_WRAPPER_CHANGED"
        )
        return {
            "installed_sha256": self.wrapper_hash(),
            "backup_sha256": file_sha(backup),
            "backup_mode": "0600",
            "backup_directory_fsynced": True,
            "old_owner_continues_old_release": True,
        }

    def named_term_once(self, before: dict[str, Any]) -> dict[str, Any]:
        self._mutation_guard()
        self.check_source()
        self.guard_config()
        require(self.controls() == before, "BEFORE_TERM_CONTROLS_CHANGED")
        require(self.wrapper_hash() == self.manifest["wrapper_new_sha256"], "NEW_WRAPPER_REQUIRED")
        self.guard_old()
        reply = self.rpc("supervisor.signalProcess", (PROGRAM, "TERM"))
        return {
            "rpc_reply": reply,
            "atomic_expected_pid_cas": False,
            "reply_is_delivery_proof": False,
            "must_observe_real_target_exit_and_new_running": True,
        }

    def observe(self, before: dict[str, Any]) -> dict[str, Any]:
        old = self.manifest["old_owner"]
        current = self.owner(old["pid"])
        if current is not None:
            require(current == old, "ORIGINAL_PID_REUSED_OR_CHANGED")
            return {"state": "DRAIN_PENDING", "old_owner": current}
        native = self.rpc("supervisor.getProcessInfo", (PROGRAM,))
        require(native["name"] == PROGRAM, "FIXED_NATIVE_PROGRAM_REQUIRED")
        if native["state"] != 20 or native["statename"].upper() != "RUNNING" or native["pid"] <= 0:
            return {"state": "OLD_EXIT_OBSERVED", "native_state": native["statename"]}
        new = self.owner(native["pid"])
        if new is None or self.running_probe is None:
            return {"state": "OLD_EXIT_OBSERVED", "runtime_import_proof": "PENDING"}
        proof = self.running_probe(native["pid"])
        require(
            new["live_trading_enabled"] is False
            and proof["pid"] == new["pid"]
            and proof["start_ticks"] == new["start_ticks"]
            and proof["source_revision"] == self.manifest["new_revision"]
            and proof["runtime_files"] == self.manifest["runtime_files"]
            and proof["cwd_and_pythonpath_match"] is True
            and proof["imports_match"] is True
            and proof["live_trading_enabled"] is False,
            "ACTUAL_NEW_RUNTIME_PROOF_REQUIRED",
        )
        self.guard_config()
        require(
            self.wrapper_hash() == self.manifest["wrapper_new_sha256"], "INSTALLED_WRAPPER_CHANGED"
        )
        after = self.controls()
        repeated = self.rpc("supervisor.getProcessInfo", (PROGRAM,))
        require(
            self.owner(new["pid"]) == new
            and all(repeated[key] == native[key] for key in ("name", "state", "statename", "pid")),
            "NEW_OWNER_CHANGED_DURING_PROBE",
        )
        comparison = compare_controls(before, after, new)
        return {
            "state": "PROVISIONAL_RUNTIME_READY",
            "new_owner": new,
            "runtime_proof": proof,
            "after_controls": after,
            "controls_comparison": comparison,
            "handover_acceptance": "PENDING_INDEPENDENT_PRODUCTION_EVIDENCE",
            "required_evidence": [
                "old_natural_exit_disposition",
                "owned_child_cleanup",
                "exactly_one_automatic_restart",
                "after_controls_independent_review",
                "ordinary_export_git_public_api_chain",
            ],
            "extra_restart_not_excluded_by_single_snapshot": True,
        }


def compare_controls(
    before: dict[str, Any], after: dict[str, Any], new: dict[str, Any]
) -> dict[str, Any]:
    old_owners = {key: value for key, value in before["owners"].items() if key != "publisher"}
    new_owners = {key: value for key, value in after["owners"].items() if key != "publisher"}
    publisher = {"pid": new["pid"], "start_ticks": new["start_ticks"]}
    result = {
        "unrelated_owners_equal": old_owners == new_owners,
        "publisher_matches_observed_new": after["owners"].get("publisher") == publisher,
        "context_equal": before["context"] == after["context"],
        "frozen_controls_equal": before["frozen_controls"] == after["frozen_controls"],
        "books_equal": before["frozen_controls"]["books"] == after["frozen_controls"]["books"],
        "changed_top_level": [key for key in before if before[key] != after.get(key)],
        "automatic_restore_or_rewrite": False,
    }
    result["independent_review_required"] = not all(
        result[key]
        for key in (
            "unrelated_owners_equal",
            "publisher_matches_observed_new",
            "context_equal",
            "frozen_controls_equal",
            "books_equal",
        )
    )
    return result


def advance(
    manifest: dict[str, Any], gates: dict[str, Any], adapter: Any, journal: Any, *, backup: Path
) -> dict[str, Any]:
    """One bounded step. Observation expiry never kills, stops, resumes or re-signals."""
    check_gates(manifest, gates)
    require(
        journal.root == Path(manifest["attempt_root"])
        and backup == journal.root / "private/wrapper.old.py",
        "ATTEMPT_PATH_BINDING_CHANGED",
    )
    rows = journal.read()
    by_phase = {row["phase"]: row for row in rows}
    binding = digest({"manifest": manifest, "gates": gates})
    if rows:
        require(rows[0]["payload"]["binding_sha256"] == binding, "ATTEMPT_BINDING_CHANGED")
    else:
        adapter.guard_old()
        adapter.check_release()
        before = adapter.controls()
        require({"owners", "context", "frozen_controls"} == set(before), "COMPLETE_BEFORE_REQUIRED")
        journal.append("PREPARED", {"binding_sha256": binding, "before": before})
        by_phase = {row["phase"]: row for row in journal.read()}
    before = by_phase["PREPARED"]["payload"]["before"]
    if "WRAPPER_INSTALLED" not in by_phase:
        if "WRAPPER_INTENT" in by_phase:
            return {"state": "WRAPPER_OUTCOME_UNCERTAIN_READONLY_REVIEW", **AUTHORITY}
        journal.append(
            "WRAPPER_INTENT",
            {
                "target_path_sha256": digest(manifest["wrapper_target"]),
                "old_sha256": manifest["wrapper_old_sha256"],
                "new_sha256": manifest["wrapper_new_sha256"],
            },
        )
        try:
            receipt = adapter.install_wrapper(before, backup)
        except Exception as exc:
            journal.append("WRAPPER_UNCERTAIN", {"error_type": type(exc).__name__})
            return {"state": "WRAPPER_OUTCOME_UNCERTAIN_READONLY_REVIEW", **AUTHORITY}
        journal.append("WRAPPER_INSTALLED", receipt)
        by_phase = {row["phase"]: row for row in journal.read()}
    if "TERM_INTENT" not in by_phase:
        adapter.guard_old()
        journal.append(
            "TERM_INTENT",
            {
                "program": PROGRAM,
                "signal": "TERM",
                "old_owner": manifest["old_owner"],
                "non_atomic_name_interface": True,
            },
        )
        try:
            receipt = adapter.named_term_once(before)
        except Exception as exc:
            journal.append("TERM_UNCERTAIN", {"error_type": type(exc).__name__})
            return {"state": "TERM_DELIVERY_UNCERTAIN_NO_REPLAY", **AUTHORITY}
        journal.append("TERM_RESULT", receipt)
        by_phase = {row["phase"]: row for row in journal.read()}
    if "TERM_RESULT" not in by_phase:
        return {"state": "TERM_DELIVERY_UNCERTAIN_NO_REPLAY", **AUTHORITY}
    observed = adapter.observe(before)
    phase = observed["state"]
    require(
        phase in {"DRAIN_PENDING", "OLD_EXIT_OBSERVED", "PROVISIONAL_RUNTIME_READY"},
        "OBSERVATION_INVALID",
    )
    if phase not in by_phase:
        journal.append(phase, observed)
    return {
        **observed,
        **AUTHORITY,
        "functional_acceptance": "SEPARATE_ORDINARY_AUTONOMOUS_EVIDENCE_REQUIRED",
        "before_controls_restored_or_rewritten": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--gates", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            check_gates(
                json.loads(args.manifest.read_bytes()), json.loads(args.gates.read_bytes())
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
