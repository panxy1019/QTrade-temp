"""Synthetic normalized gate receipts; no claimed real measurements or production I/O."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location("incremental_gate_v4",
    ROOT / "publisher_incremental_gates_v4_20261028.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def freeze(batches):
    return {"freeze_sha256": sha(f"SYNTHETIC freeze{batches}".encode()),
        "ledger_sha256": sha(f"SYNTHETIC ledger{batches}".encode()),
        "catalog_sha256": sha(f"SYNTHETIC catalog{batches}".encode()),
        "batches": batches, "chunks": batches * 25}


def fixture():
    evidence = {}

    def retain(value):
        raw = value if type(value) is bytes else json.dumps(value, sort_keys=True).encode()
        identity = sha(raw)
        evidence[identity] = raw
        return identity

    original = retain(b"SYNTHETIC original measurement source; not real production evidence")
    manifest = {"schema_version": g.MANIFEST_SCHEMA, "source_revision": "a" * 40,
        "runtime_files": {"scripts/publish_unified_market.py": "b" * 64,
                          "scripts/unified_market_checkpoint.py": "c" * 64,
                          "src/qtrade/research/market_validation_receipts.py": "d" * 64},
        "freezes": {"baseline_5640": freeze(5640), "current_5701": freeze(5701)},
        "production_policy": dict(g.PRODUCTION_POLICY), "recovery_diagnostic_seconds": 90}
    source = {"source_revision": manifest["source_revision"],
              "source_inventory_sha256": g.digest(manifest["runtime_files"])}
    bundle = {"schema_version": g.SCHEMA, "manifest_sha256": g.digest(manifest),
              "modes": {}, "checks": {}}
    for name in ["tests", "source_import_audit", "independent_review", "fault_suite"]:
        check = {"schema_version": g.CHECK_SCHEMA, "kind": name, **source,
                 "state": "PASS", "complete": True, "evidence_refs": [original]}
        if name == "fault_suite":
            check["covered_faults"] = sorted(g.FAULTS)
        bundle["checks"][name] = retain(check)
    for mode, minimum in g.MODES.items():
        bundle["modes"][mode] = []
        for index in range(minimum):
            measurement = retain(f"SYNTHETIC original measured run {mode}/{index}".encode())
            name = "current_5701" if mode == "normal_incremental" and index == 0 else "baseline_5640"
            oracle = {"schema_version": g.ORACLE_SCHEMA, "state": "PASS", **source,
                "freeze": manifest["freezes"][name], "output_index_sha256": "e" * 64,
                "output_objects_manifest_sha256": "f" * 64, "target_entries": 3000,
                "full_source_batches": manifest["freezes"][name]["batches"],
                "all_raw_normalized_shards_lineage_verified": True,
                "candidate_receipts_used": False, "independent_replayer": True,
                "evidence_refs": [original]}
            diagnostic = mode in {"cold_bootstrap", "local_db_loss_sidecar_recovery"}
            limit = 120 if mode == "cold_bootstrap" else 90 if diagnostic else 45
            elapsed = 118 if mode == "cold_bootstrap" else 70 if diagnostic else 38
            run = {"schema_version": g.RUN_SCHEMA, "mode": mode,
                "run_id": measurement, **source,
                "freeze_name": name, "elapsed_seconds": elapsed, "wall_seconds": elapsed + 0.1,
                "profiled": False, "state": "SUCCEEDED", "production_export_seconds": 45,
                "diagnostic_only": diagnostic, "diagnostic_limit_seconds": limit,
                "output_isolated_from_production": True, "new_process": mode != "normal_incremental",
                "initial_local_db_exists": mode in {"normal_incremental", "checkpoint_new_process"},
                "initial_sidecar_exists": mode != "cold_bootstrap", "prior_unmeasured_prewarm": False,
                "scenario": "real_change_5701" if index == 0 else "rollover" if index == 1 else "unchanged",
                "measured": {"source_bytes_read": 237_000_000, "cache_peak_rows": 0,
                             "cache_peak_estimated_bytes": 0},
                "output_index_sha256": "e" * 64, "output_objects_manifest_sha256": "f" * 64,
                "oracle_sha256": retain(oracle), "evidence_refs": [measurement, original]}
            bundle["modes"][mode].append(retain(run))
    return manifest, bundle, evidence, retain


def patch_run(bundle, evidence, retain, mode, field, value):
    ref = bundle["modes"][mode][0]
    run = json.loads(evidence[ref])
    run[field] = value
    bundle["modes"][mode][0] = retain(run)


def test_mode_results_never_grant_production_120_or_apply():
    manifest, bundle, evidence, _ = fixture()
    result = g.check_incremental_gates_v4(manifest, bundle, evidence)
    assert result["state"] == "GATES_PASS_PREPARATION_ONLY"
    assert result["mode_samples"] == g.MODES
    assert result["production_export_seconds"] == 45
    assert result["production_apply_authorized_by_check"] is False
    assert result["cold_diagnostic_production_authority"] is False
    assert result["first_production_cold_failure_must_be_retained"] is True


@pytest.mark.parametrize("mode,field,value", [
    ("normal_incremental", "wall_seconds", 45.001),
    ("normal_incremental", "elapsed_seconds", True),
    ("normal_incremental", "production_export_seconds", 120),
    ("normal_incremental", "prior_unmeasured_prewarm", True),
    ("checkpoint_new_process", "wall_seconds", 45.001),
    ("checkpoint_new_process", "new_process", False),
    ("checkpoint_new_process", "initial_sidecar_exists", False),
    ("cold_bootstrap", "diagnostic_only", False),
    ("cold_bootstrap", "diagnostic_limit_seconds", 45),
    ("cold_bootstrap", "wall_seconds", 120.001),
    ("cold_bootstrap", "initial_local_db_exists", True),
    ("cold_bootstrap", "initial_sidecar_exists", True),
    ("local_db_loss_sidecar_recovery", "wall_seconds", 90.001),
    ("local_db_loss_sidecar_recovery", "initial_local_db_exists", True),
    ("local_db_loss_sidecar_recovery", "initial_sidecar_exists", False),
    ("local_db_loss_sidecar_recovery", "diagnostic_only", False),
    ("normal_incremental", "freeze_name", "baseline_5640"),
    ("normal_incremental", "profiled", True),
    ("normal_incremental", "state", "FAILED"),
    ("normal_incremental", "unknown_field", "injected"),
])
def test_real_gate_boundary_faults_rejected(mode, field, value):
    manifest, bundle, evidence, retain = fixture()
    patch_run(bundle, evidence, retain, mode, field, value)
    with pytest.raises(ValueError):
        g.check_incremental_gates_v4(manifest, bundle, evidence)


@pytest.mark.parametrize("mode", list(g.MODES))
def test_missing_or_duplicate_measurements_cannot_meet_sample_count(mode):
    manifest, bundle, evidence, _ = fixture()
    bundle["modes"][mode].pop()
    with pytest.raises(ValueError, match="MODE_SAMPLE_COUNT"):
        g.check_incremental_gates_v4(manifest, bundle, evidence)


def test_current5701_cannot_be_replaced_by_old4200_oracle():
    manifest, bundle, evidence, _ = fixture()
    manifest["freezes"]["current_5701"]["batches"] = 4200
    bundle["manifest_sha256"] = g.digest(manifest)
    with pytest.raises(ValueError, match="REQUIRED_FREEZE"):
        g.check_incremental_gates_v4(manifest, bundle, evidence)


@pytest.mark.parametrize("field,value", [("candidate_receipts_used", True),
    ("independent_replayer", False), ("full_source_batches", 4200), ("target_entries", 2694),
    ("output_index_sha256", "0" * 64)])
def test_oracle_must_independently_bind_complete_current_output(field, value):
    manifest, bundle, evidence, retain = fixture()
    ref = bundle["modes"]["normal_incremental"][0]
    run = json.loads(evidence[ref])
    oracle = json.loads(evidence[run["oracle_sha256"]])
    oracle[field] = value
    run["oracle_sha256"] = retain(oracle)
    bundle["modes"]["normal_incremental"][0] = retain(run)
    with pytest.raises(ValueError, match="INDEPENDENT_COMPLETE_ORACLE_REQUIRED"):
        g.check_incremental_gates_v4(manifest, bundle, evidence)


def test_bound_sha_requires_actual_original_bytes():
    manifest, bundle, evidence, _ = fixture()
    original = next(key for key, value in evidence.items() if value.startswith(b"SYNTHETIC original"))
    del evidence[original]
    with pytest.raises(ValueError, match="ACTUAL_EVIDENCE_REQUIRED"):
        g.check_incremental_gates_v4(manifest, bundle, evidence)


def test_actual_evidence_byte_tamper_fails_before_pass():
    manifest, bundle, evidence, _ = fixture()
    reference = bundle["checks"]["tests"]
    evidence[reference] += b" "
    with pytest.raises(ValueError, match="EVIDENCE_BYTES_CHANGED"):
        g.check_incremental_gates_v4(manifest, bundle, evidence)


def test_fault_coverage_cannot_silently_omit_nfs_or_owner_failures():
    manifest, bundle, evidence, retain = fixture()
    check = json.loads(evidence[bundle["checks"]["fault_suite"]])
    check["covered_faults"].remove("slow_nfs")
    bundle["checks"]["fault_suite"] = retain(check)
    with pytest.raises(ValueError, match="COMPLETE_FAULT_COVERAGE"):
        g.check_incremental_gates_v4(manifest, bundle, evidence)
