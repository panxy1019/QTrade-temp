"""Independent adversarial fixtures for actual-report parsing; no production I/O."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location("actual_adapter_v6",
    ROOT / "publisher_actual_evidence_v6_20261028.py")
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
operator_spec = importlib.util.spec_from_file_location("handover_v6_preparation",
    ROOT / "publisher_handover_v6_preparation_20261028.py")
h = importlib.util.module_from_spec(operator_spec)
operator_spec.loader.exec_module(h)


def raw(value):
    return json.dumps(value, sort_keys=True).encode()


def signed(value):
    return {**value, "evidence_sha256": a.digest(value)}


def fixture(monkeypatch, profile="baseline_modes"):
    driver = b"SYNTHETIC reviewed driver, never executed"
    monkeypatch.setattr(a, "REVIEWED_DRIVERS", {a.sha(driver): profile})
    revision = "a" * 40
    git = {name: "b" * 64 for name in a.REQUIRED_MODULES.values()}
    source = {"source_scope": "EXACT_PUBLIC_GIT_COMMIT", "base_revision": revision,
              "source_revision": revision, "files": git}
    freeze = signed({**a.AUTHORITY, "schema_version": "qtrade.market-benchmark-freeze.v2",
        "selected_batches": 5640 if profile == "baseline_modes" else 5701,
        "selected_chunks": 140136 if profile == "baseline_modes" else 141634,
        "ledger_sha256": "1" * 64, "catalog_raw_sha256": "2" * 64,
        "captured_at": "2026-10-28T07:09:45.831362+00:00"})
    mount_raw = b"1 0 0:1 / / rw - overlay overlay rw\n42 1 0:22 / /data rw - nfs4 SYNTHETIC.private:/fake rw\n"
    line = mount_raw.decode().splitlines()[1]
    mount = {"raw_sha256": a.sha(mount_raw), "selected_mount": {"mount_id": "42", "parent_id": "1",
        "major_minor": "0:22", "root": "/", "mount_point": "/data", "fs_type": "nfs4",
        "source": "SYNTHETIC.private:/fake", "raw_line": line}, "output_st_dev": 22,
        "production_st_dev": 22, "output_resolved": "/data/operator-checks/SYNTHETIC",
        "production_resolved": "/data/unified-data/market-public", "available_bytes": 200 * 1024**3}
    labels = ([f"bootstrap{i}" for i in range(1, 6)] + [f"restart{i}" for i in range(1, 6)]
        + [f"recovery{i}" for i in range(1, 6)] if profile == "baseline_modes" else
        ["incremental"] + [f"restart{i:02}" for i in range(1, 20)])
    child_by_label, results = {}, []
    for index, label in enumerate(labels):
        cold, recovery = label.startswith("bootstrap"), label.startswith("recovery")
        limit = 120 if cold or recovery else 45
        wall = 110.0 if cold else 70.0 if recovery else 38.0
        child = {"state": "SUCCEEDED", "statistics": {"export_seconds": wall - 0.5,
            "selected_batches": freeze["selected_batches"], "selected_chunks": freeze["selected_chunks"],
            "quarantined_entries": 0, "source_bytes_read": 237250656, "cache_estimated_bytes": 0,
            "cached_rows": 0, "checkpoint_id": "c" * 64, "index_id": "d" * 64, "total_files": 5986,
            "total_bytes": 166050382},
            "wall_seconds": wall, "pid": 1200 + index, "peak_rss_kib": 900000,
            "cache_empty_at_start": True}
        child_by_label[label] = raw(child)
        item = {**child, "pass_name": label, "seconds_limit": limit, "within_budget": True,
            "index_raw_sha256": "e" * 64, "output_refs_manifest_sha256": "f" * 64,
            "output_refs_count": 5986, "previous_success_used_as_oracle": False,
            "independent_full_current_audit": "PENDING"}
        if profile == "baseline_modes":
            item.update(initial_local_database_exists=not cold and not recovery,
                        initial_object_count=0 if cold else 5986)
        results.append(item)
    report = {**a.AUTHORITY, "schema_version": "qtrade.market-export-worker-benchmark.v1",
        "source_base_revision": revision, "source_scope": "EXACT_PUBLIC_GIT_COMMIT",
        "source_manifest_sha256": a.sha(raw(source)), "source_files_verified": len(git),
        "freeze_evidence_sha256": freeze["evidence_sha256"], "controlled_snapshot_clock": freeze["captured_at"],
        "production_output_modified": False, "profiling_enabled": False,
        "production_seconds_limit_unchanged": 45,
        "clock_scope": "FROZEN_BENCHMARK_ONLY_NOT_PRODUCTION_ACCEPTANCE",
        "source_byte_budget": 512000000, "cache_estimated_byte_budget": 128000000,
        "candidate_files": git, "worker_sources": git, "mount_before": copy.deepcopy(mount),
        "mount_after": copy.deepcopy(mount), "parent_pid": 1000, "parent_start_ticks": "12345",
        "results": results}
    if profile == "current_continuity":
        report.update(initial_state="CLONE_OF_PRIOR_REAL_DURABLE_5640_CHECKPOINT_NOT_NO_CHECKPOINT_COLD",
            concurrent_operator_workload="FINAL_FULL_COLD_DIAGNOSTICS_ON_SAME_SHARED_NFS_REPORTED_NOT_EXCLUSIVE")
    arguments = {"source_manifest_raw": raw(source), "freeze_raw": raw(freeze),
        "round_stdout": child_by_label, "mount_before_raw": mount_raw, "mount_after_raw": mount_raw,
        "driver_raw": driver, "expected_git_files": git, "expected_revision": revision,
        "expected_source_manifest_sha256": a.sha(raw(source)), "expected_freeze_sha256": a.sha(raw(freeze))}
    return report, arguments


def derive(report, arguments):
    return a.derive_benchmark(raw(signed(report)), **arguments)


@pytest.mark.parametrize("profile,count,batches", [("baseline_modes", 15, 5640), ("current_continuity", 20, 5701)])
def test_actual_report_values_are_derived_with_honest_unknowns(monkeypatch, profile, count, batches):
    report, args = fixture(monkeypatch, profile)
    result = derive(report, args)
    assert len(result["runs"]) == count
    assert {run["scope_batches"] for run in result["runs"]} == {batches}
    assert all(run["initial_sidecar_exists"] is None and run["started_at"] is None
               and run["child_start_ticks"] is None for run in result["runs"])
    assert result["production_apply_authorized"] is False
    projection = raw(result)
    assert b"SYNTHETIC.private" not in projection and b"raw_line" not in projection


def test_failed_original_cannot_be_wrapped_as_succeeded38(monkeypatch):
    report, args = fixture(monkeypatch)
    args["round_stdout"]["bootstrap1"] = raw({"state": "FAILED", "wall_seconds": 999,
        "error_code": "ACTUAL_ORIGINAL_FAILURE", "filesystem": "tmpfs"})
    report["results"][0].update(state="SUCCEEDED", wall_seconds=38.0)
    with pytest.raises(ValueError, match="ORIGINAL_MEASUREMENT_FAILED"):
        derive(report, args)


def test_original999_seconds_cannot_be_replaced_by_normalized38(monkeypatch):
    report, args = fixture(monkeypatch)
    child = json.loads(args["round_stdout"]["bootstrap1"])
    child["wall_seconds"] = 999.0
    args["round_stdout"]["bootstrap1"] = raw(child)
    with pytest.raises(ValueError, match="REPORT_STDOUT_CONTRADICTION"):
        derive(report, args)
    report["results"][0]["wall_seconds"] = 999.0
    with pytest.raises(ValueError, match="ORIGINAL_WALL_BUDGET"):
        derive(report, args)


def test_same_execution_json_whitespace_does_not_add_samples(monkeypatch):
    report, args = fixture(monkeypatch)
    first = derive(report, args)
    report_raw = raw(signed(report)) + b"  \n"
    variant = a.derive_benchmark(report_raw, **{**args, "round_stdout": {
        key: value + b" \n" for key, value in args["round_stdout"].items()}})
    assert [run["execution_id"] for run in first["runs"]] == [
        run["execution_id"] for run in variant["runs"]]
    with pytest.raises(ValueError, match="DUPLICATE_SEMANTIC_EXECUTION"):
        a.assert_unique_executions(first["runs"] + variant["runs"])


def test_tmpfs_original_cannot_be_declared_nfs(monkeypatch):
    report, args = fixture(monkeypatch)
    changed = args["mount_before_raw"].replace(b"nfs4", b"tmpfs")
    args["mount_before_raw"] = changed
    report["mount_before"]["raw_sha256"] = a.sha(changed)
    report["mount_before"]["selected_mount"]["raw_line"] = changed.decode().splitlines()[1]
    report["mount_before"]["selected_mount"]["fs_type"] = "tmpfs"
    with pytest.raises(ValueError, match="ACTUAL_NFS_DEVICE_REQUIRED"):
        derive(report, args)


def test_mount_device_must_match_raw_and_production_before_after(monkeypatch):
    report, args = fixture(monkeypatch)
    report["mount_after"]["output_st_dev"] = 23
    with pytest.raises(ValueError, match="ACTUAL_NFS_DEVICE_REQUIRED"):
        derive(report, args)


def test_baseline5640_never_relabeled_current5701(monkeypatch):
    report, args = fixture(monkeypatch)
    monkeypatch.setattr(a, "REVIEWED_DRIVERS", {a.sha(args["driver_raw"]): "current_continuity"})
    with pytest.raises(ValueError, match="EXPLICIT_SCOPE_MATRIX"):
        derive(report, args)


@pytest.mark.parametrize("removed", ["scripts/unified_market_checkpoint.py",
    "src/qtrade/research/market_validation_receipts.py"])
def test_new_dependency_missing_fails_before_mutation(monkeypatch, removed):
    _, args = fixture(monkeypatch)
    source = json.loads(args["source_manifest_raw"])
    del source["files"][removed]
    # Recomputing all caller-provided subset hashes still cannot remove required modules.
    with pytest.raises(ValueError, match="NEW_MODULES_REQUIRED_BEFORE_MUTATION"):
        a.source_preflight(raw(source), source["files"], args["expected_revision"])


def test_git_full_inventory_subset_is_rejected(monkeypatch):
    _, args = fixture(monkeypatch)
    git = {**args["expected_git_files"], "src/qtrade/__init__.py": "1" * 64}
    with pytest.raises(ValueError, match="COMPLETE_GIT_INVENTORY_REQUIRED"):
        a.source_preflight(args["source_manifest_raw"], git, args["expected_revision"])


@pytest.mark.parametrize("legacy", [{"cold": {}, "warm": {}}, {"freeze": {"batches": 4200}},
    {"schema_version": "qtrade.publisher-incremental-gates.v4"}])
def test_handover_entry_has_no_legacy4200_fallback(legacy):
    with pytest.raises(ValueError, match="ONLY_V6_ACTUAL_EVIDENCE_BUNDLE_ALLOWED"):
        a.require_v6_bundle(legacy)


def test_even_v6_discriminator_alone_never_grants_mutation():
    with pytest.raises(ValueError, match="V6_COMPOSITE_INTEGRATION_PENDING_NO_MUTATION"):
        a.require_v6_bundle({"schema_version": "qtrade.publisher-handover-gates.v6"})


def test_self_attested_fault_suite_cannot_replace_actual_xml():
    with pytest.raises(ValueError, match="LEGACY_OR_SELF_ATTESTED_GATE_FORBIDDEN"):
        a.require_v6_bundle({"schema_version": "qtrade.publisher-handover-gates.v6", "fault_suite_complete": True})
    with pytest.raises(ValueError, match="REQUIRED_FAULT_TESTCASE_MISSING"):
        a.parse_test_xml(b'<testsuite tests="1"><testcase classname="x" name="unrelated"/></testsuite>',
                         {"x::disk_full"})


@pytest.mark.parametrize("marker", ["failure", "error", "skipped"])
def test_actual_fault_test_failure_or_skip_is_not_pass(marker):
    xml = f'<testsuite><testcase classname="x" name="disk_full"><{marker}/></testcase></testsuite>'.encode()
    with pytest.raises(ValueError, match="ACTUAL_TEST_FAILURE_OR_SKIP"):
        a.parse_test_xml(xml, {"x::disk_full"})


def test_missing_round_stdout_cannot_borrow_report_fields(monkeypatch):
    report, args = fixture(monkeypatch)
    del args["round_stdout"]["bootstrap2"]
    with pytest.raises(ValueError, match="ALL_ACTUAL_STDOUT_REQUIRED"):
        derive(report, args)


def test_unreviewed_driver_is_not_original_measurement_authority(monkeypatch):
    report, args = fixture(monkeypatch)
    args["driver_raw"] += b" altered"
    with pytest.raises(ValueError, match="UNREVIEWED_DRIVER"):
        derive(report, args)


def test_real_handover_entry_refuses_legacy_before_any_action():
    with pytest.raises(ValueError, match="ONLY_V6_ACTUAL_EVIDENCE_BUNDLE_ALLOWED"):
        h.check_gates({"required_freeze": {"batches": 4200}}, {"cold": {}, "warm": {}})
    with pytest.raises(ValueError, match="V6_COMPOSITE_INTEGRATION_PENDING_NO_MUTATION"):
        h.check_gates({}, {"schema_version": "qtrade.publisher-handover-gates.v6"})


def test_direct_adapter_mutation_cannot_skip_v6_gate():
    adapter = h.PublisherOnlyAdapter({}, None, None, mutation_stage_approved=True)
    with pytest.raises(ValueError, match="ONLY_V6_ACTUAL_EVIDENCE_BUNDLE_ALLOWED"):
        adapter._mutation_guard()


def test_actual_source_preflight_refuses_missing_modules_before_path_io():
    adapter = h.PublisherOnlyAdapter({"runtime_files": {"scripts/publish_unified_market.py": "a" * 64}},
                                     None, None)
    with pytest.raises(ValueError, match="NEW_MODULES_REQUIRED_BEFORE_MUTATION"):
        adapter.check_source()


def oracle_fixture(monkeypatch):
    report, args = fixture(monkeypatch)
    runs = derive(report, args)["runs"]
    freeze = json.loads(args["freeze_raw"])
    imports = {name: "3" * 64 for name in list(a.REQUIRED_MODULES.values())[:6]}
    oracle = {**a.AUTHORITY, "schema_version": "qtrade.independent-current-market-oracle.v1",
        "state": "PASS", "scope": "ORIGINAL_458_READONLY_ORACLE_NOT_EXPORT_PERFORMANCE_OR_PRODUCTION_ACCEPTANCE",
        "baseline_revision": "458552de1fea1faf77d02dcd9ec69251a0c364b7", "baseline_imports": imports,
        "candidate_files": args["expected_git_files"], "freeze_evidence_sha256": freeze["evidence_sha256"],
        "ledger_sha256": freeze["ledger_sha256"], "catalog_raw_sha256": freeze["catalog_raw_sha256"],
        "controlled_snapshot_clock": freeze["captured_at"], "selected_batches": 5640,
        "selected_chunks": 140136, "all_entries_equal": True, "all_shards_byte_equal": True,
        "entry_count": 3000, "unique_shards": 5986, "output_bytes": 166050382,
        "decoded_batch_cache_used": False, "exporter_called": False, "candidate_validator_called": False,
        "market_pointer_written": False, "production_output_modified": False,
        "entry_states": {"AVAILABLE": 2995, "NO_DATA": 5},
        "statistics": {"source_failures": 0, "batches_validated": 5640, "actual_shards_read": 5986,
                       "actual_output_bytes_read": 166050382, "source_bytes_read": 237250656},
        "index_raw_sha256": "e" * 64, "index_evidence_sha256": "d" * 64,
        "readonly_output_device": 22, "source_manifest_bindings_sha256": "4" * 64,
        "candidate_manifest_sha256": "5" * 64}
    kwargs = {"freeze_raw": args["freeze_raw"], "expected_git_files": args["expected_git_files"],
              "derived_runs": runs, "baseline_import_sha256": imports}
    return oracle, kwargs


def test_actual_original_oracle_is_parsed_and_bound_to_each_measured_output(monkeypatch):
    oracle, args = oracle_fixture(monkeypatch)
    data = raw(signed(oracle))
    result = a.derive_oracle(data, expected_oracle_sha256=a.sha(data), **args)
    assert result["scope_batches"] == 5640 and result["measured_outputs_bound"] == 15
    assert result["production_apply_authorized"] is False


@pytest.mark.parametrize("field,value", [("state", "FAILED"), ("candidate_validator_called", True),
    ("selected_batches", 4200), ("entry_count", 2694), ("unique_shards", 5987),
    ("index_raw_sha256", "0" * 64)])
def test_bad_oracle_assertion_cannot_replace_actual_comparison(monkeypatch, field, value):
    oracle, args = oracle_fixture(monkeypatch)
    oracle[field] = value
    data = raw(signed(oracle))
    with pytest.raises(ValueError):
        a.derive_oracle(data, expected_oracle_sha256=a.sha(data), **args)


def test_xml_declared_count_cannot_multiply_one_actual_execution():
    with pytest.raises(ValueError, match="XML_COUNT_CONTRADICTION"):
        a.parse_test_xml(b'<testsuite tests="100"><testcase classname="x" name="one"/></testsuite>',
                         {"x::one"})
