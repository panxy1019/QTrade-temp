"""Read-only acceptance of one genuinely completed ETF SHADOW next-session transition.

Only an explicitly separate immutable audit report is written by the CLI. No
clock override, service cycle, source request, account constructor or DB is used.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import UTC, date, datetime, time
from hashlib import sha256
from pathlib import Path
from typing import Any

from qtrade.market_data.akshare import SHANGHAI
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_versions import file_hash
from qtrade.research.etf_research_contract import (
    verify_etf_shadow_observation,
    verify_etf_target,
)
from qtrade.research.etf_shadow_publication import validate_etf_shadow_publication
from qtrade.research.model_registry import checked_document, checked_identity
from scripts.atomic_json import write_json_immutable
from scripts.run_etf_shadow_service import intent, target_context

REPO = Path(__file__).resolve().parents[1]
PREFIX = "ETF_TRANSITION_AUDIT_"


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError(PREFIX+code)


def same(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
        right, sort_keys=True, allow_nan=False)


def document(path: Path, maximum: int = 300_000) -> dict[str, Any]:
    require(not path.is_symlink() and path.is_file() and 0 < path.stat().st_size <= maximum,
            "DOCUMENT_BOUND_OR_PATH_INVALID")
    value: dict[str, Any] = json.loads(path.read_bytes())
    checked_document(value)
    require(same({key: value[key] for key in AUTHORITY}, AUTHORITY), "AUTHORITY_TYPE_INVALID")
    return value


def tracked(path: Path, hashes: dict[Path, str], maximum: int = 300_000) -> dict[str, Any]:
    value = document(path, maximum)
    raw = path.read_bytes()
    require(same(json.loads(raw), value), "EVIDENCE_CHANGED_DURING_READ")
    hashes[path] = sha256(raw).hexdigest()
    return value


def source_files(root: Path, hashes: dict[Path, str]) -> dict[str, str]:
    tracked(root/"diagnostic.json", hashes)
    proof = tracked(root/"receipt.json", hashes)
    digest = checked_identity(proof["raw"]["sha256"])
    blob = root/"capture-store"/"objects"/(digest+".jsonl.gz")
    for path in (root/"capture-store", blob.parent, blob):
        require(not path.is_symlink(), "SOURCE_OBJECT_SYMLINK")
    require(blob.is_file() and 0 < blob.stat().st_size <= 64_000_000,
            "SOURCE_OBJECT_BOUND_INVALID")
    hashes[blob] = file_hash(blob)
    return {"diagnostic": hashes[root/"diagnostic.json"], "receipt": hashes[root/"receipt.json"],
            "raw_object": hashes[blob]}


def receipt(path: Path, schema: str, fields: dict[str, Any]) -> dict[str, Any]:
    value = document(path, 16_000)
    require(same(value, signed({**AUTHORITY, "schema_version": schema, **fields})),
            "LOCAL_RECEIPT_CONTEXT_MISMATCH")
    return value


def publication(path: Path, stamp: datetime) -> tuple[dict[str, Any], str, str]:
    require(not path.is_symlink() and path.is_file() and 0 < path.stat().st_size <= 1_100_000,
            "PUBLICATION_BOUND_OR_PATH_INVALID")
    raw = path.read_bytes()
    supplied = json.loads(raw)
    if isinstance(supplied, dict) and "publication" in supplied:
        require(set(supplied) == set(AUTHORITY) | {"publication_available", "service_fresh",
                "current_target", "publication"}, "API_ENVELOPE_FIELDS_INVALID")
        require(same({key: supplied[key] for key in AUTHORITY}, AUTHORITY)
                and supplied["publication_available"] is True and supplied["service_fresh"] is True
                and type(supplied["current_target"]) is bool,
                "API_PUBLICATION_UNAVAILABLE_OR_STALE")
        value, kind = supplied["publication"], "CAPTURED_PUBLIC_API_ENVELOPE"
    else:
        value, kind = supplied, "SIGNED_SERVICE_PUBLICATION"
    validate_etf_shadow_publication(value, now=stamp)
    require(value["state"] == "RUNNING" and not value["errors"], "PUBLIC_SERVICE_NOT_READY")
    for key in ("generated_at", "heartbeat_at"):
        age = (stamp-datetime.fromisoformat(value[key])).total_seconds()
        require(0 <= age <= 720, "PUBLICATION_NOT_FRESH")
    return value, kind, sha256(raw).hexdigest()


def _failure(error: Exception) -> str:
    text = str(error)
    if text.startswith((PREFIX, "ETF_CONTRACT_", "ETF_SHADOW_", "ETF_ARCHIVE_")) and re.fullmatch(
            r"[A-Z][A-Z0-9_]{0,159}", text):
        return text
    return PREFIX+"EVIDENCE_INVALID"


def audit_transition(archives: Path, local_root: Path, published_forward: Path, day: date,
                     target_id: str, calendar: Calendar, revision: str, *,
                     now: datetime | None = None) -> dict[str, Any]:
    """Use actual wall time in production; injected clocks are explicitly marked test-only."""
    checked_identity(target_id)
    require(re.fullmatch(r"[a-f0-9]{40}", revision) is not None, "REVISION_REQUIRED")
    require(os.environ.get("LIVE_TRADING_ENABLED", "false").lower() == "false",
            "LIVE_DISABLED_REQUIRED")
    started = now or datetime.now(UTC)
    require(started.tzinfo is not None, "AWARE_CLOCK_REQUIRED")
    failures: list[str] = []
    bindings: dict[str, Any] = {"target_id": target_id, "implementation_revision": revision,
        "calendar_sha256": signed(calendar.document)["evidence_sha256"]}
    observed_count = missing_count = None
    public_kind = None
    original_files: dict[Path, str] = {}
    try:
        close = datetime.combine(day, time(15), SHANGHAI)
        require(calendar.is_open(day) and started >= close
                and calendar.expected(started, "daily").date() >= day,
                "REAL_SESSION_NOT_COMPLETE")
        require(archives.is_dir() and not archives.is_symlink()
                and local_root.is_dir() and not local_root.is_symlink(), "INPUT_DIRECTORY_INVALID")
        public, public_kind, public_file_hash = publication(published_forward, started)
        bindings.update(publication_sha256=public["evidence_sha256"],
                        publication_file_sha256=public_file_hash)
        target_dir = local_root/"targets"/target_id
        require(not (local_root/"targets").is_symlink() and not target_dir.is_symlink(),
                "TARGET_DIRECTORY_INVALID")
        target_path = target_dir/"target.json"
        header = tracked(target_path, original_files)
        admission_path = target_dir/"admission.json"
        admission = tracked(admission_path, original_files, 16_000)
        source_id = checked_identity(header["bindings"]["diagnostic_id"])
        target_source = archives/source_id
        require(not target_source.is_symlink(), "SOURCE_DIRECTORY_SYMLINK")
        target_source_files = source_files(target_source, original_files)
        target = verify_etf_target(target_path, target_source, calendar=calendar, now=started)
        require(same(target, header) and target["target_id"] == target_id
            and datetime.fromisoformat(
            target["bindings"]["expected_execution_at"]) == datetime.combine(
                day, time(9, 30), SHANGHAI), "TARGET_DAY_OR_IDENTITY_CHANGED")
        contract_id = checked_identity(target["bindings"]["contract_id"])
        job = local_root/"contracts"/contract_id/"sessions"/str(day)
        for path in (local_root/"contracts", local_root/"contracts"/contract_id, job.parent, job):
            require(not path.is_symlink(), "SESSION_DIRECTORY_SYMLINK")
        tracked(job/"intent.json", original_files, 16_000)
        request = intent(job/"intent.json", contract_id, calendar, started)
        target_context(target, request, target_id)
        issued = datetime.fromisoformat(request["issued_at"])
        frozen = datetime.fromisoformat(target["frozen_at"])
        require(issued <= frozen, "INTENT_AFTER_TARGET_FREEZE")
        tracked(job/"target-binding.json", original_files, 16_000)
        bound = receipt(job/"target-binding.json", "qtrade.etf-shadow-target-binding.v1", {
            "intent_sha256": request["evidence_sha256"], "target_id": target_id})
        reference = tracked(job/"observation-source.json", original_files, 16_000)
        observed_source_id = checked_identity(reference["diagnostic_id"])
        receipt(job/"observation-source.json", "qtrade.etf-shadow-observation-source.v1", {
            "intent_sha256": request["evidence_sha256"], "target_id": target_id,
            "diagnostic_id": observed_source_id})
        completed = tracked(job/"completed.json", original_files, 16_000)
        observation_id = checked_identity(completed["observation_id"])
        receipt(job/"completed.json", "qtrade.etf-shadow-completed.v1", {
            "intent_sha256": request["evidence_sha256"], "target_id": target_id,
            "observation_id": observation_id})
        observed_source = archives/observed_source_id
        require(not observed_source.is_symlink(), "SOURCE_DIRECTORY_SYMLINK")
        observed_source_files = source_files(observed_source, original_files)
        observations = target_dir/"observations"
        require(not observations.is_symlink(), "OBSERVATION_DIRECTORY_SYMLINK")
        # Exact paths from immutable completion receipts, never directory-order discovery.
        observation_path = observations/(observation_id+".json")
        observed_header = tracked(observation_path, original_files)
        observation = verify_etf_shadow_observation(observation_path, target_path, target_source,
            observed_source, calendar=calendar, now=started)
        require(same(observation, observed_header)
                and observation["observation_id"] == observation_id
                and observation["session"] == str(day), "OBSERVATION_CONTEXT_MISMATCH")
        require(not observation["missing_target_inputs"], "SELECTED_CLOSES_INCOMPLETE")
        expected = {"target": target, "observation": observation}
        entries = [row for row in public["entries"] if row["target"]["target_id"] == target_id]
        require(len(entries) == 1 and same(entries[0], expected), "PUBLIC_ENTRY_MISSING_OR_CHANGED")
        require(public["contract_id"] == contract_id, "PUBLIC_CONTRACT_CHANGED")
        # Core/public validators already enforce SHADOW, blocked PAPER, empty execution
        # arrays and null cash/NAV/returns. Do not duplicate their behavior validators.
        files = {"intent": job/"intent.json", "target_binding": job/"target-binding.json",
            "observation_source": job/"observation-source.json", "completion": job/"completed.json",
            "target": target_path, "admission": admission_path, "observation": observation_path}
        bindings.update(contract_id=contract_id, intent_sha256=request["evidence_sha256"],
            target_binding_sha256=bound["evidence_sha256"], target_sha256=target["evidence_sha256"],
            admission_sha256=admission["evidence_sha256"], observation_id=observation_id,
            observation_sha256=observation["evidence_sha256"],
            local_file_sha256={name: original_files[path] for name, path in files.items()},
            target_source={"diagnostic_id": source_id,
                "diagnostic_sha256": target["bindings"]["diagnostic_sha256"],
                "receipt_sha256": target["bindings"]["receipt_sha256"],
                "file_sha256": target_source_files},
            observation_source={"diagnostic_id": observed_source_id,
                "diagnostic_sha256": observation["bindings"]["diagnostic_sha256"],
                "receipt_sha256": observation["bindings"]["receipt_sha256"],
                "file_sha256": observed_source_files},
            frozen_at=target["frozen_at"], durable_at=admission["durable_at"],
            expected_execution_at=target["bindings"]["expected_execution_at"],
            observed_at=observation["observed_at"], processed_at=observation["processed_at"])
        observed_count = len(observation["observed_closes"])
        missing_count = len(observation["missing_target_inputs"])
        # Reject a stale final result if real raw replay consumed the freshness budget.
        finished = now or datetime.now(UTC)
        require(finished >= started, "SYSTEM_CLOCK_MOVED_BACKWARDS")
        public_again, _, final_public_hash = publication(published_forward, finished)
        require(same(public_again, public)
                and final_public_hash == bindings["publication_file_sha256"],
                "PUBLICATION_CHANGED_DURING_AUDIT")
        require(all(not path.is_symlink() and file_hash(path) == digest
                    for path, digest in original_files.items()),
                "LOCAL_OR_SOURCE_EVIDENCE_CHANGED_DURING_AUDIT")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
        failures.append(_failure(error))
    finished = now or datetime.now(UTC)
    accepted = not failures
    return signed({**AUTHORITY, "schema_version": "qtrade.etf-forward-transition-audit.v1",
        "started_at": started.isoformat(), "audited_at": finished.isoformat(), "day": str(day),
        "clock_basis": "SYSTEM_WALL_CLOCK" if now is None else "CONTROLLED_TEST_CLOCK",
        "state": "PASSED" if accepted else "BLOCKED",
        "etf_shadow_transition_accepted": accepted, "security_type": "ETF", "mode": "SHADOW",
        "publication_input_kind": public_kind, "bindings": bindings, "failures": failures,
        "observed_close_count": observed_count, "missing_selected_close_count": missing_count,
        "execution_admitted": False, "paper_admission": "BLOCKED",
        "orders": [], "fills": [], "holdings": [], "cash": None, "net_asset_value": None,
        "forward_return": None, "whole_step1_accepted": False, "historical_pit_certified": False,
        "browser_verified": False, "limitations": [
            "ONLY_PINNED_ETF_TARGET_AND_EXACT_COMPLETED_NEXT_SESSION_AUDITED",
            "SHADOW_HAS_NO_EXECUTION_OR_PORTFOLIO_RETURN",
            "ETF_TRADING_RULES_ADJUSTMENTS_DISTRIBUTIONS_AND_SPLITS_NOT_CERTIFIED",
            "HASHES_ARE_INTEGRITY_BINDINGS_NOT_EXTERNAL_TIMESTAMP_OR_PIT_CERTIFICATION",
            "FILE_INPUT_BINDING_DOES_NOT_CERTIFY_NETWORK_DELIVERY_OR_BROWSER_RENDERING",
            "SYNTHETIC_TEST_CLOCKS_ARE_NOT_REAL_SESSION_ACCEPTANCE",
        ]})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archives", "local-root", "published-forward", "output"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--calendar", type=Path, default=REPO/"configs/observer_calendar_2026.json")
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args(argv)
    # Intentionally no --now and no calls to any target/service/account writer.
    try:
        output = args.output.resolve()
        require(not args.output.is_symlink() and not any(output.is_relative_to(path.resolve())
            for path in (args.archives, args.local_root, args.published_forward.parent)),
            "OUTPUT_MUST_BE_SEPARATE")
        result = audit_transition(args.archives, args.local_root, args.published_forward, args.day,
            args.target_id, Calendar(args.calendar), args.revision)
        write_json_immutable(args.output, result)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, _failure(error)+"\n")
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["etf_shadow_transition_accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
