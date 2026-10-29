"""Pure multi-mode gate schema. No production apply, filesystem or process I/O.

The evidence adapter must preserve the original measurement/oracle files and
derive these versioned receipts from them. Hash binding is integrity, not proof
of an assertion or digital authentication. Independent review remains required.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

SCHEMA = "qtrade.publisher-incremental-gates.v4"
MANIFEST_SCHEMA = "qtrade.publisher-incremental-gate-manifest.v4"
RUN_SCHEMA = "qtrade.publisher-incremental-run.v4"
CHECK_SCHEMA = "qtrade.publisher-incremental-check.v4"
ORACLE_SCHEMA = "qtrade.publisher-incremental-oracle.v4"
MAX_BYTES = 16_000_000
MODES = {"normal_incremental": 20, "checkpoint_new_process": 5,
         "cold_bootstrap": 5, "local_db_loss_sidecar_recovery": 5}
PRODUCTION_POLICY = {"export_seconds": 45, "source_byte_budget": 512_000_000,
    "max_cache_rows": 100_000, "max_cache_estimated_bytes": 128_000_000,
    "git_seconds": 90, "git_cleanup_term_seconds": 2, "git_cleanup_kill_seconds": 2}
FAULTS = {"unchanged", "real_increment", "window_rollover", "historical_backfill",
    "same_price_new_lineage", "revocation", "quarantine_release", "conflict", "rule_upgrade",
    "source_corruption", "missing_shard", "bad_receipt", "disk_full", "slow_nfs",
    "source_budget", "crash_fsync", "crash_generation", "crash_head", "crash_db_record",
    "collector_concurrency", "publisher_competition", "stale_owner", "git_non_fast_forward",
    "http_timeout", "term_no_retry"}


def require(condition, code):
    if not condition:
        raise ValueError("PUBLISHER_INCREMENTAL_GATE_" + code)


def identity(value):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def fields(value, expected):
    require(type(value) is dict and set(value) == set(expected), "EXACT_SCHEMA_REQUIRED")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _seconds(value, limit):
    require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= limit,
            "MODE_TIME_BUDGET")


def check_incremental_gates_v4(manifest, bundle, evidence_by_sha256):
    """Validate exact normalized evidence contracts; return preparation only.

    evidence_by_sha256 maps every referenced SHA256 to its actual retained bytes.
    Original artifacts may be opaque; normalized run/check/oracle receipts are
    parsed strictly. This function never creates a PASS receipt for an absent run.
    """
    fields(manifest, {"schema_version", "source_revision", "runtime_files", "freezes",
        "production_policy", "recovery_diagnostic_seconds"})
    require(manifest["schema_version"] == MANIFEST_SCHEMA, "MANIFEST_SCHEMA")
    revision = manifest["source_revision"]
    require(type(revision) is str and re.fullmatch(r"[a-f0-9]{40}", revision), "SOURCE_REVISION")
    inventory = manifest["runtime_files"]
    require(type(inventory) is dict and inventory and all(type(path) is str and path
        and not path.startswith(("/", "\\")) and ".." not in path.split("/")
        and "\\" not in path and ":" not in path and identity(sha)
        for path, sha in inventory.items()), "SOURCE_INVENTORY")
    require(manifest["production_policy"] == PRODUCTION_POLICY
        and all(type(manifest["production_policy"][k]) is int for k in PRODUCTION_POLICY),
        "PRODUCTION_45_POLICY_REQUIRED")
    recovery_limit = manifest["recovery_diagnostic_seconds"]
    require(type(recovery_limit) in (int, float) and math.isfinite(recovery_limit)
        and recovery_limit > 0, "EXPLICIT_RECOVERY_DIAGNOSTIC_BUDGET")
    freezes = manifest["freezes"]
    fields(freezes, {"baseline_5640", "current_5701"})
    for name, count in (("baseline_5640", 5640), ("current_5701", 5701)):
        freeze = freezes[name]
        fields(freeze, {"freeze_sha256", "ledger_sha256", "catalog_sha256", "batches", "chunks"})
        require(all(identity(freeze[k]) for k in ("freeze_sha256", "ledger_sha256", "catalog_sha256"))
            and type(freeze["batches"]) is int and freeze["batches"] == count
            and type(freeze["chunks"]) is int and freeze["chunks"] > 0, "REQUIRED_FREEZE")
    fields(bundle, {"schema_version", "manifest_sha256", "modes", "checks"})
    require(bundle["schema_version"] == SCHEMA and bundle["manifest_sha256"] == digest(manifest),
            "BUNDLE_MANIFEST_BINDING")
    inventory_sha = digest(inventory)

    def raw(sha):
        require(identity(sha) and sha in evidence_by_sha256, "ACTUAL_EVIDENCE_REQUIRED")
        value = evidence_by_sha256[sha]
        require(type(value) is bytes and 0 < len(value) <= MAX_BYTES
            and hashlib.sha256(value).hexdigest() == sha, "EVIDENCE_BYTES_CHANGED")
        return value

    def document(sha):
        return json.loads(raw(sha), object_pairs_hook=_pairs,
            parse_constant=lambda value: require(False, "NONFINITE_JSON"))

    def refs(values):
        require(type(values) is list and 1 <= len(values) <= 100, "ORIGINAL_EVIDENCE_REFS")
        for sha in values:
            raw(sha)

    def source(value):
        require(value["source_revision"] == revision
            and value["source_inventory_sha256"] == inventory_sha, "SOURCE_BINDING")

    checks = bundle["checks"]
    fields(checks, {"tests", "source_import_audit", "independent_review", "fault_suite"})
    for name, sha in checks.items():
        check = document(sha)
        expected = {"schema_version", "kind", "source_revision", "source_inventory_sha256",
                    "state", "complete", "evidence_refs"}
        if name == "fault_suite":
            expected.add("covered_faults")
        fields(check, expected)
        require(check["schema_version"] == CHECK_SCHEMA and check["kind"] == name
            and check["state"] == "PASS" and check["complete"] is True, "COMPLETE_CHECK_REQUIRED")
        source(check)
        refs(check["evidence_refs"])
        if name == "fault_suite":
            require(type(check["covered_faults"]) is list
                and len(check["covered_faults"]) == len(FAULTS)
                and set(check["covered_faults"]) == FAULTS, "COMPLETE_FAULT_COVERAGE")
    fields(bundle["modes"], MODES)
    totals, scenarios, seen_ids = {}, set(), set()
    for mode, minimum in MODES.items():
        run_refs = bundle["modes"][mode]
        require(type(run_refs) is list and minimum <= len(run_refs) <= 200
            and len(set(run_refs)) == len(run_refs), "MODE_SAMPLE_COUNT")
        for sha in run_refs:
            run = document(sha)
            fields(run, {"schema_version", "mode", "run_id", "source_revision",
                "source_inventory_sha256", "freeze_name", "elapsed_seconds", "wall_seconds",
                "profiled", "state", "production_export_seconds", "diagnostic_only",
                "diagnostic_limit_seconds", "output_isolated_from_production", "new_process",
                "initial_local_db_exists", "initial_sidecar_exists", "prior_unmeasured_prewarm",
                "scenario", "measured", "output_index_sha256", "output_objects_manifest_sha256",
                "oracle_sha256", "evidence_refs"})
            require(run["schema_version"] == RUN_SCHEMA and run["mode"] == mode
                and identity(run["run_id"]) and run["run_id"] not in seen_ids,
                "RUN_ID_OR_MODE")
            seen_ids.add(run["run_id"])
            source(run)
            require(run["freeze_name"] in freezes, "RUN_FREEZE_REQUIRED")
            freeze = freezes[run["freeze_name"]]
            require(run["profiled"] is False and run["state"] == "SUCCEEDED"
                and type(run["production_export_seconds"]) is int
                and run["production_export_seconds"] == 45
                and run["output_isolated_from_production"] is True
                and run["prior_unmeasured_prewarm"] is False, "UNPROFILED_ISOLATED_RUN_REQUIRED")
            diagnostic = mode in {"cold_bootstrap", "local_db_loss_sidecar_recovery"}
            limit = 120 if mode == "cold_bootstrap" else recovery_limit if diagnostic else 45
            require(run["diagnostic_only"] is diagnostic
                and type(run["diagnostic_limit_seconds"]) in (int, float)
                and run["diagnostic_limit_seconds"] == limit, "MODE_DIAGNOSTIC_BOUNDARY")
            _seconds(run["elapsed_seconds"], limit)
            _seconds(run["wall_seconds"], limit)
            require(all(type(run[k]) is bool for k in ("new_process", "initial_local_db_exists",
                "initial_sidecar_exists")), "REAL_PROCESS_CACHE_STATE_REQUIRED")
            if mode == "checkpoint_new_process":
                require(run["new_process"] and run["initial_local_db_exists"]
                    and run["initial_sidecar_exists"], "VALID_CHECKPOINT_RESTART_REQUIRED")
            elif mode == "cold_bootstrap":
                require(run["new_process"] and not run["initial_local_db_exists"]
                    and not run["initial_sidecar_exists"], "ACTUAL_FULL_COLD_REQUIRED")
            elif mode == "local_db_loss_sidecar_recovery":
                require(run["new_process"] and not run["initial_local_db_exists"]
                    and run["initial_sidecar_exists"], "ACTUAL_DB_LOSS_RECOVERY_REQUIRED")
            if mode == "normal_incremental":
                require(run["scenario"] in {"unchanged", "real_change_5701", "rollover"},
                        "NORMAL_SCENARIO_REQUIRED")
                scenarios.add(run["scenario"])
                if run["scenario"] == "real_change_5701":
                    require(run["freeze_name"] == "current_5701", "CURRENT_5701_REQUIRED")
            measured = run["measured"]
            caps = {"source_bytes_read": 512_000_000, "cache_peak_rows": 100_000,
                    "cache_peak_estimated_bytes": 128_000_000}
            fields(measured, caps)
            require(all(type(measured[k]) is int and 0 <= measured[k] <= cap
                for k, cap in caps.items()), "PHYSICAL_RESOURCE_BUDGET")
            require(measured["source_bytes_read"] > 0, "CURRENT_PHYSICAL_READ_REQUIRED")
            require(identity(run["output_index_sha256"])
                and identity(run["output_objects_manifest_sha256"]), "OUTPUT_IDENTITIES_REQUIRED")
            refs(run["evidence_refs"])
            require(run["run_id"] == run["evidence_refs"][0], "UNIQUE_MEASUREMENT_BYTES_REQUIRED")
            oracle = document(run["oracle_sha256"])
            fields(oracle, {"schema_version", "state", "source_revision", "source_inventory_sha256",
                "freeze", "output_index_sha256", "output_objects_manifest_sha256", "target_entries",
                "full_source_batches", "all_raw_normalized_shards_lineage_verified",
                "candidate_receipts_used", "independent_replayer", "evidence_refs"})
            source(oracle)
            require(oracle["schema_version"] == ORACLE_SCHEMA and oracle["state"] == "PASS"
                and oracle["freeze"] == freeze
                and oracle["output_index_sha256"] == run["output_index_sha256"]
                and oracle["output_objects_manifest_sha256"] == run["output_objects_manifest_sha256"]
                and type(oracle["target_entries"]) is int and oracle["target_entries"] == 3000
                and type(oracle["full_source_batches"]) is int
                and oracle["full_source_batches"] == freeze["batches"]
                and oracle["all_raw_normalized_shards_lineage_verified"] is True
                and oracle["candidate_receipts_used"] is False
                and oracle["independent_replayer"] is True, "INDEPENDENT_COMPLETE_ORACLE_REQUIRED")
            refs(oracle["evidence_refs"])
        totals[mode] = len(run_refs)
    require(scenarios == {"unchanged", "real_change_5701", "rollover"}, "NORMAL_SCENARIO_COVERAGE")
    return {"state": "GATES_PASS_PREPARATION_ONLY", "schema_version": SCHEMA,
        "source_revision": revision, "manifest_sha256": digest(manifest), "mode_samples": totals,
        "production_export_seconds": 45, "production_apply_authorized_by_check": False,
        "cold_diagnostic_production_authority": False, "automatic_prewarm_authorized": False,
        "first_production_cold_failure_must_be_retained": True,
        "live_trading_enabled": False, "external_order_transmission": False}
