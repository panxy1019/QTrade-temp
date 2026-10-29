"""Publisher bridge: explicit check/readonly prepare only. No CLI apply operation."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib.util
import json
import os
import socket
import stat
import subprocess
import sys
import xmlrpc.client
from pathlib import Path
from types import SimpleNamespace

OPERATOR_SHA256 = "fe42741b681e51e5b662d380f3d59ad12c8a3ec6c380c27c6e6d253c4cd871f6"
READER = Path(
    "/root/qtrade-local/operator-evidence/graded-quality-458552d-20261028/rollout-operator-3dd14200cc7a8a6d.py"
)
READER_SHA256 = "3dd14200cc7a8a6db9aa6cdcbf8528795cfb8ea3f1be3f1b21ac0cca117acdf7"
CONTEXT = Path(
    "/root/qtrade-local/operator-evidence/forward-handover-9846a7f-20261028/directed_handover.py"
)
CONTEXT_SHA256 = "75fcf061c30138ae3157909f67608aa9d817148894994a3d7d58244528871811"
SOCKET = Path("/var/run/supervisor.sock")
PROGRAM = "qtrade-research-publication"
BASE = Path("/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade")
BASE_MODULES = {
    "scripts.publish_research_snapshot": "scripts/publish_research_snapshot.py",
    "scripts.publish_unified_market": "scripts/publish_unified_market.py",
    "scripts.unified_store": "scripts/unified_store.py",
    "qtrade.research.unified_market": "src/qtrade/research/unified_market.py",
    "qtrade.market_data.sina_incremental": "src/qtrade/market_data/sina_incremental.py",
}
MODULES = {**BASE_MODULES,
    "scripts.unified_market_checkpoint": "scripts/unified_market_checkpoint.py",
    "qtrade.research.market_validation_receipts": "src/qtrade/research/market_validation_receipts.py",
}


def require(value, code):
    if not value:
        raise ValueError("PUBLISHER_BRIDGE_" + code)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_module(name, path, expected):
    require(
        path.is_absolute() and not path.is_symlink() and sha(path) == expected,
        "REVIEWED_HELPER_CHANGED",
    )
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(sha(path) == expected, "REVIEWED_HELPER_CHANGED_DURING_IMPORT")
    return module


def readonly_reader():
    require(sha(CONTEXT) == CONTEXT_SHA256, "CONTEXT_HELPER_CHANGED")
    module = checked_module("publisher_reviewed_readonly_helpers", READER, READER_SHA256)
    require(
        Path(module.h.__file__) == CONTEXT and sha(CONTEXT) == CONTEXT_SHA256,
        "CONTEXT_BINDING_CHANGED",
    )
    # Do not expose stage, initialize_owner_map, ctl, service_tokens or recovery.
    return SimpleNamespace(
        controls=module.controls,
        frozen_controls=module.frozen_controls,
        h=SimpleNamespace(context=module.h.context),
    )


class UnixPublisherRPC:
    """Installed Unix endpoint and reviewed manual HTTP/XMLRPC path; NEVER retries.

    CLI constructs only readonly instances. Root may separately bind allow_one_term
    only after real gates and the adapter's durable TERM_INTENT. One instance also
    consumes its sole signal before I/O; an exception never permits a retry.
    """

    def __init__(self, expected_socket, *, allow_one_term=False):
        self.expected_socket, self.allow_one_term, self.term_consumed = (
            expected_socket,
            allow_one_term,
            False,
        )

    def __call__(self, method, arguments):
        if method == "supervisor.getProcessInfo":
            require(arguments == (PROGRAM,), "FIXED_READONLY_PROGRAM_REQUIRED")
        elif method == "supervisor.signalProcess":
            require(
                self.allow_one_term and not self.term_consumed and arguments == (PROGRAM, "TERM"),
                "ONE_FIXED_TERM_NOT_AVAILABLE",
            )
            self.term_consumed = True
        else:
            raise ValueError("PUBLISHER_BRIDGE_RPC_METHOD_DENIED")
        info = SOCKET.stat()
        require(
            stat.S_ISSOCK(info.st_mode)
            and {"device": info.st_dev, "inode": info.st_ino, "uid": info.st_uid}
            == self.expected_socket,
            "UNIX_SOCKET_IDENTITY_CHANGED",
        )
        connection = http.client.HTTPConnection("publisher-unix.invalid", timeout=10)
        try:
            connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.sock.settimeout(10)
            connection.sock.connect(str(SOCKET))
            body = xmlrpc.client.dumps(arguments, methodname=method)
            connection.request("POST", "/RPC2", body, {"Content-Type": "text/xml"})
            response = connection.getresponse()
            raw = response.read(2_000_001)
            require(response.status == 200 and len(raw) <= 2_000_000, "RPC_RESPONSE_INVALID")
            return xmlrpc.client.loads(raw)[0][0]
        finally:
            connection.close()


FIXED_MARKET_STATE_FLAG = b"--unified-market-state-root"
FIXED_MARKET_STATE_ROOT = b"/root/qtrade-local/market-publication"


def verify_market_state_argv(arguments, *, expected):
    """Pure process-argv check; only the new release must end in the fixed pair."""
    require(type(expected) is bool and type(arguments) is list and 1 <= len(arguments) <= 256
            and all(type(value) is bytes for value in arguments), "ARGV_SHAPE_INVALID")
    args = arguments[:-1] if arguments[-1] == b"" else arguments
    require(all(args) and sum(len(value) for value in args) <= 65536, "ARGV_BOUND")
    options = [index for index, value in enumerate(args)
               if value.split(b"=", 1)[0].startswith(b"--")
               and (value.split(b"=", 1)[0] == FIXED_MARKET_STATE_FLAG
                    or FIXED_MARKET_STATE_FLAG.startswith(value.split(b"=", 1)[0]))]
    if expected:
        require(options == [len(args) - 2] and args[-2:] == [
            FIXED_MARKET_STATE_FLAG, FIXED_MARKET_STATE_ROOT,
        ], "EXACT_LOCAL_MARKET_STATE_ARGUMENTS_REQUIRED")
    else:
        require(not options, "OLD_RELEASE_STATE_ARGUMENTS_FORBIDDEN")
    return {"fixed_local_state_root": expected, "checked_argv_tail": True}


def runtime_probe(operator, pid, revision, files):
    """Actual PID/cwd/env/file proof plus readonly origin resolution replay.

    The child resolves specs with PathFinder; it never imports publisher code.
    This does not inspect the live process's sys.modules or prove live stack state.
    """
    modules = BASE_MODULES if revision == operator.OLD_REVISION else MODULES
    root = BASE / "releases" / revision
    process = Path("/proc") / str(pid)
    before = operator.PublisherOnlyAdapter.owner(pid)
    require(
        before is not None and before["live_trading_enabled"] is False,
        "ACTUAL_LIVE_DISABLED_REQUIRED",
    )
    require(Path(os.readlink(process / "cwd")) == root, "ACTUAL_CWD_REQUIRED")
    args = (process / "cmdline").read_bytes().split(b"\0")
    require(
        b"-m" in args and args[args.index(b"-m") + 1] == b"scripts.publish_research_snapshot",
        "ACTUAL_PUBLISHER_MODULE_REQUIRED",
    )
    verify_market_state_argv(args, expected=revision != operator.OLD_REVISION)
    environment = dict(
        row.split(b"=", 1) for row in (process / "environ").read_bytes().split(b"\0") if b"=" in row
    )
    require(
        args[1:args.index(b"-m")] in ([], [b"-B"])
        and not environment.get(b"PYTHONSAFEPATH"),
        "IMPLICIT_MODULE_CWD_NOT_AVAILABLE",
    )
    pythonpath = environment.get(b"PYTHONPATH", b"").decode()
    paths = [
        ((root / item).resolve() if not Path(item).is_absolute() else Path(item).resolve())
        for item in pythonpath.split(":")
    ]
    require(
        # The verified -m invocation adds its verified cwd to sys.path.
        # Require src explicitly; do not require cwd a second time in PYTHONPATH.
        root / "src" in paths
        and all(path in {root, root / "src"} for path in paths),
        "ACTUAL_PYTHONPATH_REQUIRED",
    )
    require(
        bool(files) and all(name in files for name in modules.values()),
        "COMPLETE_RUNTIME_PROBE_FILES_REQUIRED",
    )
    for name, expected in files.items():
        path = root / name
        require(
            path.resolve().is_relative_to(root) and not path.is_symlink() and sha(path) == expected,
            "ACTUAL_RUNTIME_FILE_CHANGED",
        )
    executable = args[0].decode()
    require(executable == str(BASE / "venv/bin/python"), "REVIEWED_INTERPRETER_REQUIRED")
    resolver = """import importlib.machinery,json,sys
result={}
for name in json.loads(sys.argv[1]):
    paths=None; prefix=''
    for part in name.split('.'):
        prefix=(prefix+'.' if prefix else '')+part
        spec=importlib.machinery.PathFinder.find_spec(prefix,paths)
        assert spec is not None
        paths=spec.submodule_search_locations
    result[name]=spec.origin
print(json.dumps(result,sort_keys=True))
"""
    completed = subprocess.run(
        [executable, "-B", "-c", resolver, json.dumps(list(modules))],
        cwd=root,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": pythonpath,
            "LIVE_TRADING_ENABLED": "false",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    origins = json.loads(completed.stdout)
    require(
        all(Path(origins[name]).resolve() == root / relative for name, relative in modules.items()),
        "ORIGIN_RESOLUTION_REQUIRED",
    )
    require(operator.PublisherOnlyAdapter.owner(pid) == before, "ACTUAL_OWNER_CHANGED_DURING_PROBE")
    return {
        "pid": pid,
        "start_ticks": before["start_ticks"],
        "source_revision": revision,
        "runtime_files": files,
        "cwd_and_pythonpath_match": True,
        "imports_match": True,
        "live_trading_enabled": False,
        "origin_resolution_files": modules,
        "fixed_market_state_root_checked": revision != operator.OLD_REVISION,
        "import_proof_scope": "READONLY_PATHFINDER_RESOLUTION_REPLAY_NOT_LIVE_SYS_MODULES",
    }


def make_adapter(operator, manifest, bindings):
    """Concrete readonly wiring; no journal, backup, replacement or signal here."""
    require(
        bindings["helpers"]["reader"] == {"path": str(READER), "sha256": READER_SHA256}
        and bindings["helpers"]["context"] == {"path": str(CONTEXT), "sha256": CONTEXT_SHA256},
        "REVIEWED_BINDINGS_REQUIRED",
    )
    socket_identity = {key: bindings["socket"][key] for key in ("device", "inode", "uid")}
    require(
        bindings["socket"]["path"] == str(SOCKET) and bindings["socket"]["is_unix_socket"] is True,
        "REVIEWED_UNIX_SOCKET_REQUIRED",
    )
    rpc = UnixPublisherRPC(socket_identity)  # No signal authority in check/prepare.
    return operator.PublisherOnlyAdapter(
        manifest,
        readonly_reader(),
        rpc,
        running_probe=lambda pid: runtime_probe(
            operator, pid, manifest["new_revision"], manifest["runtime_files"]
        ),
        mutation_stage_approved=False,
    )


def prepare_snapshot(operator, manifest, gates, adapter, *, old_probe=None, fixture_only=False):
    """Read existing state, retain missing gate/probe reasons, serialize one preparation."""
    if fixture_only:
        gate_result = {"state": "SYNTHETIC_FIXTURE_NO_REAL_GATE_VALIDATION"}
    else:
        try:
            gate_result = operator.check_gates(manifest, gates)
        except (KeyError, ValueError) as exc:
            gate_result = {
                "state": "PENDING_REAL_GATE_BUNDLE",
                "error_type": type(exc).__name__,
                "code": str(exc) if isinstance(exc, ValueError) else "REQUIRED_FIELD_MISSING",
            }
    adapter.guard_old()
    before = adapter.controls()
    old = manifest["old_owner"]
    proof = (
        old_probe(old["pid"]) if old_probe else {"state": "PENDING_REVIEWED_OLD_RUNTIME_INVENTORY"}
    )
    require(adapter.owner(old["pid"]) == old, "PREPARATION_OWNER_CHANGED")
    return {
        "state": "MOCK_PREPARATION_ONLY" if fixture_only else "READONLY_PREPARATION_ONLY",
        "fixture_only": fixture_only,
        "gate_result": gate_result,
        "before": before,
        "old_runtime_proof": proof,
        "operator_sha256": OPERATOR_SHA256,
        "wrapper_write": False,
        "signal_sent": False,
        "mutation_stage_approved": False,
        "live_trading_enabled": False,
        "no_phase_journal_or_consumed_intent_created": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--prepare", action="store_true")
    action.add_argument("--fixture", action="store_true")
    parser.add_argument("--operator", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--gates", type=Path)
    parser.add_argument("--bindings", type=Path)
    args = parser.parse_args()
    operator = checked_module(
        "publisher_reviewed_operator", args.operator.resolve(), OPERATOR_SHA256
    )
    if args.fixture:
        old = {"pid": 1, "start_ticks": "SYNTHETIC_ONLY"}
        adapter = SimpleNamespace(
            guard_old=lambda: None,
            controls=lambda: {
                "owners": {"publisher": old},
                "context": {"active": "SYNTHETIC_ONLY"},
                "frozen_controls": {"books": {}, "rolling": "OFF"},
            },
            owner=lambda pid: old.copy(),
        )
        result = prepare_snapshot(operator, {"old_owner": old}, {}, adapter, fixture_only=True)
    else:
        require(args.manifest is not None and args.gates is not None, "EXPLICIT_INPUTS_REQUIRED")
        manifest, gates = (
            json.loads(args.manifest.read_bytes()),
            json.loads(args.gates.read_bytes()),
        )
        if args.check:
            result = operator.check_gates(manifest, gates)
        else:
            require(args.bindings is not None, "EXPLICIT_BINDINGS_REQUIRED")
            bindings = json.loads(args.bindings.read_bytes())
            adapter = make_adapter(operator, manifest, bindings)
            old_files = bindings.get("old_runtime_files")
            probe = (
                (lambda pid: runtime_probe(operator, pid, operator.OLD_REVISION, old_files))
                if old_files
                else None
            )
            result = prepare_snapshot(operator, manifest, gates, adapter, old_probe=probe)
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
