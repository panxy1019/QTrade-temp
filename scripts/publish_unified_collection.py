"""Derive bounded current collection status without copying historical data versions."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qtrade.api.unified_data import validate_publication
from qtrade.market_data.sina_incremental import PRICE_BASIS
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts.atomic_json import write_json_atomic

REPO = Path(__file__).resolve().parents[1]


def publish_collection(
    base: Path, progress: dict[str, Any], *, destination: Path | None = None,
) -> dict[str, Any]:
    if any(progress.get(k) != v for k, v in AUTHORITY.items()):
        raise ValueError("COLLECTION_PROGRESS_AUTHORITY_MISMATCH")
    if "evidence_sha256" in progress and signed({
        k: v for k, v in progress.items() if k != "evidence_sha256"
    }) != progress:
        raise ValueError("COLLECTION_PROGRESS_HASH_MISMATCH")
    # The immutable observation master and its historical coverage keep their clock.
    original = json.loads((base / "unified-data/catalog/latest.json").read_text())
    document = {**AUTHORITY, "schema_version": "qtrade.unified-catalog.v1",
                "built_at": original["observed_at"], "master": original}
    validate_publication(signed(document))
    if progress["universe_version"] != original["universe_version"]:
        raise ValueError("INCREMENTAL_PUBLICATION_UNIVERSE_DRIFT")
    tasks = {(row["symbol"], row["frequency"]): row for row in progress["tasks"]}
    manifest_cache = {}
    for row in original["securities"]:
        if not row["in_target"]:
            continue
        row["incremental"] = {}
        for freq in ("day", "5min"):
            task = tasks.get((row["symbol"], freq))
            latest = None
            if task and task["last_success_batch"]:
                batch_id = task["last_success_batch"]
                if batch_id not in manifest_cache:
                    manifest = json.loads((base / "unified-data/lake/batches" /
                                           (batch_id + ".json")).read_text())
                    if (signed({k: v for k, v in manifest.items() if k != "evidence_sha256"})
                            != manifest or any(manifest.get(k) != v for k, v in AUTHORITY.items())):
                        raise ValueError("PUBLICATION_BATCH_MANIFEST_HASH_MISMATCH")
                    manifest_cache[batch_id] = manifest
                latest = manifest_cache[batch_id]["outcomes"][row["symbol"]]
                binding = manifest_cache[batch_id]["spec"]
                if (binding["frequency"] != freq or binding["universe_version"] != (
                        original["universe_version"]) or latest["state"] != "SUCCEEDED"
                        or datetime.fromisoformat(latest["end"]) != datetime.fromisoformat(
                            task["completed_end"])):
                    raise ValueError("PUBLICATION_TASK_BATCH_BINDING_MISMATCH")
            state = "PLANNED"
            if latest:
                state = "LIMITED_RESEARCH" if latest["missing_amount_bars"] else (
                    "CONTENT_COVERAGE_VERIFIED")
            if task and task["state"] in {"FAILED", "INTERRUPTED"}:
                state = "FAILED_RETAINING_LAST_SUCCESS" if latest else "QUARANTINED"
            row["incremental"][freq] = {
                "planned": True, "state": state,
                "task_state": task["state"] if task else "NOT_SCHEDULED",
                "expected_end": task["desired_end"] if task else None,
                "observed_start": task.get("accepted_start", latest["start"])
                if latest and task else None,
                "observed_end": task["completed_end"] if task else None,
                "bar_count": task.get("accepted_bar_count", latest["bar_count"] if latest else 0)
                if task else 0,
                "issues": [task["last_error"]] if task and task["last_error"] else [],
                "last_success_batch": task["last_success_batch"] if task else None,
                "last_success_at": manifest_cache[task["last_success_batch"]].get("completed_at")
                if latest and task else None,
                "next_attempt_at": task["next_attempt_at"] if task else None,
                "last_chunk_missing_amount_bars": latest["missing_amount_bars"] if latest else None,
                "price_basis": PRICE_BASIS, "independent_source_reconciled": False,
                "historical_pit_certified": False,
            }
    counts = {freq: dict(Counter(r["incremental"][freq]["state"]
                                for r in original["securities"] if r["in_target"]))
              for freq in ("day", "5min")}
    collection = {k: v for k, v in progress.items() if k not in {"tasks", "evidence_sha256"}}
    collection["source_progress_sha256"] = signed({
        k: v for k, v in progress.items() if k != "evidence_sha256"
    })["evidence_sha256"]
    collection["calendar"] = json.loads((REPO / "configs/observer_calendar_2026.json").read_text())
    collection["task_counts"] = dict(Counter(r["state"] for r in progress["tasks"]))
    master = signed({**{k: v for k, v in original.items() if k != "evidence_sha256"},
                     "incremental_counts": counts, "collection": collection})
    result = signed({**AUTHORITY, "schema_version": "qtrade.unified-catalog.v1",
                     "built_at": datetime.now(UTC).isoformat(), "master": master})
    validate_publication(result)
    write_json_atomic(destination or base / "unified-data/public/unified.json", result,
                      compact=True)
    return result
