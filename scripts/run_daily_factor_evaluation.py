"""Explicit nonproduction runner for reviewed, READY-version daily factor diagnostics."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qtrade.research.factor_evaluation import FactorEvaluationStore, evaluate_daily
from qtrade.research.factor_registry import FactorRegistry


def isolated_output(root: Path, *, protected: tuple[Path, ...]) -> Path:
    """Require an independent directory; no source/dataset/registry overwrite roots."""
    resolved = root.resolve()
    if any(parent.is_symlink() for parent in (root, *root.parents)) or any(
        resolved == path.resolve() or resolved.is_relative_to(path.resolve())
        or path.resolve().is_relative_to(resolved) for path in protected
    ):
        raise ValueError("FACTOR_EVALUATION_OUTPUT_OVERLAPS_PROTECTED_INPUT")
    if any(part.casefold() in {"production", "prod", "active", "services", "forward-paper"}
           for part in resolved.parts):
        raise ValueError("FACTOR_EVALUATION_NONPRODUCTION_OUTPUT_REQUIRED")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def run(*, version_root: Path, data_database: Path, factor_database: Path,
        request: dict[str, Any], output_root: Path) -> dict[str, str]:
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise ValueError("FACTOR_EVALUATION_LIVE_DISABLED_REQUIRED")
    if not factor_database.is_file() or factor_database.is_symlink():
        raise ValueError("FACTOR_EVALUATION_EXISTING_FACTOR_DATABASE_REQUIRED")
    output = isolated_output(output_root, protected=(
        Path(__file__).resolve().parents[1], version_root, data_database, factor_database))
    store = FactorEvaluationStore(output / "evaluations.sqlite")
    now = datetime.now(UTC)
    identity = store.start(request, now=now)
    try:
        report = evaluate_daily(version_root=version_root, data_database=data_database,
            registry=FactorRegistry(factor_database, read_only=True), request=request, now=now,
            evaluation_id=identity)
        reference = store.save(report)
    except Exception as exc:
        store.fail(identity, error_type=type(exc).__name__, now=datetime.now(UTC))
        raise
    # Review export follows the committed bundle. Exclusive creation prevents replacement.
    with (output / (identity + ".json")).open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2)
        handle.write("\n")
    return reference


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version-root", type=Path, required=True)
    parser.add_argument("--data-database", type=Path, required=True)
    parser.add_argument("--factor-database", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_bytes())
    reference = run(version_root=args.version_root, data_database=args.data_database,
        factor_database=args.factor_database, request=request, output_root=args.output_root)
    print(json.dumps(reference, sort_keys=True))


if __name__ == "__main__":
    main()
