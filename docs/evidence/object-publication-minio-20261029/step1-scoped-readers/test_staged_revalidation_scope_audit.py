"""New contract checks only; old historical metadata never counts as actual D pass."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha1, sha256
from pathlib import Path

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts import audit_qlib_staged_revalidation as audit

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "docs/evidence/qlib-archived-version-20261028/cluster"


@pytest.fixture
def legacy():
    summary = json.loads((FIXTURE / "summary.json").read_bytes())
    record = json.loads((FIXTURE / "job-and-registry.json").read_bytes())
    job = {
        **record["job"],
        "request": json.dumps(record["request"]),
        "result": json.dumps(record["result"]),
    }
    link = {
        **record["strategy_association"][0],
        "contract_json": json.dumps(summary["strategy_definition"]),
    }
    return (
        summary,
        {
            "training_jobs": [job],
            "strategy_association": [link],
            "training_events": record["events"],
        },
        record,
    )


def check_legacy(value):
    summary, facts, record = value
    audit.legacy_training_anchor(
        summary,
        facts,
        record["model"]["model_id"],
        record["job"]["id"],
        record["request"]["spec"]["data_binding"]["data_version_id"],
        datetime.now(UTC),
    )


def test_real_legacy_metadata_contract_without_inventing_plan(legacy):
    check_legacy(legacy)
    assert "rolling_plan" not in legacy[0]


@pytest.mark.parametrize(
    "damage",
    [
        "fake_plan",
        "missing_job",
        "duplicate_job",
        "duplicate_strategy",
        "failed_job",
        "wrong_job_kind",
        "changed_model",
        "changed_run",
        "changed_version",
        "wrong_binding",
        "changed_strategy",
        "non_experiment",
        "champion",
        "skip_running",
        "unknown_training_event",
        "future_completed",
    ],
)
def test_legacy_anchor_rejects_bad_binding_and_job(legacy, damage):
    summary, facts, record = deepcopy(legacy)
    job = facts["training_jobs"][0]
    request = json.loads(job["request"])
    result = json.loads(job["result"])
    if damage == "fake_plan":
        summary["rolling_plan"] = {"data_version_id": "f" * 64}
    elif damage == "missing_job":
        facts["training_jobs"] = []
    elif damage == "duplicate_job":
        facts["training_jobs"].append(deepcopy(job))
    elif damage == "duplicate_strategy":
        facts["strategy_association"] *= 2
    elif damage == "failed_job":
        job["state"] = "FAILED"
    elif damage == "wrong_job_kind":
        request["kind"] = "QLIB_ROLLING_JOB"
    elif damage == "changed_model":
        result["model_artifact_id"] = "f" * 64
    elif damage == "changed_run":
        result["run_id"] = "OTHER_RUN"
    elif damage == "changed_version":
        record["request"]["spec"]["data_binding"]["data_version_id"] = "f" * 64
    elif damage == "wrong_binding":
        summary["data_binding"]["dataset_sha256"] = "f" * 64
    elif damage == "changed_strategy":
        facts["strategy_association"][0]["version"] = "f" * 64
    elif damage == "non_experiment":
        facts["strategy_association"][0]["status"] = "PAPER"
    elif damage == "champion":
        facts["strategy_association"][0]["role"] = "CHAMPION"
    elif damage == "skip_running":
        facts["training_events"].pop(1)
    elif damage == "unknown_training_event":
        facts["training_events"][2]["state"] = "ACTIVE"
    elif damage == "future_completed":
        summary["completed_at"] = "2099-01-01T00:00:00+00:00"
    job["request"] = json.dumps(request)
    job["result"] = json.dumps(result)
    if "data_binding" in summary:
        summary["data_binding"] = signed(
            {k: v for k, v in summary["data_binding"].items() if k != "evidence_sha256"}
        )
    with pytest.raises((ValueError, KeyError)):
        check_legacy((summary, facts, record))


@pytest.fixture
def staged():
    now = datetime(2026, 10, 29, 0, tzinfo=UTC)
    registered = now - timedelta(days=3)
    model = "a" * 64
    definition = signed({**AUTHORITY, "model_id": model})
    proofs = []
    for i, version in enumerate(("c" * 64, "d" * 64)):
        contract = signed(
            {
                **AUTHORITY,
                "model_bundle_sha256": model,
                "data_version_id": version,
                "generated_at": (registered + timedelta(hours=i * 24 + 1)).isoformat(),
            }
        )
        proof = signed(
            {
                **AUTHORITY,
                "validation_id": str(i + 1) * 64,
                "model_bundle_sha256": model,
                "contract": contract,
                "model_activated": False,
                "fit_methods_forbidden": True,
                "fitted_processors_unchanged": True,
                "auto_predict_enabled": False,
            }
        )
        proofs.append(
            {
                "validation_id": proof["validation_id"],
                "data_version_id": version,
                "model_id": model,
                "proof": json.dumps(proof),
            }
        )
    events = []
    for i, kind in enumerate(("TRAINED", "VALIDATING", "VALIDATED", "STAGED", "INPUT_VALIDATED")):
        at = registered if i == 0 else registered + timedelta(hours=2 if i < 4 else 26)
        details = (
            {"definition_sha256": definition["evidence_sha256"]}
            if i == 0
            else {"imported_verified_validation": proofs[0]["validation_id"]}
            if i < 4
            else {"validation_id": proofs[1]["validation_id"]}
        )
        payload = signed(
            {**AUTHORITY, "model_id": model, "kind": kind, "at": at.isoformat(), "details": details}
        )
        events.append(
            {
                "sequence": i + 1,
                "model_id": model,
                "kind": kind,
                "at": at.isoformat(),
                "payload": json.dumps(payload),
            }
        )
    return {"model_events": events, "all_validations": proofs}, model, definition, registered, now


def check_staged(value):
    return audit.staged_lifecycle(*value)


def test_original_staging_proof_retained_with_one_additional_input_validation(staged):
    original, imports = check_staged(staged)
    assert len(original) == 4 and len(imports) == 1


@pytest.mark.parametrize(
    "damage",
    [
        "missing_original",
        "orphan_original",
        "missing_target",
        "duplicate_proof",
        "duplicate_import",
        "unknown_event",
        "activated_event",
        "bad_event_model",
        "refit",
        "processors_changed",
        "activated_proof",
        "autopredict",
        "proof_version_mismatch",
        "import_before_proof",
        "naive_event",
    ],
)
def test_revalidation_preserves_complete_old_and_new_lifecycle(staged, damage):
    facts, model, definition, registered, now = deepcopy(staged)
    events = facts["model_events"]
    proofs = facts["all_validations"]
    if damage == "missing_original":
        proofs.pop(0)
    elif damage == "orphan_original":
        p = deepcopy(proofs[0])
        p["validation_id"] = "9" * 64
        proofs.append(p)
    elif damage == "missing_target":
        proofs.pop()
    elif damage == "duplicate_proof":
        proofs.append(deepcopy(proofs[0]))
    elif damage == "duplicate_import":
        events.append(deepcopy(events[-1]))
    elif damage in {"unknown_event", "activated_event", "bad_event_model", "naive_event"}:
        value = json.loads(events[-1]["payload"])
        if damage == "unknown_event":
            value["kind"] = "MYSTERY"
        elif damage == "activated_event":
            value["kind"] = "ACTIVE"
        elif damage == "bad_event_model":
            value["model_id"] = "f" * 64
        else:
            value["at"] = "2026-10-28T02:00:00"
        events[-1].update(
            kind=value["kind"],
            model_id=value["model_id"],
            at=value["at"],
            payload=json.dumps(signed({k: v for k, v in value.items() if k != "evidence_sha256"})),
        )
    else:
        value = json.loads(proofs[-1]["proof"])
        if damage == "refit":
            value["fit_methods_forbidden"] = False
        elif damage == "processors_changed":
            value["fitted_processors_unchanged"] = False
        elif damage == "activated_proof":
            value["model_activated"] = True
        elif damage == "autopredict":
            value["auto_predict_enabled"] = True
        elif damage == "proof_version_mismatch":
            value["contract"]["data_version_id"] = "f" * 64
        elif damage == "import_before_proof":
            value["contract"]["generated_at"] = now.isoformat()
        value["contract"] = signed(
            {k: v for k, v in value["contract"].items() if k != "evidence_sha256"}
        )
        proofs[-1]["proof"] = json.dumps(
            signed({k: v for k, v in value.items() if k != "evidence_sha256"})
        )
    with pytest.raises((ValueError, TypeError, KeyError)):
        check_staged((facts, model, definition, registered, now))


def test_exact_archive_rejects_extra_even_with_valid_manifest(tmp_path):
    (tmp_path / "payload").write_bytes(b"PUBLIC_SYNTHETIC")
    (tmp_path / "checksums.json").write_text(json.dumps(signed({"files": {"payload": "f" * 64}})))
    audit.exact_training_archive(tmp_path)
    (tmp_path / "hidden").write_bytes(b"UNDECLARED")
    with pytest.raises(ValueError, match="EXACT_TRAINING_ARCHIVE_FILESET_REQUIRED"):
        audit.exact_training_archive(tmp_path)


def test_no_clock_mutation_or_queue_constructor_in_cli():
    source = Path(audit.__file__).read_text()
    assert "--now" not in source and "ModelRegistry(" not in source and "Worker(" not in source
    assert "enqueue(" not in source and "activate(" not in source and "recover(" not in source


@pytest.mark.parametrize(
    "damage",
    [
        "none",
        "wrong_manifest_pin",
        "wrong_revision",
        "extra_source",
        "missing_source",
        "wrong_size",
        "wrong_sha256",
        "wrong_gitblob",
    ],
)
def test_complete_training_source_manifest_is_explicit_and_checks_both_hashes(tmp_path, damage):
    archive = tmp_path / "archive"
    (archive / "source/scripts").mkdir(parents=True)
    raw = b"PUBLIC_SYNTHETIC_SOURCE\n"
    source = archive / "source/scripts/example.py"
    source.write_bytes(raw)
    record = {
        "bytes": len(raw),
        "sha256": sha256(raw).hexdigest(),
        "gitblob": sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(),
    }
    manifest = {
        "revision": "b" * 40,
        "files": {"scripts/example.py": record},
        "overrides": [],
        "removed": [],
    }
    if damage == "wrong_revision":
        manifest["revision"] = "c" * 40
    elif damage == "extra_source":
        (archive / "source/undeclared.py").write_bytes(raw)
    elif damage == "missing_source":
        source.unlink()
    elif damage == "wrong_size":
        record["bytes"] += 1
    elif damage == "wrong_sha256":
        record["sha256"] = "f" * 64
    elif damage == "wrong_gitblob":
        record["gitblob"] = "f" * 40
    path = tmp_path / "source-manifest.json"
    path.write_text(json.dumps(manifest))
    expected = sha256(path.read_bytes()).hexdigest() if damage != "wrong_manifest_pin" else "f" * 64
    if damage == "none":
        result = audit.verify_training_source(archive, path, expected, "b" * 40)
        assert result["files"] == 1 and result["pure_git_tree_without_overrides_claimed"] is False
    else:
        with pytest.raises(ValueError):
            audit.verify_training_source(archive, path, expected, "b" * 40)
