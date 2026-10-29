"""Real retained-artifact positive path and independently corrupted-copy counterexamples.

Only local in-memory copies change. No production RPC, file mutation or private
wrapper read. A private package path can be provided for isolated Linux review.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location("actual_composite_v6_tested",
    ROOT / "publisher_actual_composite_v6_20261028.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
a = c.a
operator_spec = importlib.util.spec_from_file_location("v6_actual_foundation_operator",
    ROOT / "publisher_handover_v6_preparation_20261028.py")
h = importlib.util.module_from_spec(operator_spec)
operator_spec.loader.exec_module(h)


@pytest.fixture(scope="module")
def original():
    package = Path(os.environ.get("ACTUAL_V6_PRIVATE_PACKAGE",
        "E:/QTrade-temp/publisher-v6-actual-bundle-20261028T114221Z-0eec8c94"))
    manifest = json.loads((package / "manifest.json").read_bytes())
    bundle = json.loads((package / "gates.json").read_bytes())
    artifacts = {role: (package / "gate-evidence" / info["file"]).read_bytes()
                 for role, info in bundle["artifact_files"].items()}
    manifest["actual_composite_sha256"] = a.sha((ROOT / "publisher_actual_composite_v6_20261028.py").read_bytes())
    return manifest, bundle, artifacts


def copies(original):
    manifest, bundle, artifacts = original
    return copy.deepcopy(manifest), copy.deepcopy(bundle), dict(artifacts)


def replace(manifest, bundle, artifacts, role, value, signed=False):
    if signed:
        value = {key: item for key, item in value.items() if key != "evidence_sha256"}
        value["evidence_sha256"] = a.digest(value)
    raw = json.dumps(value, sort_keys=True).encode()
    artifacts[role] = raw
    bundle["artifact_files"][role]["sha256"] = a.sha(raw)
    manifest["gate_bundle_sha256"] = a.digest(bundle)


def test_actual_35_oracles_25_faults_origins_and_nfs_probe_positive(original):
    manifest, bundle, artifacts = copies(original)
    result = a.require_v6_bundle(bundle, manifest=manifest, artifacts=artifacts)
    assert result["state"] == "GATES_PASS_PREPARATION_ONLY"
    assert result["actual_semantic_executions"] == 35
    assert result["scope_mode_samples"] == {"baseline": 15, "current": 20}
    assert result["fault_coverage"]["covered_scenarios"] == 25
    assert result["oracles"]["current"]["unique_shards"] == 5987
    assert result["origin_preflight"]["origin_modules_verified"] == 7
    assert result["slow_nfs_probe"]["negative_wall_seconds"] > 45
    assert result["production_apply_authorized_by_check"] is False
    assert result["fault_coverage"]["real_power_loss_certified"] is False
    assert result["fault_coverage"]["real_nfs_outage_certified"] is False


def test_actual_failed_stdout_cannot_be_wrapped_as_38s_success(original):
    manifest, bundle, artifacts = copies(original)
    role = "round_current_incremental"
    value = json.loads(artifacts[role])
    value.update(state="FAILED", wall_seconds=999)
    replace(manifest, bundle, artifacts, role, value)
    with pytest.raises(ValueError, match="ORIGINAL_MEASUREMENT_FAILED"):
        c.validate_actual_composite(manifest, bundle, artifacts)


def test_report_stdout_mismatch_rejected_even_with_recomputed_outer_hashes(original):
    manifest, bundle, artifacts = copies(original)
    value = json.loads(artifacts["report_current"])
    value["results"][0]["wall_seconds"] = 1.0
    replace(manifest, bundle, artifacts, "report_current", value, signed=True)
    with pytest.raises(ValueError, match="REPORT_STDOUT_CONTRADICTION"):
        c.validate_actual_composite(manifest, bundle, artifacts)


def test_original_failed_oracle_cannot_be_opaque_pass(original):
    manifest, bundle, artifacts = copies(original)
    value = json.loads(artifacts["oracle_current"])
    value["state"] = "FAILED"
    replace(manifest, bundle, artifacts, "oracle_current", value, signed=True)
    manifest["actual_oracle_sha256"]["current"] = a.sha(artifacts["oracle_current"])
    with pytest.raises(ValueError, match="ACTUAL_INDEPENDENT_ORACLE_SOURCE"):
        c.validate_actual_composite(manifest, bundle, artifacts)


def test_actual_origin_proof_missing_new_dependency_rejected(original):
    manifest, bundle, artifacts = copies(original)
    value = json.loads(artifacts["origin_preflight"])
    del value["origins"]["qtrade.research.market_validation_receipts"]
    replace(manifest, bundle, artifacts, "origin_preflight", value)
    with pytest.raises(ValueError, match="COMPLETE_PREMUTATION_ORIGINS"):
        c.validate_actual_composite(manifest, bundle, artifacts)


def test_arbitrary_unknown_artifact_is_rejected_before_reading_any_content(original):
    manifest, bundle, artifacts = copies(original)
    artifacts["not_an_allowed_role"] = b"never read private files"
    bundle["artifact_files"]["not_an_allowed_role"] = {"file": "x.raw", "sha256": a.sha(artifacts["not_an_allowed_role"])}
    manifest["gate_bundle_sha256"] = a.digest(bundle)
    with pytest.raises(ValueError, match="EXACT_ACTUAL_ARTIFACT_SET"):
        c.validate_actual_composite(manifest, bundle, artifacts)


def test_source_hash_change_without_manifest_rebinding_is_rejected(original):
    manifest, bundle, artifacts = copies(original)
    bundle["artifact_files"]["source_manifest"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="TRUSTED_MANIFEST_BUNDLE_BINDING"):
        c.validate_actual_composite(manifest, bundle, artifacts)


def test_fault_xml_failure_is_not_hidden_by_complete_flag(original):
    manifest, bundle, artifacts = copies(original)
    raw = artifacts["xml_additional5"].replace(b"</testcase>", b"<failure/></testcase>", 1)
    # Pytest uses self-closing nodes for successful cases; cover that exact form.
    if raw == artifacts["xml_additional5"]:
        import re
        raw = re.sub(br"(<testcase\b[^>]*?)/>", br"\1><failure/></testcase>", raw, count=1)
    assert raw != artifacts["xml_additional5"]
    artifacts["xml_additional5"] = raw
    bundle["artifact_files"]["xml_additional5"]["sha256"] = a.sha(raw)
    manifest["gate_bundle_sha256"] = a.digest(bundle)
    with pytest.raises(ValueError, match="ACTUAL_TEST_FAILURE_OR_SKIP"):
        c.validate_actual_composite(manifest, bundle, artifacts)


def test_injection_scope_cannot_be_relabeled_actual_outage(original):
    manifest, bundle, artifacts = copies(original)
    value = json.loads(artifacts["slow_nfs_probe"])
    value["injection_scope"] = "REAL_NFS_OUTAGE"
    replace(manifest, bundle, artifacts, "slow_nfs_probe", value, signed=True)
    with pytest.raises(ValueError, match="ACTUAL_SLOW_NFS_SCOPE"):
        c.validate_actual_composite(manifest, bundle, artifacts)


def test_fault_self_attestation_and_legacy_gate_never_fallback(original):
    manifest, bundle, artifacts = copies(original)
    bundle["fault_suite_complete"] = True
    manifest["gate_bundle_sha256"] = a.digest(bundle)
    with pytest.raises(ValueError, match="ONLY_V6_ACTUAL_EVIDENCE_BUNDLE_ALLOWED"):
        c.validate_actual_composite(manifest, bundle, artifacts)


def test_real_operator_foundation_and_actual_bundle_positive(original):
    manifest, bundle, artifacts = copies(original)
    result = h.check_loaded_evidence(manifest, bundle, artifacts)
    assert result["state"] == "GATES_PASS_PREPARATION_ONLY"
    assert result["foundation"]["old_owner"]["pid"] == 294214
    assert result["foundation"]["signal_calls"] == 0
    assert result["production_apply_authorized_by_check"] is False


def test_fresh_raw_settings_cannot_be_replaced_by_normalized_expected_settings(original):
    manifest, bundle, artifacts = copies(original)
    value = json.loads(artifacts["foundation_runtime"])
    value["settings"]["autorestart"] = False
    replace(manifest, bundle, artifacts, "foundation_runtime", value)
    bundle["supervisor_runtime"]["evidence_sha256"] = a.sha(artifacts["foundation_runtime"])
    manifest["gate_bundle_sha256"] = a.digest(bundle)
    with pytest.raises(ValueError, match="ACTUAL_FOUNDATION_CONTRADICTION"):
        h.check_loaded_evidence(manifest, bundle, artifacts)


def test_fresh_raw_owner_cannot_be_ignored(original):
    manifest, bundle, artifacts = copies(original)
    value = json.loads(artifacts["foundation_runtime"])
    value["old_owner"]["pid"] += 1
    replace(manifest, bundle, artifacts, "foundation_runtime", value)
    bundle["supervisor_runtime"]["evidence_sha256"] = a.sha(artifacts["foundation_runtime"])
    manifest["gate_bundle_sha256"] = a.digest(bundle)
    with pytest.raises(ValueError, match="ACTUAL_FOUNDATION_CONTRADICTION"):
        h.check_loaded_evidence(manifest, bundle, artifacts)


def test_failed_raw_supervisor_experiment_never_becomes_opaque_pass(original):
    manifest, bundle, artifacts = copies(original)
    value = json.loads(artifacts["supervisor_semantics_raw"])
    value["state"] = "FAILED"
    replace(manifest, bundle, artifacts, "supervisor_semantics_raw", value)
    bundle["supervisor_semantics"]["evidence_sha256"] = a.sha(artifacts["supervisor_semantics_raw"])
    manifest["gate_bundle_sha256"] = a.digest(bundle)
    with pytest.raises(ValueError, match="ACTUAL_SEMANTICS_SOURCE"):
        h.check_loaded_evidence(manifest, bundle, artifacts)


def test_good_old_cold_fields_never_override_actual_v6_contract(original):
    manifest, bundle, artifacts = copies(original)
    bundle["cold"] = {"state": "SUCCEEDED", "wall_seconds": 38, "batches": 4200}
    manifest["gate_bundle_sha256"] = a.digest(bundle)
    with pytest.raises(ValueError, match="ONLY_V6_ACTUAL_EVIDENCE_BUNDLE_ALLOWED"):
        h.check_loaded_evidence(manifest, bundle, artifacts)
