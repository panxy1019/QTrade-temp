"""Executable read-only v6 composition of original evidence, with scoped faults."""
import hashlib
import importlib.util
import json
from pathlib import Path

ADAPTER_SHA256 = "ef3665447333b16ff8b04f53e37562a15dae53902d21016db5de7e06adbff0a6"
path = Path(__file__).with_name("publisher_actual_evidence_v6_20261028.py")
if hashlib.sha256(path.read_bytes()).hexdigest() != ADAPTER_SHA256:
    raise ValueError("PUBLISHER_V6_ACTUAL_ADAPTER_CHANGED")
spec = importlib.util.spec_from_file_location("publisher_actual_v6_component", path)
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
require = a.require
EXTRA_TESTS = {
    "historical_backfill": "test_historical_backfill_rebuilds_complete_window_and_keeps_both_sources",
    "quarantine_release": "test_quarantine_release_revalidates_restored_source_without_altering_old_generation",
    "disk_full": "test_enospc_during_new_object_write_preserves_committed_head",
    "slow_nfs": "test_slow_generation_io_exceeds_real_deadline_without_moving_head",
    "git_non_fast_forward": "test_real_non_fast_forward_push_cannot_report_new_publication_success",
}
FAULTS = {"unchanged", "real_increment", "window_rollover", "historical_backfill",
    "same_price_new_lineage", "revocation", "quarantine_release", "conflict", "rule_upgrade",
    "source_corruption", "missing_shard", "bad_receipt", "disk_full", "slow_nfs", "source_budget",
    "crash_fsync", "crash_generation", "crash_head", "crash_db_record", "collector_concurrency",
    "publisher_competition", "stale_owner", "git_non_fast_forward", "http_timeout", "term_no_retry"}


def artifact_roles():
    roles = {"source_manifest", "origin_preflight", "fault_map_original", "xml_linux793", "xml_tools102",
        "xml_independent109", "xml_additional5", "additional_test_manifest", "additional_test_source",
        "slow_nfs_probe", "mount_slow_before", "mount_slow_after", "slow_probe_driver",
        "foundation_runtime", "supervisor_semantics_raw"}
    for scope in ("baseline", "current"):
        roles.update(prefix + scope for prefix in ("report_", "freeze_", "driver_", "oracle_"))
        roles.update({"mount_" + scope + "_before", "mount_" + scope + "_after"})
    for label in [f"bootstrap{i}" for i in range(1, 6)] + [f"restart{i}" for i in range(1, 6)] + [f"recovery{i}" for i in range(1, 6)]:
        roles.add("round_baseline_" + label)
    for label in ["incremental"] + [f"restart{i:02}" for i in range(1, 20)]:
        roles.add("round_current_" + label)
    return roles


def origin_preflight(raw, manifest):
    value = a.document(raw)
    require(value["schema_version"] == "qtrade.publisher-release-origin-preflight.v6"
        and value["state"] == "PASS" and value["source_revision"] == manifest["new_revision"]
        and value["source_manifest_sha256"] == manifest["actual_source_manifest_sha256"]
        and value["source_files_verified"] == len(manifest["runtime_files"])
        and value["source_inventory_before_sha256"] == a.digest(manifest["runtime_files"])
        and value["source_inventory_after_sha256"] == a.digest(manifest["runtime_files"])
        and value["resolver_source_sha256"] == manifest["actual_origin_resolver_sha256"],
        "ACTUAL_PREMUTATION_ORIGIN_PROOF")
    require(all(value[key] is False for key in ("module_execution", "private_wrapper_read",
        "production_service_control", "live_trading_enabled", "pycache_write_enabled")), "ORIGIN_SCOPE")
    origins = value["origins"]
    require(set(origins) == set(a.REQUIRED_MODULES), "COMPLETE_PREMUTATION_ORIGINS")
    root = manifest["release_root"]
    require(value["release_root_sha256"] == a.sha(root.encode()), "ORIGIN_RELEASE_ROOT")
    for name, relative in a.REQUIRED_MODULES.items():
        row = origins[name]
        require(row["origin_relative"] == relative
            and row["source_sha256"] == manifest["runtime_files"][relative]
            and row["actual_origin_sha256"] == a.sha((root + "/" + relative).encode()),
            "ACTUAL_ORIGIN_PATH_MISMATCH")
    return {"raw_sha256": a.sha(raw), "source_files_verified": value["source_files_verified"],
            "origin_modules_verified": len(origins), "module_execution": False}


def foundation(runtime_raw, semantics_raw, manifest, bundle):
    value = a.document(runtime_raw)
    runtime = bundle["supervisor_runtime"]
    semantics = bundle["supervisor_semantics"]
    require(value["schema_version"] == "qtrade.publisher-foundation-readonly.v6"
        and a.sha(runtime_raw) == runtime["evidence_sha256"]
        and value["old_owner"] == manifest["old_owner"]
        and value["config_files"] == manifest["supervisor_config_files"] == runtime["config_files"]
        and value["settings"] == runtime["settings"]
        and value["binary_sha256"] == runtime["binary_sha256"] == semantics["binary_sha256"]
        and value["wrapper_old_sha256"] == manifest["wrapper_old_sha256"]
        and value["wrapper_new_sha256"] == manifest["wrapper_new_sha256"], "ACTUAL_FOUNDATION_CONTRADICTION")
    native = value["native"]
    require(native["name"] == manifest["program"] and native["pid"] == value["old_owner"]["pid"]
        and native["state"] == 20 and native["statename"].upper() == "RUNNING"
        and value["live_trading_enabled"] is False and value["wrapper_changed"] is False
        and value["production_service_control"] is False and value["signal_calls"] == 0
        and value["candidate_private_only"] is True and value["candidate_bytes"] == 695,
        "READONLY_FOUNDATION_SCOPE")
    socket = value["socket"]
    require(socket["path"] == "/var/run/supervisor.sock" and socket["is_unix_socket"] is True
        and type(socket["device"]) is int and socket["device"] > 0
        and type(socket["inode"]) is int and socket["inode"] > 0 and socket["uid"] == 0,
        "ACTUAL_SOCKET_IDENTITY")
    original = a.document(semantics_raw)
    require(a.sha(semantics_raw) == semantics["evidence_sha256"] and original["state"] == "PASS"
        and original["binary_sha256"] == original["binary_after_sha256"] == value["binary_sha256"]
        and original["production_mutations"] is False and original["production_ctl_invocations"] == 0
        and original["production_identity_unchanged"] is True, "ACTUAL_SEMANTICS_SOURCE")
    cases = {case["case"]: case for case in original["cases"]}
    require(set(cases) == {"normal", "term"}, "ACTUAL_SEMANTICS_CASES")
    for name, case in cases.items():
        require(case["state"] == "PASS" and case["exactly_two_generations"] is True
            and case["sentinel_identities_unchanged"] is True and case["stopProcess_invocations"] == 0
            and case["external_main_term_count"] == (1 if name == "term" else 0)
            and case["normal_exit_zero_event"]["event"] == "NATURAL_EXIT_ZERO"
            and case["old_pid"] != case["new_pid"]
            and case["native_new_running"]["controlled"]["pid"] == case["new_pid"]
            and case["native_new_running"]["controlled"]["state"] == 20, "ACTUAL_SINGLE_RESTART_SEMANTICS")
    require(cases["term"]["alive_and_native_running_after_110_seconds"] is True
        and cases["term"]["drain_seconds"] > 110
        and not original["cleanup"]["errors"] and not original["cleanup"]["remaining_non_zombie_fixture_pids"]
        and len(original["cleanup"]["signals"]) == 1
        and original["cleanup"]["signals"][0]["method"] == "supervisor.signalProcess", "ACTUAL_NATURAL_DRAIN_SEMANTICS")
    return {"runtime_raw_sha256": a.sha(runtime_raw), "semantics_raw_sha256": a.sha(semantics_raw),
        "checked_at": value["checked_at"], "old_owner": value["old_owner"], "socket_identity": {
            key: socket[key] for key in ("device", "inode", "uid")}, "native_running_observed": True,
        "wrapper_changed": False, "signal_calls": 0, "natural_drain_seconds": cases["term"]["drain_seconds"],
        "expected_pid_cas": False, "current_owner_and_config_must_be_rechecked_before_mutation": True}


def fault_coverage(map_raw, xml_by_name, additional_xml, additional_manifest_raw,
                   additional_source, manifest):
    mapping = a.document(map_raw)
    records = mapping["mapping"]
    require(type(records) is list and len(records) == 25
        and {row["fault"] for row in records} == FAULTS, "ACTUAL_25_FAULT_MAPPING_REQUIRED")
    ids = {}
    for name in ("linux793", "tools102", "independent109"):
        raw = xml_by_name[name]
        require(a.sha(raw) == mapping["xml_reports"][name]["sha256"], "PINNED_ACTUAL_FAULT_XML")
        parsed = a.parse_test_xml(raw, set())
        require(parsed["actual_test_count"] == mapping["xml_reports"][name]["tests"], "ACTUAL_FAULT_XML_COUNT")
        root = a.ET.fromstring(raw)
        ids[name] = {case.attrib.get("classname", "") + "." + case.attrib.get("name", "")
                     for case in root.iter("testcase")}
    required = {"tests.test_market_incremental_failure_boundaries::" + name for name in EXTRA_TESTS.values()}
    extra = a.parse_test_xml(additional_xml, required)
    require(extra["actual_test_count"] == 5, "FIVE_ACTUAL_ADDITIONAL_CASES_REQUIRED")
    source = a.document(additional_manifest_raw)
    overlay = "tests/test_market_incremental_failure_boundaries.py"
    require(source["source_revision"] == manifest["new_revision"]
        and source["copy_source_manifest_sha256"] == manifest["actual_source_manifest_sha256"]
        and source["candidate_paths"] == [overlay]
        and source["files"][overlay] == a.sha(additional_source)
        and all(source["files"].get(name) == value for name, value in manifest["runtime_files"].items())
        and set(source["files"]) == set(manifest["runtime_files"]) | {overlay},
        "ACTUAL_TEST_ONLY_OVERLAY_SOURCE_BINDING")
    projected = []
    for row in records:
        fault = row["fault"]
        level = row["coverage"]
        matches = row["matched_testcases"]
        if fault in EXTRA_TESTS:
            require(level == "MISSING", "ORIGINAL_FAULT_MAPPING_CHANGED")
            level = "DIRECT_FIXTURE"
            refs = {"additional5": ["tests.test_market_incremental_failure_boundaries." + EXTRA_TESTS[fault]]}
        else:
            require(level in {"DIRECT_FIXTURE", "PARTIAL", "TOOLS_MOCK"}, "FAULT_REMAINS_UNCOVERED")
            refs = {name: names for name, names in matches.items() if names}
            require(refs and all(name in ids and set(names) <= ids[name] for name, names in refs.items()),
                    "ACTUAL_PASSED_TESTCASE_MAPPING_REQUIRED")
        projected.append({"fault": fault, "coverage": level, "actual_testcase_refs": refs,
            "scope_limit": row["scope_limit"] if fault not in EXTRA_TESTS else
                "ISOLATED_INTERFACE_INJECTION_NOT_PRODUCTION_DISK_OR_NETWORK_DESTRUCTION"})
    return {"raw_map_sha256": a.sha(map_raw), "additional_xml_sha256": a.sha(additional_xml),
        "covered_scenarios": len(projected), "mapping": projected,
        "real_power_loss_certified": False, "real_nfs_outage_certified": False,
        "distributed_cas_certified": False, "blanket_real_fault_complete_claim": False}


def slow_nfs_probe(raw, *, before_raw, after_raw, driver_raw, manifest, freeze_raw, runs):
    require(a.sha(driver_raw) == manifest["actual_slow_probe_driver_sha256"], "SLOW_PROBE_DRIVER")
    value = a.signed_document(raw)
    freeze = a.signed_document(freeze_raw)
    require(value["schema_version"] == "qtrade.same-nfs-latency-injection.v1"
        and value["state"] == "EXPECTED_FAILURE_AND_RECOVERY_VERIFIED"
        and value["source_revision"] == manifest["new_revision"]
        and value["source_manifest_sha256"] == manifest["actual_source_manifest_sha256"]
        and value["freeze_evidence_sha256"] == freeze["evidence_sha256"]
        and value["injection_scope"] == "ACTUAL_ISOLATED_NFS_GENERATION_WITH_CONTROLLED_POST_FSYNC_LATENCY_NOT_REAL_OUTAGE"
        and value["normal_seconds_limit_unchanged"] == 45
        and value["production_output_modified"] is False
        and value["logical_clock_scope"] == "FROZEN_DIAGNOSTIC_ONLY", "ACTUAL_SLOW_NFS_SCOPE")
    negative = value["negative_export"]
    require(negative["error_class"] == "ValueError" and negative["error_code"] == "UNIFIED_MARKET_TIME_BUDGET"
        and type(negative["wall_seconds"]) in (int, float) and negative["wall_seconds"] > 45
        and value["negative_head_preserved"] is True
        and a.identity(value["original_head"]) and a.identity(value["new_recovery_head"])
        and value["original_head"] != value["new_recovery_head"], "EXPECTED_SLOW_FAILURE_AND_HEAD_PRESERVATION")
    injected = value["injected_elapsed_seconds"]
    require(type(injected) is list and injected and all(type(number) in (int, float)
        and number >= 16 for number in injected), "ACTUAL_LATENCY_INJECTION")
    stats = value["recovery_statistics"]
    require(stats["selected_batches"] == freeze["selected_batches"] == 5701
        and stats["selected_chunks"] == freeze["selected_chunks"]
        and stats["replayed_batches"] == 0 and stats["receipt_hits"] == 5701
        and type(stats["export_seconds"]) in (int, float) and 0 <= stats["export_seconds"] <= 45,
        "ACTUAL_RECOVERY_WITHOUT_BUDGET_CHANGE")
    before = a.mount_projection(before_raw, value["mount_before"])
    after = a.mount_projection(after_raw, value["mount_after"])
    keys = ("mount_id", "parent_id", "major_minor", "fs_type", "output_st_dev", "production_st_dev",
            "selected_line_sha256")
    require(all(before[key] == after[key] == runs[0]["mount_before"][key] for key in keys),
            "SLOW_PROBE_SAME_ACTUAL_NFS")
    return {"raw_sha256": a.sha(raw), "scope": value["injection_scope"],
        "negative_wall_seconds": negative["wall_seconds"], "negative_head_preserved": True,
        "recovery_export_seconds": stats["export_seconds"], "recovery_receipt_hits": stats["receipt_hits"],
        "production_export_seconds": 45, "mount_before": before, "mount_after": after,
        "hard_interrupt_of_nfs_syscall_certified": False, "real_outage_certified": False}


def validate_actual_composite(manifest, bundle, artifacts):
    require(bundle.get("schema_version") == "qtrade.publisher-handover-gates.v6"
        and not any(key in bundle for key in ("cold", "warm", "normalized_runs", "fault_suite_complete")),
        "ONLY_V6_ACTUAL_EVIDENCE_BUNDLE_ALLOWED")
    require(manifest["gate_bundle_sha256"] == a.digest(bundle), "TRUSTED_MANIFEST_BUNDLE_BINDING")
    require(set(artifacts) == set(bundle["artifact_files"]) == artifact_roles(), "EXACT_ACTUAL_ARTIFACT_SET")
    for name, raw in artifacts.items():
        require(a.sha(raw) == bundle["artifact_files"][name]["sha256"], "ACTUAL_ARTIFACT_HASH_CHANGED")
    source = artifacts["source_manifest"]
    a.source_preflight(source, manifest["runtime_files"], manifest["new_revision"])
    scopes, all_runs, oracles = {}, [], {}
    for name, profile in (("baseline", "baseline_modes"), ("current", "current_continuity")):
        report = a.document(artifacts["report_" + name])
        rounds = {row["pass_name"]: artifacts["round_" + name + "_" + row["pass_name"]]
                  for row in report["results"]}
        derived = a.derive_benchmark(artifacts["report_" + name], source_manifest_raw=source,
            freeze_raw=artifacts["freeze_" + name], round_stdout=rounds,
            mount_before_raw=artifacts["mount_" + name + "_before"],
            mount_after_raw=artifacts["mount_" + name + "_after"], driver_raw=artifacts["driver_" + name],
            expected_git_files=manifest["runtime_files"], expected_revision=manifest["new_revision"],
            expected_source_manifest_sha256=manifest["actual_source_manifest_sha256"],
            expected_freeze_sha256=manifest["actual_freeze_sha256"][name])
        require(derived["profile"] == profile, "COMPOSITE_EXPLICIT_SCOPE_MATRIX")
        scopes[name] = derived
        all_runs.extend(derived["runs"])
        oracles[name] = a.derive_oracle(artifacts["oracle_" + name],
            expected_oracle_sha256=manifest["actual_oracle_sha256"][name],
            freeze_raw=artifacts["freeze_" + name], expected_git_files=manifest["runtime_files"],
            derived_runs=derived["runs"], baseline_import_sha256=manifest["baseline_import_sha256"])
    a.assert_unique_executions(all_runs)
    origins = origin_preflight(artifacts["origin_preflight"], manifest)
    faults = fault_coverage(artifacts["fault_map_original"],
        {name: artifacts["xml_" + name] for name in ("linux793", "tools102", "independent109")},
        artifacts["xml_additional5"], artifacts["additional_test_manifest"],
        artifacts["additional_test_source"], manifest)
    slow = slow_nfs_probe(artifacts["slow_nfs_probe"], before_raw=artifacts["mount_slow_before"],
        after_raw=artifacts["mount_slow_after"], driver_raw=artifacts["slow_probe_driver"],
        manifest=manifest, freeze_raw=artifacts["freeze_current"], runs=scopes["current"]["runs"])
    controls = foundation(artifacts["foundation_runtime"], artifacts["supervisor_semantics_raw"], manifest, bundle)
    return {"state": "GATES_PASS_PREPARATION_ONLY", "schema_version": bundle["schema_version"],
        "source_revision": manifest["new_revision"], "actual_semantic_executions": len(all_runs),
        "scope_mode_samples": {name: len(value["runs"]) for name, value in scopes.items()},
        "oracles": oracles, "origin_preflight": origins, "fault_coverage": faults,
        "slow_nfs_probe": slow, "foundation": controls, "production_export_seconds": 45,
        "production_apply_authorized_by_check": False, "automatic_prewarm_authorized": False,
        "first_production_cold_failure_must_be_retained": True,
        "limitations": scopes["current"]["limitations"], "live_trading_enabled": False,
        "external_order_transmission": False}
