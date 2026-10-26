"""Build an immutable public-source PIT gap report offline; no vendor or service calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.public_pit_audit import build_audit
from qtrade.research.workflow import digest
from scripts.atomic_json import write_json_immutable
from scripts.pit_collection_queue import read_attempts


def _signed(path: Path, raw: bytes | None = None) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_bytes() if raw is None else raw)
    if not isinstance(value, dict) or signed({
        k: v for k, v in value.items() if k != "evidence_sha256"
    }) != value:
        raise ValueError("PIT_AUDIT_INPUT_HASH_MISMATCH")
    return value


def _research_only(value: dict[str, Any]) -> bool:
    return all((value.get(k) is False) if v is False else value.get(k) == v
               for k, v in AUTHORITY.items())


def run(
    base: Path, *, official_path: Path | None = None, observed_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    root = base / "pit-repair"
    coverage = _signed(root / "coverage.json")
    if coverage.get("schema_version") != "qtrade.pit-repair.v1" or not _research_only(
        coverage
    ) or coverage.get("historical_pit_certified") is not False or coverage.get(
        "historical_available_at_known"
    ) is not False:
        raise ValueError("PIT_AUDIT_COVERAGE_AUTHORITY_INVALID")
    identity = coverage.get("master_sha256")
    if not isinstance(identity, str) or re.fullmatch(r"[a-f0-9]{64}", identity) is None:
        raise ValueError("PIT_AUDIT_MASTER_IDENTITY_INVALID")
    master = json.loads((root / "master" / (identity + ".json")).read_bytes())
    if digest(master) != identity or master.get("source") != "BaoStock query_stock_basic" or (
        master.get("historical_available_at") is not None
        or not master.get("rows") or len(master["rows"]) != coverage["master_rows"]
    ):
        raise ValueError("PIT_AUDIT_MASTER_INVALID")
    clock = datetime.fromisoformat(master["observed_at"])
    if clock.tzinfo is None or clock > now:
        raise ValueError("PIT_AUDIT_MASTER_CLOCK_INVALID")
    dataset = json.loads((base / "qlib-data-20261023/dataset.json").read_bytes())
    if "evidence_sha256" in dataset and signed({
        k: v for k, v in dataset.items() if k != "evidence_sha256"
    }) != dataset:
        raise ValueError("PIT_AUDIT_DATASET_HASH_MISMATCH")
    statuses, errors, byte_hashes = {}, {}, {}
    checksums = coverage.get("source_checksums")
    if not isinstance(checksums, dict) or len(checksums) > 1000:
        raise ValueError("PIT_AUDIT_STATUS_SCOPE_INVALID")
    for name, identity in checksums.items():
        if re.fullmatch(r"[036][0-9]{5}\.json", name) is None:
            raise ValueError("PIT_AUDIT_STATUS_PATH_INVALID")
        symbol = name[:6]
        path = root / "status" / name
        if not path.exists():
            errors[symbol] = "STATUS_CHECKPOINT_MISSING"
            byte_hashes[name] = None
            continue
        raw = path.read_bytes()
        byte_hashes[name] = hashlib.sha256(raw).hexdigest()
        try:
            record = _signed(path, raw)
            if digest(record) != identity:
                raise ValueError("PIT_AUDIT_STATUS_HASH_MISMATCH")
            statuses[symbol] = record
        except (ValueError, TypeError, KeyError):
            errors[symbol] = "STATUS_CHECKPOINT_INTEGRITY_INVALID"
    official = _signed(official_path) if official_path else None
    if official is not None and (
        official.get("schema_version") != "qtrade.official-master-reconciliation.v1"
        or not _research_only(official)
        or official.get("historical_pit_certified") is not False
        or official.get("historical_available_at") is not None
        or official.get("used_in_qlib") is not False
        or official.get("official_rows") != len(official["records"])
    ):
        raise ValueError("PIT_AUDIT_OFFICIAL_RECONCILIATION_INVALID")
    observed = _signed(observed_path) if observed_path else None
    if observed is not None and (
        observed.get("schema_version") != "qtrade.observed-security-master.v1"
        or not _research_only(observed)
        or observed.get("historical_pit_certified_count") != 0
        or any(row.get("available_at") is not None for row in observed["securities"])
    ):
        raise ValueError("PIT_AUDIT_OBSERVED_MASTER_INVALID")
    for document in (official, observed):
        if document is not None:
            observed_at = datetime.fromisoformat(document["observed_at"])
            if observed_at.tzinfo is None or observed_at > now:
                raise ValueError("PIT_AUDIT_SOURCE_OBSERVATION_CLOCK_INVALID")
    marker = root / "next-pass.json"
    schedule = json.loads(marker.read_bytes()) if marker.exists() else {}
    result = build_audit(
        dataset=dataset, coverage=coverage, master=master, statuses=statuses,
        status_errors=errors, attempts=read_attempts(root, dataset, now), schedule=schedule,
        official=official, observed=observed, now=now,
    )
    result = signed({**{k: v for k, v in result.items() if k != "evidence_sha256"},
                     "actual_status_file_sha256": byte_hashes})
    output = root / "audits" / (result["evidence_sha256"] + ".json")
    if not output.exists():
        write_json_immutable(output, result)
    elif json.loads(output.read_bytes()) != result:
        raise ValueError("PIT_AUDIT_OUTPUT_IDENTITY_CONFLICT")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--official-reconciliation", type=Path)
    parser.add_argument("--observed-master", type=Path)
    args = parser.parse_args()
    result = run(args.base, official_path=args.official_reconciliation,
                 observed_path=args.observed_master)
    print(json.dumps({k: v for k, v in result.items() if k not in {
        "records", "repair_tasks", "actual_status_file_sha256",
    }}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
