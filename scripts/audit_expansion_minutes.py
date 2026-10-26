"""Recheck every old minute checkpoint without contacting a vendor or rewriting it."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from qtrade.data.minute_admission import RULE_VERSION, audit_minute_checkpoint
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts.atomic_json import write_json_atomic, write_json_immutable


def audit(root: Path, output: Path, *, requested_end: date) -> dict[str, Any]:
    receipts = []
    started = datetime.now(UTC)
    for path in sorted((root / "minute").glob("*/*.json")):
        if date.fromisoformat(path.stem) != requested_end:
            continue
        try:
            receipt = audit_minute_checkpoint(
                root, path, symbol=path.parent.name, requested_end=requested_end,
            )
        except Exception as error:
            # No vendor responses, environment or credentials enter public errors.
            receipt = signed({
                **AUTHORITY, "schema_version": "qtrade.minute-admission.v1",
                "rule_version": RULE_VERSION, "symbol": path.parent.name,
                "requested_end": str(requested_end), "state": "QUARANTINED",
                "checkpoint_sha256": sha256(path.read_bytes()).hexdigest(),
                "issues": ["CHECKPOINT_INTEGRITY_OR_CONTENT_FAILED"],
                "error_type": type(error).__name__,
            })
        target = output / "receipts" / (receipt["evidence_sha256"] + ".json")
        if not target.exists():
            write_json_immutable(target, receipt)
        elif json.loads(target.read_text()) != receipt:
            raise ValueError("ADMISSION_RECEIPT_CONFLICT")
        receipts.append(receipt)
    report = signed({
        **AUTHORITY, "schema_version": "qtrade.minute-admission-audit.v1",
        "rule_version": RULE_VERSION, "requested_end": str(requested_end),
        "started_at": started.isoformat(), "completed_at": datetime.now(UTC).isoformat(),
        "checkpoint_count": len(receipts),
        "states": dict(Counter(r["state"] for r in receipts)), "receipts": receipts,
    })
    target = output / "reports" / (report["evidence_sha256"] + ".json")
    write_json_immutable(target, report)
    write_json_atomic(output / "latest.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    report = audit(args.root, args.output, requested_end=args.end)
    print(json.dumps({k: report[k] for k in (
        "checkpoint_count", "states", "evidence_sha256", "completed_at",
    )}))


if __name__ == "__main__":
    main()
