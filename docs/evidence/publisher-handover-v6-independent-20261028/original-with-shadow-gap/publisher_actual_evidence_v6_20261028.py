"""Derive facts from reviewed real benchmark bytes; no normalized PASS inputs.

Raw mountinfo/report bytes stay in memory/private operator evidence. Public
projections contain hashes and selected non-private mount identity only.
This adapter grants no deployment authority and performs no process/network I/O.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath

SCHEMA = "qtrade.publisher-actual-evidence.v6"
MAX_BYTES = 16_000_000
REQUIRED_MODULES = {
    "scripts.publish_research_snapshot": "scripts/publish_research_snapshot.py",
    "scripts.publish_unified_market": "scripts/publish_unified_market.py",
    "scripts.unified_store": "scripts/unified_store.py",
    "scripts.unified_market_checkpoint": "scripts/unified_market_checkpoint.py",
    "qtrade.research.market_validation_receipts": "src/qtrade/research/market_validation_receipts.py",
    "qtrade.research.unified_market": "src/qtrade/research/unified_market.py",
    "qtrade.market_data.sina_incremental": "src/qtrade/market_data/sina_incremental.py",
}
REVIEWED_DRIVERS = {
    "d4215b08294bba939061ba83edae7a7ed1bd7e38501477310fe0dfa6e4a5e982": "baseline_modes",
    "04966f3bb1a978417779b0ed09dcfc3d6b057f96d335ba6c24b3fba16de5a1d4": "current_continuity",
}
AUTHORITY = {"classification": "PUBLIC_SOURCE_RESEARCH_OBSERVATION_ONLY", "point_in_time": False,
    "realtime_readiness": False, "real_data_paper_readiness": False,
    "external_order_transmission": False, "live_trading_enabled": False, "promotion": "NOT_ELIGIBLE"}


def require(condition, code):
    if not condition:
        raise ValueError("PUBLISHER_V6_" + code)


def sha(raw):
    require(type(raw) is bytes and len(raw) <= MAX_BYTES, "EVIDENCE_BYTE_BOUND")
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode())


def pairs(values):
    output = {}
    for key, value in values:
        require(key not in output, "DUPLICATE_JSON_KEY")
        output[key] = value
    return output


def document(raw):
    sha(raw)
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: require(False, "NONFINITE_JSON"))


def identity(value, size=64):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{" + str(size) + "}", value) is not None


def integer(value, minimum=0):
    require(type(value) is int and value >= minimum, "INTEGER_REQUIRED")
    return value


def signed_document(raw):
    value = document(raw)
    require(type(value) is dict and value.get("evidence_sha256") == digest({
        k: v for k, v in value.items() if k != "evidence_sha256"}), "ORIGINAL_EVIDENCE_HASH")
    require(all(type(value.get(k)) is type(v) and value.get(k) == v
                for k, v in AUTHORITY.items()), "ORIGINAL_AUTHORITY")
    return value


def source_preflight(source_manifest_raw, expected_git_files, expected_revision):
    """Compare the complete independently obtained Git tree, not a caller subset."""
    source = document(source_manifest_raw)
    require(source.get("source_scope") == "EXACT_PUBLIC_GIT_COMMIT"
        and source.get("source_revision") == expected_revision
        and source.get("base_revision") == expected_revision and identity(expected_revision, 40),
        "EXACT_GIT_SOURCE_REQUIRED")
    files = source.get("files")
    require(type(files) is dict and files == expected_git_files and bool(files)
        and all(identity(value) for value in files.values()), "COMPLETE_GIT_INVENTORY_REQUIRED")
    require(all(path in files for path in REQUIRED_MODULES.values()), "NEW_MODULES_REQUIRED_BEFORE_MUTATION")
    require(all(type(path) is str and path and "\\" not in path and ":" not in path
        and not PurePosixPath(path).is_absolute() and ".." not in PurePosixPath(path).parts
        for path in files), "GIT_PATH_INVALID")
    return source


def _unescape(value):
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


def _linux_device(value):
    # Linux dev_t; independent of the adapter host's operating system.
    integer(value, 1)
    return f"{((value >> 8) & 0xfff) | ((value >> 32) & 0xfffff000)}:" + str(
        (value & 0xff) | ((value >> 12) & 0xffffff00))


def mount_projection(raw, claimed):
    require(type(claimed) is dict and claimed.get("raw_sha256") == sha(raw), "MOUNT_RAW_HASH")
    output = PurePosixPath(claimed["output_resolved"])
    production = PurePosixPath(claimed["production_resolved"])
    require(output.is_absolute() and production.is_absolute(), "MOUNT_PATH")
    records = []
    for line in raw.decode("utf-8", errors="strict").splitlines():
        fields = line.split()
        require(len(fields) >= 10 and "-" in fields, "MOUNTINFO_SCHEMA")
        split = fields.index("-")
        require(split + 3 < len(fields), "MOUNTINFO_SCHEMA")
        point = _unescape(fields[4])
        row = {"mount_id": fields[0], "parent_id": fields[1], "major_minor": fields[2],
            "root": fields[3], "mount_point": point, "fs_type": fields[split + 1],
            "source": fields[split + 2], "raw_line": line}
        records.append(row)
    def selected(path):
        candidates = [row for row in records if path.is_relative_to(PurePosixPath(row["mount_point"]))]
        require(bool(candidates), "MOUNTINFO_NOT_COVERING_OUTPUT")
        return max(candidates, key=lambda row: len(row["mount_point"]))
    row = selected(output)
    require(row == claimed["selected_mount"] and selected(production) == row,
            "ACTUAL_PRODUCTION_EQUIVALENT_MOUNT_REQUIRED")
    require(row["fs_type"] in {"nfs", "nfs4"}
        and claimed["output_st_dev"] == claimed["production_st_dev"]
        and row["major_minor"] == _linux_device(claimed["output_st_dev"]), "ACTUAL_NFS_DEVICE_REQUIRED")
    require(integer(claimed["available_bytes"]) >= 100 * 1024**3, "DATA_RESERVE")
    return {"raw_sha256": sha(raw), "selected_line_sha256": sha(row["raw_line"].encode()),
        "mount_id": row["mount_id"], "parent_id": row["parent_id"], "major_minor": row["major_minor"],
        "fs_type": row["fs_type"], "output_st_dev": claimed["output_st_dev"],
        "production_st_dev": claimed["production_st_dev"],
        "output_path_sha256": sha(str(output).encode()),
        "production_path_sha256": sha(str(production).encode())}


def derive_benchmark(report_raw, *, source_manifest_raw, freeze_raw, round_stdout,
                     mount_before_raw, mount_after_raw, driver_raw, expected_git_files,
                     expected_revision, expected_source_manifest_sha256, expected_freeze_sha256):
    """Parse exact actual report + child stdout and derive every accepted fact.

    No normalized run/schema assertions are accepted as inputs. Missing original
    report/stdout/mount bytes fail closed. The declared approval matrix is
    baseline5640 modes5/5/5 and current5701 normal20, without relabelling scopes.
    """
    require(sha(source_manifest_raw) == expected_source_manifest_sha256
        and sha(freeze_raw) == expected_freeze_sha256, "PINNED_INPUT_HASH")
    source = source_preflight(source_manifest_raw, expected_git_files, expected_revision)
    profile = REVIEWED_DRIVERS.get(sha(driver_raw))
    require(profile is not None, "UNREVIEWED_DRIVER")
    freeze = signed_document(freeze_raw)
    require(freeze["schema_version"] == "qtrade.market-benchmark-freeze.v2", "ACTUAL_FREEZE_SCHEMA")
    expected_batches = 5640 if profile == "baseline_modes" else 5701
    require(type(freeze["selected_batches"]) is int and freeze["selected_batches"] == expected_batches,
            "EXPLICIT_SCOPE_MATRIX")
    report = signed_document(report_raw)
    require(report["schema_version"] == "qtrade.market-export-worker-benchmark.v1"
        and report["source_base_revision"] == expected_revision
        and report["source_scope"] == "EXACT_PUBLIC_GIT_COMMIT"
        and report["source_manifest_sha256"] == sha(source_manifest_raw)
        and report["source_files_verified"] == len(expected_git_files)
        and report["freeze_evidence_sha256"] == freeze["evidence_sha256"]
        and report["controlled_snapshot_clock"] == freeze["captured_at"], "ACTUAL_SOURCE_FREEZE_BINDING")
    require(report["production_output_modified"] is False and report["profiling_enabled"] is False
        and type(report["production_seconds_limit_unchanged"]) is int
        and report["production_seconds_limit_unchanged"] == 45
        and report["clock_scope"] == "FROZEN_BENCHMARK_ONLY_NOT_PRODUCTION_ACCEPTANCE"
        and report["source_byte_budget"] == 512_000_000
        and report["cache_estimated_byte_budget"] == 128_000_000, "ACTUAL_UNCHANGED_POLICY")
    for name, value in report["candidate_files"].items():
        require(expected_git_files.get(name) == value, "CANDIDATE_SOURCE_MISMATCH")
    require(all(report["candidate_files"].get(path) == expected_git_files[path] for path in (
        "scripts/unified_market_checkpoint.py", "src/qtrade/research/market_validation_receipts.py")),
        "NEW_MODULES_REQUIRED_BEFORE_MUTATION")
    require(all(expected_git_files.get(name) == value for name, value in report["worker_sources"].items()),
            "WORKER_SOURCE_MISMATCH")
    before = mount_projection(mount_before_raw, report["mount_before"])
    after = mount_projection(mount_after_raw, report["mount_after"])
    mount_keys = ["mount_id", "parent_id", "major_minor", "fs_type", "output_st_dev", "production_st_dev"]
    require(all(before[key] == after[key] for key in mount_keys)
        and before["selected_line_sha256"] == after["selected_line_sha256"], "MOUNT_CHANGED_DURING_RUN")
    parent_pid = integer(report["parent_pid"], 1)
    ticks = report["parent_start_ticks"]
    require(type(ticks) is str and ticks.isascii() and ticks.isdigit() and int(ticks) > 0,
            "ACTUAL_PARENT_START_TICKS_REQUIRED")
    if profile == "baseline_modes":
        labels = [f"bootstrap{i}" for i in range(1, 6)] + [f"restart{i}" for i in range(1, 6)] + [f"recovery{i}" for i in range(1, 6)]
    else:
        labels = ["incremental"] + [f"restart{i:02}" for i in range(1, 20)]
        require(report["initial_state"] == "CLONE_OF_PRIOR_REAL_DURABLE_5640_CHECKPOINT_NOT_NO_CHECKPOINT_COLD"
            and report.get("concurrent_operator_workload") == "FINAL_FULL_COLD_DIAGNOSTICS_ON_SAME_SHARED_NFS_REPORTED_NOT_EXCLUSIVE",
            "ACTUAL_CONTINUITY_SEED_SCOPE")
    results = report["results"]
    require(type(results) is list and [value.get("pass_name") for value in results] == labels,
            "ACTUAL_COMPLETE_ORDERED_RUNS_REQUIRED")
    require(type(round_stdout) is dict and set(round_stdout) == set(labels), "ALL_ACTUAL_STDOUT_REQUIRED")
    derived = []
    for item in results:
        label = item["pass_name"]
        child = document(round_stdout[label])
        require(type(child) is dict and child.get("state") == "SUCCEEDED", "ORIGINAL_MEASUREMENT_FAILED")
        require(set(child) == {"state", "statistics", "wall_seconds", "pid", "peak_rss_kib", "cache_empty_at_start"},
                "ACTUAL_CHILD_SCHEMA")
        require(all(item.get(key) == value and type(item.get(key)) is type(value)
                    for key, value in child.items()), "REPORT_STDOUT_CONTRADICTION")
        mode = "normal_incremental" if profile == "current_continuity" else (
            "cold_bootstrap" if label.startswith("bootstrap") else "checkpoint_new_process"
            if label.startswith("restart") else "local_db_loss_sidecar_recovery")
        limit = 45 if mode in {"normal_incremental", "checkpoint_new_process"} else 120
        require(type(item["seconds_limit"]) is int and item["seconds_limit"] == limit
            and item["within_budget"] is True, "ACTUAL_MODE_BUDGET")
        wall = child["wall_seconds"]
        stats = child["statistics"]
        require(type(wall) in (int, float) and math.isfinite(wall) and 0 <= wall <= limit,
                "ORIGINAL_WALL_BUDGET")
        elapsed = stats["export_seconds"]
        require(type(elapsed) in (int, float) and math.isfinite(elapsed) and 0 <= elapsed <= wall,
                "ORIGINAL_EXPORT_CLOCK")
        require(child["cache_empty_at_start"] is True and integer(child["peak_rss_kib"], 1) > 0,
                "ACTUAL_NEW_PROCESS_MEASUREMENT")
        require(type(stats["selected_batches"]) is int and stats["selected_batches"] == expected_batches
            and type(stats["selected_chunks"]) is int and stats["selected_chunks"] == freeze["selected_chunks"]
            and stats["quarantined_entries"] == 0, "ACTUAL_SELECTED_INPUT_SCOPE")
        require(1 <= integer(stats["source_bytes_read"]) <= 512_000_000
            and integer(stats["cache_estimated_bytes"]) <= 128_000_000
            and integer(stats["cached_rows"]) <= 100_000, "ACTUAL_RESOURCE_BUDGET")
        for key in ("checkpoint_id", "index_id"):
            require(identity(stats[key]), "ACTUAL_GENERATION_IDENTITY")
        for key in ("index_raw_sha256", "output_refs_manifest_sha256"):
            require(identity(item[key]), "ACTUAL_OUTPUT_IDENTITY")
        require(integer(item["output_refs_count"], 1) == integer(stats["total_files"], 1)
            and item["previous_success_used_as_oracle"] is False, "ACTUAL_OUTPUT_COUNT")
        initial_db = item.get("initial_local_database_exists")
        initial_objects = item.get("initial_object_count")
        if profile == "baseline_modes":
            require(type(initial_db) is bool and type(initial_objects) is int,
                    "ACTUAL_INITIAL_DB_OBJECT_STATE")
            require(initial_db is (mode == "checkpoint_new_process")
                and (initial_objects == 0 if mode == "cold_bootstrap" else initial_objects > 0),
                "ACTUAL_MODE_INITIAL_STATE")
        execution = {"parent_pid": parent_pid, "parent_start_ticks": int(ticks), "pass_name": label,
            "child_pid": integer(child["pid"], 1), "freeze_evidence_sha256": freeze["evidence_sha256"],
            "source_manifest_sha256": sha(source_manifest_raw)}
        derived.append({"execution_id": digest(execution), "execution": execution, "mode": mode,
            "scope_batches": expected_batches, "state": child["state"], "wall_seconds": wall,
            "export_seconds": elapsed, "limit_seconds": limit,
            "production_export_seconds": 45, "diagnostic_only": limit == 120,
            "round_stdout_sha256": sha(round_stdout[label]), "report_sha256": sha(report_raw),
            "statistics": stats, "initial_local_database_exists": initial_db,
            "initial_object_count": initial_objects, "initial_sidecar_exists": None,
            "child_start_ticks": None, "started_at": None, "completed_at": None,
            "output_index_sha256": item["index_raw_sha256"],
            "output_refs_manifest_sha256": item["output_refs_manifest_sha256"],
            "mount_before": before, "mount_after": after,
            "independent_oracle_status": "PENDING_SEPARATE_ORIGINAL_ORACLE"})
    assert_unique_executions(derived)
    return {"schema_version": SCHEMA, "state": "ACTUAL_MEASUREMENTS_DERIVED_NO_DEPLOYMENT_AUTHORITY",
        "profile": profile, "source_revision": expected_revision,
        "source_manifest_sha256": sha(source_manifest_raw), "freeze_raw_sha256": sha(freeze_raw),
        "report_sha256": sha(report_raw), "driver_sha256": sha(driver_raw), "runs": derived,
        "limitations": ["PER_ROUND_UTC_AND_CHILD_START_TICKS_NOT_CAPTURED",
            "INITIAL_SIDECAR_STATE_NOT_CAPTURED", "CACHE_PEAK_NOT_CAPTURED",
            "CURRENT5701_DIAGNOSTIC_MODES_NOT_REPEATED", "SEPARATE_FULL_ORACLE_AND_FAULT_XML_REQUIRED"],
        "production_apply_authorized": False, "private_mount_bytes_in_projection": False}


def assert_unique_executions(runs):
    ids = [digest(value["execution"]) for value in runs]
    require(len(ids) == len(set(ids)) and all(value["execution_id"] == actual
        for value, actual in zip(runs, ids)), "DUPLICATE_SEMANTIC_EXECUTION")


def parse_test_xml(raw, required_testcases):
    """Count actual XML case nodes; no self-declared fault_suite.complete flag."""
    sha(raw)
    require(b"<!DOCTYPE" not in raw.upper() and b"<!ENTITY" not in raw.upper(), "XML_ENTITY_FORBIDDEN")
    root = ET.fromstring(raw)
    cases = list(root.iter("testcase"))
    require(cases and all(not list(case.iter("failure")) and not list(case.iter("error"))
        and not list(case.iter("skipped")) for case in cases), "ACTUAL_TEST_FAILURE_OR_SKIP")
    names = {case.attrib.get("classname", "") + "::" + case.attrib.get("name", "") for case in cases}
    require(len(names) == len(cases), "DUPLICATE_ACTUAL_TESTCASE")
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if suites and all("tests" in suite.attrib for suite in suites):
        require(sum(int(suite.attrib["tests"]) for suite in suites) == len(cases), "XML_COUNT_CONTRADICTION")
    require(set(required_testcases) <= names, "REQUIRED_FAULT_TESTCASE_MISSING")
    return {"xml_sha256": sha(raw), "actual_test_count": len(cases),
            "verified_required_testcases": sorted(required_testcases)}


def derive_oracle(oracle_raw, *, expected_oracle_sha256, freeze_raw, expected_git_files,
                  derived_runs, baseline_import_sha256):
    """Parse original 458 oracle bytes and bind every measured output in its scope."""
    require(sha(oracle_raw) == expected_oracle_sha256, "PINNED_ORACLE_HASH")
    value = signed_document(oracle_raw)
    freeze = signed_document(freeze_raw)
    count = freeze["selected_batches"]
    expected_outputs = {5640: (5986, 166050382), 5701: (5987, 167397950)}
    require(count in expected_outputs and bool(derived_runs), "ORACLE_EXPLICIT_SCOPE")
    shards, output_bytes = expected_outputs[count]
    require(value["schema_version"] == "qtrade.independent-current-market-oracle.v1"
        and value["state"] == "PASS"
        and value["scope"] == "ORIGINAL_458_READONLY_ORACLE_NOT_EXPORT_PERFORMANCE_OR_PRODUCTION_ACCEPTANCE"
        and value["baseline_revision"] == "458552de1fea1faf77d02dcd9ec69251a0c364b7"
        and value["baseline_imports"] == baseline_import_sha256 and len(baseline_import_sha256) >= 6,
        "ACTUAL_INDEPENDENT_ORACLE_SOURCE")
    require(value["candidate_files"] == expected_git_files
        and all(path in expected_git_files for path in REQUIRED_MODULES.values()),
        "ORACLE_COMPLETE_CANDIDATE_GIT_FILES")
    require(value["freeze_evidence_sha256"] == freeze["evidence_sha256"]
        and value["ledger_sha256"] == freeze["ledger_sha256"]
        and value["catalog_raw_sha256"] == freeze["catalog_raw_sha256"]
        and value["controlled_snapshot_clock"] == freeze["captured_at"]
        and type(value["selected_batches"]) is int and value["selected_batches"] == count
        and type(value["selected_chunks"]) is int and value["selected_chunks"] == freeze["selected_chunks"],
        "ORACLE_ACTUAL_FREEZE_BINDING")
    require(value["all_entries_equal"] is True and value["all_shards_byte_equal"] is True
        and type(value["entry_count"]) is int and value["entry_count"] == 3000
        and type(value["unique_shards"]) is int and value["unique_shards"] == shards
        and type(value["output_bytes"]) is int and value["output_bytes"] == output_bytes,
        "ORACLE_COMPLETE_OUTPUT_REQUIRED")
    require(all(value[key] is False for key in ("decoded_batch_cache_used", "exporter_called",
        "candidate_validator_called", "market_pointer_written", "production_output_modified")),
        "ORACLE_CANDIDATE_REUSE_OR_WRITE_FORBIDDEN")
    states = value["entry_states"]
    require(type(states) is dict and set(states) <= {"AVAILABLE", "NO_DATA", "QUARANTINED"}
        and all(type(number) is int and number >= 0 for number in states.values())
        and sum(states.values()) == 3000, "ORACLE_ENTRY_COUNT")
    stats = value["statistics"]
    require(type(stats["source_failures"]) is int and stats["source_failures"] == 0
        and type(stats["batches_validated"]) is int and stats["batches_validated"] == count
        and stats["actual_shards_read"] == shards and stats["actual_output_bytes_read"] == output_bytes
        and 1 <= integer(stats["source_bytes_read"]) <= 512_000_000,
        "ORACLE_ACTUAL_REPLAY_COUNTS")
    for row in derived_runs:
        require(row["scope_batches"] == count
            and row["execution"]["freeze_evidence_sha256"] == freeze["evidence_sha256"]
            and row["output_index_sha256"] == value["index_raw_sha256"]
            and row["statistics"]["index_id"] == value["index_evidence_sha256"]
            and row["statistics"]["total_files"] == shards
            and row["statistics"]["total_bytes"] == output_bytes
            and row["mount_before"]["output_st_dev"] == value["readonly_output_device"],
            "ORACLE_MEASURED_OUTPUT_BINDING")
    return {"state": "ACTUAL_ORACLE_PARSED_NO_DEPLOYMENT_AUTHORITY", "oracle_sha256": sha(oracle_raw),
        "scope_batches": count, "selected_chunks": freeze["selected_chunks"], "entry_count": 3000,
        "unique_shards": shards, "output_bytes": output_bytes,
        "index_raw_sha256": value["index_raw_sha256"],
        "source_manifest_bindings_sha256": value["source_manifest_bindings_sha256"],
        "candidate_manifest_original_sha256": value["candidate_manifest_sha256"],
        "candidate_git_files_equal": True, "baseline_imports_equal": True,
        "measured_outputs_bound": len(derived_runs), "production_apply_authorized": False}


def require_v6_bundle(bundle, *, manifest=None, artifacts=None):
    """Mandatory entry discriminator for the new handover; zero legacy fallback."""
    require(type(bundle) is dict and bundle.get("schema_version") == "qtrade.publisher-handover-gates.v6",
            "ONLY_V6_ACTUAL_EVIDENCE_BUNDLE_ALLOWED")
    require(not any(key in bundle for key in ("cold", "warm", "normalized_runs", "fault_suite_complete")),
            "LEGACY_OR_SELF_ATTESTED_GATE_FORBIDDEN")
    require(manifest is not None and artifacts is not None,
            "V6_COMPOSITE_INTEGRATION_PENDING_NO_MUTATION")
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name("publisher_actual_composite_v6_20261028.py")
    require(not path.is_symlink() and sha(path.read_bytes()) == manifest["actual_composite_sha256"],
            "ACTUAL_COMPOSITE_SOURCE_CHANGED")
    spec = importlib.util.spec_from_file_location("publisher_v6_actual_composite_entry", path)
    require(spec is not None and spec.loader is not None, "ACTUAL_COMPOSITE_IMPORT_INVALID")
    composite = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(composite)
    return composite.validate_actual_composite(manifest, bundle, artifacts)
