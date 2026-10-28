"""Independent legacy-bound STAGED revalidation reader; never enqueue or activate.

Explicit contracts replace only the old rolling/first-staging assumptions. Full
archive/bundle/version/raw-day/Parquet replay primitives remain unchanged.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha1, sha256
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.model_registry import checked_identity
from qtrade.research.strategy_contract import freeze_strategy
from scripts.atomic_json import write_json_immutable
from scripts.audit_qlib_candidate_transition import (
    REPO,
    canonical,
    checked,
    clock,
    document,
    file_hash,
    inference_contract,
    job_chain,
    local,
    raw_day,
    readonly,
    require,
    rows,
    snapshot,
    validation_rows,
    verify_archive,
    verify_bundle,
    verify_validation,
    verify_version,
)


def legacy_snapshot(
    models, workflow, data, model_id, trained_id, later_id, training_job, strategies
):
    facts = snapshot(models, workflow, data, model_id, trained_id, later_id)
    with closing(readonly(models)) as db:
        facts["all_validations"] = rows(
            db,
            "SELECT * FROM model_validations WHERE model_id=? ORDER BY validation_id",
            (model_id,),
        )
    with closing(readonly(workflow)) as db:
        facts["training_jobs"] = rows(db, "SELECT * FROM jobs WHERE id=?", (training_job,))
        facts["training_events"] = rows(
            db, "SELECT rowid,* FROM events WHERE id=? ORDER BY rowid", (training_job,)
        )
    with closing(readonly(strategies)) as db:
        facts["strategy_association"] = rows(
            db,
            "SELECT j.job_id,s.strategy_id,s.status,s.role,s.version,"
            "c.contract_json FROM strategy_research_jobs j JOIN strategies s USING(strategy_id) "
            "JOIN strategy_research_contracts c USING(strategy_id) WHERE j.job_id=?",
            (training_job,),
        )
    return facts


def legacy_training_anchor(summary, facts, model, training_job, trained_id, now):
    require("rolling_plan" not in summary, "EXPLICIT_LEGACY_BOUND_RESEARCH_REQUIRED")
    require(
        len(facts["training_jobs"]) == 1 and len(facts["strategy_association"]) == 1,
        "ORIGINAL_RESEARCH_JOB_AND_STRATEGY_REQUIRED",
    )
    job = facts["training_jobs"][0]
    request = json.loads(job["request"])
    result = json.loads(job["result"])
    binding = summary["data_binding"]
    checked(binding)
    require(
        job["id"] == request["job_id"] == training_job
        and job["state"] == "COMPLETE"
        and job["error"] is None
        and request["kind"] == "QLIB_RESEARCH_JOB"
        and request["live_trading_enabled"] is False,
        "ORIGINAL_RESEARCH_JOB_REQUIRED",
    )
    require(
        request["spec"]["data_binding"] == binding
        and request["spec_sha256"] == signed(request["spec"])["evidence_sha256"]
        and binding["data_version_id"] == trained_id
        and binding["actual_end"] == summary["data_end"]
        and binding["dataset_sha256"] == summary["dataset_sha256"],
        "LEGACY_TRAINING_BINDING_CHANGED",
    )
    strategy = freeze_strategy(request["spec"], summary["source_revision"])
    link = facts["strategy_association"][0]
    require(
        summary["strategy_definition"] == strategy
        and json.loads(link["contract_json"]) == strategy
        and request["expected_strategy_version"]
        == result["strategy_version"]
        == summary["strategy_version"]
        == strategy["strategy_version"]
        == link["version"]
        and link["strategy_id"] == result["strategy_id"] == strategy["strategy_id"]
        and link["job_id"] == training_job
        and link["status"] == "EXPERIMENT"
        and link["role"] == "NONE"
        and result["model_artifact_id"] == model
        and result["run_id"] == summary["run_id"],
        "LEGACY_STRATEGY_JOB_MODEL_CHANGED",
    )
    states = [row["state"] for row in facts["training_events"]]
    require(
        len(states) >= 3
        and states[0] == "QUEUED"
        and states[1] == "RUNNING"
        and states[-1] == "COMPLETE"
        and len(states) == len(set(states))
        and set(states)
        <= {
            "QUEUED",
            "RUNNING",
            "BUILDING_PROVIDER",
            "COMPUTING_ALPHA158",
            "TRAINING_LIGHTGBM",
            "BACKTESTING",
            "COMPLETE",
        },
        "TRAINING_JOB_EVENT_CHAIN",
    )
    times = [clock(row["at"], now) for row in facts["training_events"]]
    require(
        times == sorted(times)
        and times[1]
        <= clock(summary["completed_at"], now)
        <= times[-1]
        <= clock(job["updated"], now),
        "TRAINING_JOB_CLOCK_CHAIN",
    )


def staged_lifecycle(facts, model, definition, registered_at, now):
    events = [r for r in facts["model_events"] if r["model_id"] == model]
    require(
        len(events) > 4
        and [r["kind"] for r in events[:4]] == ["TRAINED", "VALIDATING", "VALIDATED", "STAGED"]
        and all(r["kind"] == "INPUT_VALIDATED" for r in events[4:]),
        "STAGED_REVALIDATION_EVENT_CHAIN",
    )
    times = []
    for row in events:
        payload = json.loads(row["payload"])
        checked(payload)
        require(
            payload["model_id"] == model
            and payload["kind"] == row["kind"]
            and payload["at"] == row["at"],
            "REGISTRY_EVENT_PAYLOAD_CHANGED",
        )
        times.append(clock(row["at"], now))
    require(
        times == sorted(times)
        and times[0] == registered_at
        and json.loads(events[0]["payload"])["details"]["definition_sha256"]
        == definition["evidence_sha256"],
        "REGISTRY_DEFINITION_OR_CLOCK_CHANGED",
    )
    original = {
        json.loads(r["payload"])["details"]["imported_verified_validation"] for r in events[1:4]
    }
    imports = [json.loads(r["payload"])["details"]["validation_id"] for r in events[4:]]
    proofs = facts["all_validations"]
    ids = [r["validation_id"] for r in proofs]
    require(
        len(original) == 1
        and len(ids) == len(set(ids))
        and len(imports) == len(set(imports))
        and not original.intersection(imports)
        and set(ids) == original | set(imports),
        "ALL_ORIGINAL_AND_REVALIDATION_PROOFS_REQUIRED",
    )
    for row in proofs:
        proof = json.loads(row["proof"])
        checked(proof)
        checked(proof["contract"])
        require(
            proof["validation_id"] == row["validation_id"]
            and proof["model_bundle_sha256"] == model
            and proof["contract"]["data_version_id"] == row["data_version_id"]
            and proof["model_activated"] is False
            and proof["fit_methods_forbidden"] is True
            and proof["fitted_processors_unchanged"] is True
            and proof["auto_predict_enabled"] is False,
            "REGISTERED_PROOF_IDENTITY_OR_REFIT_CHANGED",
        )
        generated = clock(proof["contract"]["generated_at"], now)
        linked = (
            events[1:4]
            if row["validation_id"] in original
            else [
                r
                for r in events[4:]
                if json.loads(r["payload"])["details"]["validation_id"] == row["validation_id"]
            ]
        )
        require(all(generated <= clock(r["at"], now) for r in linked), "IMPORT_BEFORE_PROOF")
    return events[:4], events[4:]


def verify_all_registered_proofs(base, facts, model, bundle, trained_data, calendar, now):
    for registered in facts["all_validations"]:
        identity = checked_identity(registered["validation_id"])
        index = document(base / "online-research/control/validations" / (identity + ".json"))
        root = local(base, index["archive"])
        proof = verify_validation(root)
        require(
            proof == json.loads(registered["proof"])
            and index["model_id"] == model
            and index["manifest_sha256"] == proof["evidence_sha256"],
            "HISTORICAL_PROOF_ARCHIVE_CHANGED",
        )
        version_id = checked_identity(registered["data_version_id"])
        version_root = base / "unified-data/qlib-versions/versions" / version_id
        version = verify_version(version_root)
        dataset = document(version_root / "dataset.json")
        generated = clock(proof["contract"]["generated_at"], now)
        require(
            proof["contract"]
            == inference_contract(bundle, trained_data, dataset, version, calendar, now=generated),
            "HISTORICAL_INPUT_CONTRACT_CHANGED",
        )
        validation_rows(root, version_root, proof, bundle, dataset)


def exact_training_archive(archive):
    manifest = document(archive / "checksums.json", authority=False)
    paths = list(archive.rglob("*"))
    require(
        len(paths) <= 30000
        and not any(p.is_symlink() for p in (archive, *archive.parents, *paths)),
        "TRAINING_ARCHIVE_PATH_OR_BOUND",
    )
    actual = {p.relative_to(archive).as_posix() for p in paths if p.is_file()}
    require(
        actual == set(manifest["files"]) | {"checksums.json"},
        "EXACT_TRAINING_ARCHIVE_FILESET_REQUIRED",
    )


def plain_document(path):
    require(
        path.is_file()
        and not any(p.is_symlink() for p in (path, *path.parents))
        and path.stat().st_size <= 8_000_000,
        "PLAIN_DOCUMENT_PATH_OR_BOUND",
    )
    value = json.loads(path.read_bytes())
    require(type(value) is dict, "PLAIN_DOCUMENT_OBJECT_REQUIRED")
    return value


def verify_training_source(archive, manifest_path, expected_sha, revision):
    manifest = plain_document(manifest_path)
    require(
        re.fullmatch("[a-f0-9]{64}", expected_sha) is not None
        and file_hash(manifest_path) == expected_sha,
        "EXPLICIT_SOURCE_MANIFEST_CHANGED",
    )
    require(
        manifest["revision"] == revision
        and type(manifest["files"]) is dict
        and 1 <= len(manifest["files"]) <= 2000,
        "TRAINING_SOURCE_REVISION_OR_COUNT",
    )
    paths = list((archive / "source").rglob("*"))
    actual = {p.relative_to(archive / "source").as_posix() for p in paths if p.is_file()}
    require(
        actual == set(manifest["files"]) and not any(p.is_symlink() for p in paths),
        "EXACT_ARCHIVED_SOURCE_FILESET_REQUIRED",
    )
    for name, record in manifest["files"].items():
        path = local(archive / "source", name)
        raw = path.read_bytes()
        require(
            type(record["bytes"]) is int
            and len(raw) == record["bytes"]
            and sha256(raw).hexdigest() == record["sha256"]
            and sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            == record["gitblob"],
            "TRAINING_SOURCE_BYTES_CHANGED",
        )
    return {
        "manifest_sha256": expected_sha,
        "files": len(actual),
        "declared_overrides": len(manifest.get("overrides", [])),
        "declared_removed": len(manifest.get("removed", [])),
        "pure_git_tree_without_overrides_claimed": False,
    }


def verify(
    base: Path,
    models: Path,
    workflow: Path,
    data: Path,
    model_id: str,
    later_id: str,
    expected_active: str,
    expected_approval: str,
    calendar: Calendar,
    now: datetime,
    evidence: dict[str, Any],
    checks: dict[str, bool],
    trained_id: str,
    training_job_id: str,
    strategies: Path,
    validation_source: str,
    training_source_manifest: Path,
    training_source_manifest_sha: str,
) -> None:
    index = document(base / "online-research/control/models" / (model_id + ".json"))
    require(index["model_id"] == model_id, "MODEL_INDEX_IDENTITY_INVALID")
    archive = local(base, index["archive"])
    # Read just enough to choose short SQLite snapshots; full verification follows.
    header = document(archive / "summary.json")
    checked_identity(trained_id)
    require("rolling_plan" not in header, "EXPLICIT_LEGACY_BOUND_RESEARCH_REQUIRED")

    def read_facts():
        return legacy_snapshot(
            models, workflow, data, model_id, trained_id, later_id, training_job_id, strategies
        )

    facts = read_facts()
    evidence["available_target_jobs"] = len(facts["jobs"])
    evidence["available_target_proofs"] = len(facts["validations"])
    require(
        len(facts["jobs"]) == 1 and len(facts["validations"]) == 1,
        "REAL_LATER_JOB_AND_PROOF_REQUIRED_NO_EXECUTION_BY_AUDITOR",
    )
    evidence["snapshot_sha256"] = signed(facts)["evidence_sha256"]
    evidence["observed_data_head"] = (
        facts["data_head"][0]["version_id"] if len(facts["data_head"]) == 1 else None
    )
    require(len(facts["candidate"]) == 1, "CANDIDATE_NOT_REGISTERED")
    candidate = facts["candidate"][0]
    evidence.update(
        candidate_state=candidate["state"],
        registered_at=candidate["registered_at"],
        activation_count=candidate["activation_count"],
    )
    require(
        candidate["state"] == "STAGED"
        and type(candidate["activation_count"]) is int
        and candidate["activation_count"] == 0
        and candidate["activated_at"] is None,
        "CANDIDATE_NOT_UNACTIVATED_STAGED",
    )
    definition = json.loads(candidate["definition"])
    checked(definition)
    require(canonical(definition) == canonical(index["definition"]), "MODEL_DEFINITION_CHANGED")
    approval = facts["approval"]
    require(
        approval is not None
        and approval["model_id"] == expected_active
        and approval["evidence_sha256"] == expected_approval
        and expected_active != model_id,
        "ACTIVE_APPROVAL_CHANGED",
    )
    checked(approval)
    require(len(facts["active"]) == 1, "ACTIVE_MODEL_ROW_MISSING")
    active_row = facts["active"][0]
    active_definition = json.loads(active_row["definition"])
    checked(active_definition)
    require(
        active_row["model_id"] == expected_active
        and active_definition["model_id"] == expected_active
        and active_definition["schema_version"] == "qtrade.online-model.v1"
        and active_definition["fitted_restore_verified"] is True
        and active_definition["historical_pit_certified"] is False
        and active_row["activation_count"] >= 1
        and active_row["activated_at"] == approval["at"],
        "ACTIVE_MODEL_IDENTITY_INVALID",
    )
    active_training = [
        row
        for row in facts["model_events"]
        if row["model_id"] == expected_active and row["kind"] == "TRAINED"
    ]
    require(len(active_training) == 1, "ACTIVE_TRAINED_EVENT_MISSING")
    active_event = json.loads(active_training[0]["payload"])
    checked(active_event)
    require(
        active_event["model_id"] == expected_active
        and active_event["kind"] == "TRAINED"
        and active_event["at"] == active_row["registered_at"] == active_training[0]["at"]
        and active_event["details"]["definition_sha256"] == active_definition["evidence_sha256"],
        "ACTIVE_DEFINITION_EVENT_MISMATCH",
    )
    require(
        clock(active_row["registered_at"], now) <= clock(approval["at"], now),
        "ACTIVE_REGISTRATION_CLOCK_INVALID",
    )
    controls = [row for row in facts["model_events"] if row["kind"] == "CONTROL_APPLIED"]
    require(bool(controls), "ACTIVE_CONTROL_EVENT_MISSING")
    last = json.loads(controls[-1]["payload"])
    checked(last)
    require(
        last["details"] == approval
        and len(facts["commands"]) == 1
        and last["kind"] == controls[-1]["kind"] == "CONTROL_APPLIED"
        and last["model_id"] == controls[-1]["model_id"] == expected_active
        and last["at"] == controls[-1]["at"] == approval["at"],
        "ACTIVE_CONTROL_CHAIN_CHANGED",
    )
    require(
        active_row["activation_count"]
        == sum(row["model_id"] == expected_active for row in controls),
        "ACTIVE_CONTROL_COUNT_MISMATCH",
    )
    command_row = facts["commands"][0]
    command = json.loads(command_row["command"])
    require(
        json.loads(command_row["result"]) == approval
        and approval["command_sha256"] == signed(command)["evidence_sha256"],
        "ACTIVE_COMMAND_RECEIPT_MISMATCH",
    )
    approved_at = clock(approval["at"], now)
    checks["active_approval_unchanged"] = True

    exact_training_archive(archive)
    summary = verify_archive(archive)
    require(summary == header, "TRAINING_SUMMARY_CHANGED_DURING_SNAPSHOT")
    training_folder = base / "workflow/jobs" / training_job_id
    require(
        archive == training_folder / "output/runs" / summary["run_id"]
        and plain_document(training_folder / "request.json")
        == json.loads(facts["training_jobs"][0]["request"]),
        "TRAINING_ARCHIVE_NOT_ORIGINAL_JOB_OUTPUT",
    )
    evidence["training_source"] = verify_training_source(
        archive, training_source_manifest, training_source_manifest_sha, summary["source_revision"]
    )
    bundle = verify_bundle(archive / "online-restore", archive / "provider")
    restore = document(archive / "independent-restore.json")
    require(
        bundle["evidence_sha256"] == model_id
        and restore["bundle_sha256"] == model_id
        and restore["state"] == "PARITY_VERIFIED_NOT_ACTIVATED"
        and restore["fitted_processors_unchanged"] is True
        and restore["fit_methods_forbidden"] is True
        and restore["model_activated"] is False,
        "TRAINED_RESTORE_BINDING_INVALID",
    )
    require(
        all(
            definition[key] == value
            for key, value in {
                "model_id": model_id,
                "model_run_id": summary["run_id"],
                "training_data_end": summary["data_end"],
                "training_data_sha256": summary["dataset_sha256"],
                "training_code_revision": summary["source_revision"],
                "training_completed_at": summary["completed_at"],
                "restore_proof_sha256": restore["evidence_sha256"],
                "source_snapshot_sha256": summary["source_snapshot_sha256"],
                "fitted_restore_verified": True,
                "historical_pit_certified": False,
            }.items()
        ),
        "TRAINING_DEFINITION_BINDING_INVALID",
    )
    require(
        definition["fitted_restore_verified"] is True
        and definition["historical_pit_certified"] is False
        and definition["schema_version"] == "qtrade.online-model.v1"
        and type(definition["feature_count"]) is int
        and definition["feature_count"] == 158
        and bundle["code_revision"] == summary["source_revision"]
        and bundle["source_snapshot_sha256"] == summary["source_snapshot_sha256"]
        and re.fullmatch("[a-f0-9]{40}", summary["source_revision"]) is not None
        and math.isfinite(restore["max_absolute_score_difference"])
        and 0 <= restore["max_absolute_score_difference"] <= 1e-10
        and restore["feature_count"] == 158
        and all(
            restore[key] == bundle[key]
            for key in ("reference_rows", "reference_start", "reference_end", "handler_start")
        ),
        "TRAINED_RESTORE_PARITY_OR_CODE_INVALID",
    )
    trained_root = base / "unified-data/qlib-versions/versions" / trained_id
    later_root = base / "unified-data/qlib-versions/versions" / later_id
    trained, later = verify_version(trained_root), verify_version(later_root)
    require(
        trained["version_id"] == trained_id
        and later["version_id"] == later_id
        and trained_id != later_id
        and trained["actual_end"] == summary["data_end"]
        and later["actual_end"] > trained["actual_end"],
        "DATA_NOT_GENUINELY_LATER",
    )
    require(
        later["actual_end"] <= calendar.expected(now, "daily").date().isoformat(),
        "LATER_SESSION_NOT_COMPLETE",
    )
    trained_data = document(archive / "data-manifest.json")
    current_data = document(later_root / "dataset.json")
    require(
        trained_data == document(trained_root / "dataset.json")
        and file_hash(archive / "data-manifest.json")
        == summary["dataset_sha256"]
        == bundle["data_sha256"]
        == trained["files"]["dataset.json"],
        "TRAINING_DATA_VERSION_MISMATCH",
    )
    legacy_training_anchor(summary, facts, model_id, training_job_id, trained_id, now)
    request = document(archive / "request.json", authority=False)
    require(
        request["data_manifest_sha256"] == summary["dataset_sha256"]
        and request["source_revision"] == summary["source_revision"]
        and request["source_snapshot_sha256"] == summary["source_snapshot_sha256"],
        "TRAINING_REQUEST_MISMATCH",
    )
    training_at = clock(summary["completed_at"], now)
    restored_at = clock(restore["verified_at"], now)
    registered_at = clock(candidate["registered_at"], now)
    require(
        clock(trained["created_at"], now) <= restored_at <= training_at <= registered_at,
        "TRAINING_CLOCK_CHAIN_INVALID",
    )
    trained_events, input_events = staged_lifecycle(facts, model_id, definition, registered_at, now)
    verify_all_registered_proofs(base, facts, model_id, bundle, trained_data, calendar, now)
    source_files = document(archive / "checksums.json", authority=False)["files"]
    training_code = {
        name: value for name, value in source_files.items() if name.startswith("source/")
    }
    require(
        {"source/scripts/run_qlib_baseline.py", "source/scripts/qlib_restore_bundle.py"}
        <= set(training_code),
        "TRAINING_CODE_PROOF_MISSING",
    )
    evidence.update(
        training_run_id=summary["run_id"],
        trained_version_id=trained_id,
        trained_data_end=trained["actual_end"],
        later_data_end=later["actual_end"],
        training_summary_sha256=summary["evidence_sha256"],
        model_definition_sha256=definition["evidence_sha256"],
        restore_proof_sha256=restore["evidence_sha256"],
        training_code=training_code,
        training_origin="EXPLICIT_LEGACY_BOUND_RESEARCH_V1",
        training_origin_certified=True,
        training_job_verified=True,
    )
    checks["training_archive_and_registry"] = True

    require(
        len(facts["jobs"]) == 1 and len(facts["validations"]) == 1,
        "UNIQUE_LATER_VALIDATION_JOB_AND_REGISTRATION_REQUIRED",
    )
    job = facts["jobs"][0]
    queued_at, completed_at = job_chain(job, facts, now)
    job_request = json.loads(job["request"])
    checked(job_request)
    require(
        job_request["source_revision"] == validation_source, "EXPLICIT_VALIDATION_SOURCE_CHANGED"
    )
    bindings = {
        key: job_request[key] for key in ("kind", "model_id", "data_version_id", "source_revision")
    }
    require(
        job["id"] == job_request["job_id"] == signed(bindings)["evidence_sha256"][:32]
        and re.fullmatch("[a-f0-9]{40}", job_request["source_revision"]) is not None,
        "VALIDATION_JOB_IDENTITY_INVALID",
    )
    folder = base / "workflow/jobs" / job["id"]
    require(document(folder / "request.json") == job_request, "VALIDATION_JOB_REQUEST_CHANGED")
    result = json.loads(job["result"])
    validation_id = checked_identity(result["validation_id"])
    require(
        result
        == {"validation_id": validation_id, "model_id": model_id, "data_version_id": later_id},
        "VALIDATION_JOB_RESULT_CHANGED",
    )
    proof_index = document(base / "online-research/control/validations" / (validation_id + ".json"))
    proof_root = local(base, proof_index["archive"])
    require(
        proof_root == folder / "output/validations" / validation_id,
        "VALIDATION_ARCHIVE_NOT_JOB_OUTPUT",
    )
    proof = verify_validation(proof_root)
    checked(proof)
    checked(proof["contract"])
    require(
        document(folder / "output/result.json") == proof
        and proof_index["manifest_sha256"] == proof["evidence_sha256"]
        and proof_index["model_id"] == model_id
        and proof["validation_id"] == validation_id
        and proof["model_run_id"] == summary["run_id"]
        and proof["model_bundle_sha256"] == model_id,
        "VALIDATION_PROOF_IDENTITY_CHANGED",
    )
    registered = facts["validations"][0]
    require(
        registered["validation_id"] == validation_id and json.loads(registered["proof"]) == proof,
        "VALIDATION_REGISTRY_PROOF_CHANGED",
    )
    generated_at = clock(proof["contract"]["generated_at"], now)
    imported = [
        row
        for row in input_events
        if json.loads(row["payload"])["details"]["validation_id"] == validation_id
    ]
    require(len(imported) == 1, "TARGET_INPUT_VALIDATED_EVENT_REQUIRED")
    require(
        generated_at <= clock(imported[0]["at"], now) <= completed_at, "TARGET_IMPORT_CLOCK_INVALID"
    )
    require(
        proof["contract"]
        == inference_contract(
            bundle, trained_data, current_data, later, calendar, now=generated_at
        ),
        "VALIDATION_HISTORICAL_CONTRACT_CHANGED",
    )
    initial_contract = document(proof_root / "request.json")
    initial_at = clock(initial_contract["generated_at"], now)
    require(
        initial_contract
        == inference_contract(bundle, trained_data, current_data, later, calendar, now=initial_at)
        and approved_at <= queued_at <= initial_at <= generated_at <= completed_at
        and registered_at <= queued_at
        and clock(later["created_at"], now) <= queued_at,
        "VALIDATION_CLOCK_CHAIN_INVALID",
    )
    require(
        {
            "scripts/validate_qlib_forward.py",
            "scripts/qlib_restore_bundle.py",
            "src/qtrade/research/forward_prediction.py",
        }
        <= set(proof["bindings"]["code"]),
        "VALIDATION_CODE_PROOF_MISSING",
    )
    require(
        math.isfinite(proof["max_reference_score_difference"])
        and 0 <= proof["max_reference_score_difference"] <= 1e-10
        and proof["historical_reference_rows"] == bundle["reference_rows"],
        "VALIDATION_REFERENCE_PARITY_INVALID",
    )
    validation_rows(proof_root, later_root, proof, bundle, current_data)
    for version in (trained, later):
        ledger = next(
            (row for row in facts["versions"] if row["version_id"] == version["version_id"]), None
        )
        require(
            ledger is not None
            and ledger["manifest_sha256"] == version["evidence_sha256"]
            and ledger["actual_end"] == version["actual_end"],
            "DATA_CATALOG_BINDING_CHANGED",
        )
        admission = [
            row
            for row in facts["data_events"]
            if row["version_id"] == version["version_id"] and row["kind"] == "QLIB_DATA_READY"
        ]
        require(len(admission) == 1, "DATA_ADMISSION_EVENT_REQUIRED")
        payload = json.loads(admission[0]["payload"])
        checked(payload)
        require(
            payload["evidence_sha256"] == admission[0]["event_id"]
            and payload["kind"] == "QLIB_DATA_READY"
            and payload["version_id"] == version["version_id"]
            and payload["actual_end"] == version["actual_end"]
            and payload["manifest_sha256"] == version["evidence_sha256"]
            and clock(version["created_at"], now)
            <= clock(payload["admitted_at"], now)
            <= (queued_at if version is later else training_at),
            "DATA_ADMISSION_CLOCK_OR_IDENTITY_INVALID",
        )
    checks["later_automatic_validation_registered"] = True
    evidence.update(
        validation_job_id=job["id"],
        validation_id=validation_id,
        validation_sha256=proof["evidence_sha256"],
        validation_code=proof["bindings"]["code"],
        validation_queued_at=queued_at.isoformat(),
        validation_completed_at=completed_at.isoformat(),
    )
    evidence["training_raw_day"] = raw_day(base, trained_root, trained, calendar, now)
    evidence["later_raw_day"] = raw_day(base, later_root, later, calendar, now)
    checks["raw_day_receipts_and_normalized_rows"] = True
    require(read_facts() == facts, "CRITICAL_SNAPSHOT_CHANGED_DURING_REPLAY")
    checks["final_snapshot_unchanged"] = True


def audit(
    base,
    models,
    workflow,
    data,
    strategies,
    model_id,
    trained_id,
    later_id,
    training_job,
    validation_source,
    expected_active,
    expected_approval,
    training_source_manifest,
    training_source_manifest_sha,
    *,
    calendar=None,
    now=None,
):
    stamp = now or datetime.now(UTC)
    checks = {}
    evidence = {}
    errors = []
    try:
        require(stamp.tzinfo is not None, "AWARE_CLOCK_REQUIRED")
        require(
            os.environ.get("LIVE_TRADING_ENABLED", "false").lower() == "false",
            "LIVE_DISABLED_REQUIRED",
        )
        for identity in (model_id, trained_id, later_id, expected_active, expected_approval):
            checked_identity(identity)
        require(
            re.fullmatch("[a-f0-9]{32}", training_job) is not None
            and re.fullmatch("[a-f0-9]{40}", validation_source) is not None,
            "EXPLICIT_JOB_AND_SOURCE_REQUIRED",
        )
        verify(
            base,
            models,
            workflow,
            data,
            model_id,
            later_id,
            expected_active,
            expected_approval,
            calendar or Calendar(REPO / "configs/observer_calendar_2026.json"),
            stamp,
            evidence,
            checks,
            trained_id,
            training_job,
            strategies,
            validation_source,
            training_source_manifest,
            training_source_manifest_sha,
        )
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        text = str(error)
        errors.append(
            text
            if re.fullmatch("CANDIDATE_AUDIT_[A-Z0-9_]{1,120}", text)
            else "STAGED_REVALIDATION_INPUT_OR_ARCHIVE_INVALID"
        )
    return signed(
        {
            **AUTHORITY,
            "schema_version": "qtrade.legacy-staged-revalidation-audit.v1",
            "state": "BLOCKED" if errors else "VERIFIED_LEGACY_STAGED_LATER_INPUT_NOT_ACTIVATED",
            "training_contract": "LEGACY_BOUND_RESEARCH_V1",
            "validation_contract": "STAGED_INPUT_REVALIDATION_V1",
            "checked_at": stamp.isoformat(),
            "clock_source": "SYSTEM_UTC" if now is None else "CONTROLLED_TEST_ONLY",
            "candidate_model_id": model_id,
            "training_job_id": training_job,
            "training_version_id": trained_id,
            "later_version_id": later_id,
            "expected_validation_source": validation_source,
            "expected_current_model": expected_active,
            "expected_current_approval_sha256": expected_approval,
            "checks": checks,
            "evidence": evidence,
            "errors": errors,
            "read_only": True,
            "validation_executed_by_auditor": False,
            "model_activated_by_auditor": False,
            "strictly_unseen_forward_claimed": False,
            "auditor_sha256": file_hash(Path(__file__)),
            "whole_step1_accepted": False,
            "current_serving_admission_verified": False,
            "paper_forward_ledger_verified": False,
            "production_publication_verified": False,
            "browser_verified": False,
            "historical_pit_certified": False,
            "limitations": [
                "RECORDED_ARCHIVE_AND_PARQUET_REPLAY_NOT_NEW_TRAINING_OR_INFERENCE",
                "LEGACY_POST_HOC_RESEARCH_NOT_STRICTLY_UNSEEN_OR_HISTORICAL_PIT_EVIDENCE",
                "CONTENT_CHECKSUMS_AND_LEDGER_CLOCKS_NOT_EXTERNAL_TIMESTAMP_AUTHENTICATION",
                "SHORT_READONLY_SQLITE_SNAPSHOTS_ARE_RECHECKED_NOT_A_DISTRIBUTED_LOCK",
            ],
        }
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "base",
        "model-database",
        "workflow-database",
        "data-database",
        "strategy-database",
        "output",
        "training-source-manifest",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in (
        "model-id",
        "training-version",
        "later-version",
        "training-job",
        "validation-source",
        "expected-current-model",
        "expected-current-approval",
        "training-source-manifest-sha256",
    ):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--training-contract", choices=["LEGACY_BOUND_RESEARCH_V1"], required=True)
    parser.add_argument(
        "--validation-contract", choices=["STAGED_INPUT_REVALIDATION_V1"], required=True
    )
    args = parser.parse_args(argv)
    require(
        not os.path.lexists(args.output)
        and not any(args.output.resolve().is_relative_to(p.resolve()) for p in (args.base,))
        and not any(p.is_symlink() for p in args.output.parents),
        "OUTPUT_MUST_BE_NEW_AND_INDEPENDENT",
    )
    value = audit(
        args.base,
        args.model_database,
        args.workflow_database,
        args.data_database,
        args.strategy_database,
        args.model_id,
        args.training_version,
        args.later_version,
        args.training_job,
        args.validation_source,
        args.expected_current_model,
        args.expected_current_approval,
        args.training_source_manifest,
        args.training_source_manifest_sha256,
    )
    write_json_immutable(args.output, value)
    print(json.dumps(value, sort_keys=True, allow_nan=False))
    return 2 if value["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
