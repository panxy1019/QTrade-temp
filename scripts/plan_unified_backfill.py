"""Seal a bounded historical work plan without running or relabeling any collector."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from qtrade.market_data.backfill_plan import build_backfill_plan
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts.atomic_json import write_json_atomic, write_json_immutable


def publish_plan(base: Path, *, end: date, revision: str, years: int = 10) -> dict[str, Any]:
    master = json.loads((base / "unified-data/catalog/latest.json").read_text())
    value = build_backfill_plan(master, requested_end=end, observed_at=datetime.now(UTC),
                                code_revision=revision, years=years)
    root = base / "unified-data/backfill"
    path = root / "plans" / (value["evidence_sha256"] + ".json")
    write_json_immutable(path, value)
    summary = signed({
        **AUTHORITY, **{k: v for k, v in value.items() if k not in {"tasks", "evidence_sha256"}},
        "plan_sha256": value["evidence_sha256"], "plan_file": path.name,
    })
    write_json_atomic(root / "latest-plan.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--years", type=int, default=10)
    args = parser.parse_args()
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("LIVE_TRADING_MUST_REMAIN_DISABLED")
    print(json.dumps(publish_plan(args.base, end=args.end, revision=args.source_revision,
                                 years=args.years), ensure_ascii=False))


if __name__ == "__main__":
    main()
