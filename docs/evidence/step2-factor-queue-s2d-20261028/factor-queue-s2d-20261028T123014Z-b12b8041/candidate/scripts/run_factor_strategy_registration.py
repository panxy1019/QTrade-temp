"""One bounded nonproduction registration; no model, portfolio simulation or orders."""
from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qtrade.research.factor_strategy_catalog import validate_registration_request
from qtrade.research.factor_strategy_contract import strategy_spec
from qtrade.strategies.registry import StrategyRegistry
from scripts.atomic_json import write_json_immutable
from scripts.factor_research_contracts import (
    FactorResearchContracts,
    bounded_bytes,
    decode_json,
    registration_receipt,
    rejection_receipt,
)
from scripts.run_daily_factor_evaluation import isolated_output


def run(*, base: Path, data_database: Path, strategy_database: Path, archive_root: Path,
        request_path: Path, output: Path, source_revision: str) -> dict[str, Any]:
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise ValueError("FACTOR_REGISTRATION_LIVE_DISABLED_REQUIRED")
    request = validate_registration_request(decode_json(bounded_bytes(request_path, 16_000)))
    if request["source_revision"] != source_revision:
        raise ValueError("FACTOR_REGISTRATION_SOURCE_MISMATCH")
    output = isolated_output(output, protected=(Path(__file__).resolve().parents[1],
        base / "unified-data", data_database, strategy_database, archive_root, request_path))
    if any(output.iterdir()):
        raise ValueError("FACTOR_REGISTRATION_NEW_OUTPUT_REQUIRED")
    registry = StrategyRegistry(strategy_database)
    resolver = FactorResearchContracts(base, data_database, source_revision, registry, archive_root)
    try:
        definition = resolver.verify(request)
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
        write_json_immutable(output / "rejection.json",
                             rejection_receipt(request, datetime.now(UTC).isoformat()))
        raise
    # No conclusive REJECTED receipt is permitted after this line: a commit or
    # durable output error can leave the two databases at different checkpoints.
    registry.register_factor_research_experiment(strategy_spec(definition, now=datetime.now(UTC)),
        contract=definition, job_id=request["job_id"], actor=request["actor"])
    association = next(row for row in registry.research_jobs(definition["strategy_id"], limit=200)
                       if row["job_id"] == request["job_id"] and row["actor"] == request["actor"])
    receipt = registration_receipt(request, definition, association["registered_at"])
    # A crash after the commit leaves an auditable immutable association. The
    # worker reports uncertainty and never automatically restarts the old job.
    write_json_immutable(output / "strategy-definition.json", definition)
    write_json_immutable(output / "registration.json", receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("base", "data-database", "strategy-database", "archive-root", "request", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args()
    run(base=args.base, data_database=args.data_database, strategy_database=args.strategy_database,
        archive_root=args.archive_root, request_path=args.request, output=args.output,
        source_revision=args.source_revision)


if __name__ == "__main__":
    main()
