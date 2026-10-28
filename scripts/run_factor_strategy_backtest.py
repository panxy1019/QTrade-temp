"""Explicit nonproduction backtest of an existing registered factor contract.

All writable Qlib provider/cache/output state lives in one new isolated directory.
No queue, registration, model, promotion or external execution is invoked.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.data_versions import file_hash
from qtrade.research.factor_registry import FactorRegistry
from qtrade.research.factor_strategy_backtest import (
    LIMITATIONS,
    MAX_SCORE_ROWS,
    RESULT_SCHEMA,
    SCORE_SCHEMA,
    SOURCES,
    disabled,
    native_backtest,
    prepare_scores,
    require,
    score_series,
    source_snapshot,
    validate_portfolio_report,
    validate_request,
)
from qtrade.research.factor_strategy_contract import strategy_spec, validate_strategy
from qtrade.research.qlib_publication import verify_signed
from qtrade.research.strategy_contract import ready_dataset
from qtrade.research.workflow import digest
from qtrade.strategies.registry import StrategyRegistry
from scripts.atomic_json import write_json_immutable
from scripts.factor_research_contracts import EvaluationArchive, bounded_bytes, decode_json
from scripts.run_daily_factor_evaluation import isolated_output
from scripts.run_qlib_strategy_comparison import evaluate_metrics


def immutable_database_bytes(path: Path, maximum: int) -> bytes:
    for suffix in ("-wal", "-journal"):
        sidecar = path.with_name(path.name + suffix)
        require(
            not sidecar.exists() or bounded_bytes(sidecar, 0) == b"",
            "FROZEN_DATABASE_WITHOUT_WAL_REQUIRED",
        )
    return bounded_bytes(path, maximum)


def copy_provider(root: Path, output: Path) -> dict[str, str]:
    """Copy checked bytes, never hardlink into a writable Qlib provider/cache root."""
    version = decode_json(bounded_bytes(root / "version.json", 8_000_000))
    inventory = {
        name: value for name, value in version["files"].items() if name.startswith("provider/")
    }
    require(1 <= len(inventory) <= 50_000, "PROVIDER_FILE_BOUND")
    total = 0
    for name, expected in sorted(inventory.items()):
        destination = output / name
        require(destination.resolve().is_relative_to(output.resolve()), "PROVIDER_PATH")
        value = bounded_bytes(root / name, 32 * 1024**2)
        total += len(value)
        require(total <= 2 * 1024**3, "PROVIDER_BYTE_BOUND")
        require(sha256(value).hexdigest() == expected, "PROVIDER_SOURCE_BYTES_CHANGED")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as handle:
            handle.write(value)
        require(
            file_hash(root / name) == file_hash(destination) == expected,
            "PROVIDER_CHANGED_DURING_COPY",
        )
    return inventory


def run(
    *,
    base: Path,
    data_database: Path,
    strategy_database: Path,
    archive_root: Path,
    request_path: Path,
    output: Path,
    source_revision: str,
) -> dict[str, Any]:
    disabled()
    request_raw = bounded_bytes(request_path, 16_000)
    request = validate_request(decode_json(request_raw))
    require(request["source_revision"] == source_revision, "EXECUTION_SOURCE_REVISION")
    version_root = base / "unified-data/qlib-versions/versions" / request["data_version_id"]
    output = isolated_output(
        output,
        protected=(
            Path(__file__).resolve().parents[1],
            base / "unified-data",
            data_database,
            strategy_database,
            archive_root,
            request_path,
        ),
    )
    require(not any(output.iterdir()), "NEW_OUTPUT_REQUIRED_NO_RETRY")
    sources = source_snapshot()
    write_json_immutable(
        output / "execution-intent.json",
        signed(
            {
                **AUTHORITY,
                "request": request,
                "execution_source_sha256": sources,
                "started_at": datetime.now(UTC).isoformat(),
                "automatic_retry": False,
            }
        ),
    )
    try:
        # Use the real Registry reader only on an owned snapshot. Its constructor
        # initializes schemas; it must never be pointed at the protected input DB.
        database_bytes = immutable_database_bytes(strategy_database, 16_000_000)
        snapshot = output / "input-strategies.sqlite"
        snapshot.write_bytes(database_bytes)
        registry = StrategyRegistry(snapshot)
        record = registry.get(request["strategy_id"])
        definition = validate_strategy(
            registry.research_contract(request["strategy_id"]), verify_runtime_source=True
        )
        require(
            record.status.value == "EXPERIMENT"
            and record.role.value == "NONE"
            and record.spec == strategy_spec(definition, now=record.spec.created_at)
            and record.spec.version == request["strategy_version"]
            and definition["evidence_sha256"] == request["strategy_contract_sha256"]
            and any(
                job["job_id"] == request["registration_job_id"]
                for job in registry.research_jobs(request["strategy_id"], limit=200)
            ),
            "REGISTERED_REFERENCE",
        )
        index_raw = bounded_bytes(archive_root / "index.json", 100_000)
        index = decode_json(index_raw)
        verify_signed(index)
        require(
            set(index)
            == {
                *AUTHORITY,
                "schema_version",
                "registry_sha256",
                "evaluations",
                "templates",
                "evidence_sha256",
            }
            and index["schema_version"] == "qtrade.factor-research-archive-index.v1"
            and all(type(index.get(k)) is type(v) and index[k] == v for k, v in AUTHORITY.items())
            and type(index["evaluations"]) is list
            and 1 <= len(index["evaluations"]) <= 4,
            "APPROVED_ARCHIVE_INDEX",
        )
        factor_bytes = immutable_database_bytes(archive_root / "factors.sqlite", 2_000_000)
        require(
            sha256(factor_bytes).hexdigest() == index["registry_sha256"], "FACTOR_DATABASE_HASH"
        )
        reader = EvaluationArchive(archive_root, index["evaluations"])
        report = reader.get(definition["spec"]["evaluation_ref"]["evaluation_id"])
        factors = FactorRegistry(archive_root / "factors.sqlite", read_only=True)
        scores = prepare_scores(
            definition,
            report,
            factors=factors,
            version_root=version_root,
            data_database=data_database,
        )
        require(
            scores["data_binding"]["data_version_id"] == request["data_version_id"],
            "REQUEST_DATASET_BINDING",
        )
        write_json_immutable(output / "request.json", request)
        write_json_immutable(output / "strategy-definition.json", definition)
        write_json_immutable(output / "scores.json", scores)
        provider_inventory = copy_provider(version_root, output)
        portfolio, positions, packages = native_backtest(output / "provider", definition, scores)
        prediction, labels = score_series(scores)
        metrics, diagnostics = evaluate_metrics(portfolio, prediction, labels)
        portfolio.to_parquet(output / "portfolio-report.parquet")
        write_json_immutable(output / "positions.json", positions)
        write_json_immutable(output / "diagnostics.json", diagnostics)
        report_entry = next(
            entry
            for entry in index["evaluations"]
            if entry["evaluation_id"] == report["evaluation_id"]
        )
        require(
            ready_dataset(version_root, data_database) == scores["data_binding"]
            and immutable_database_bytes(strategy_database, 16_000_000) == database_bytes
            and immutable_database_bytes(archive_root / "factors.sqlite", 2_000_000) == factor_bytes
            and bounded_bytes(archive_root / "index.json", 100_000) == index_raw
            and bounded_bytes(request_path, 16_000) == request_raw
            and sha256(bounded_bytes(reader.path(report["evaluation_id"]), 8_000_000)).hexdigest()
            == report_entry["gzip_sha256"]
            and all(
                file_hash(output / name) == expected
                for name, expected in provider_inventory.items()
            )
            and source_snapshot() == sources,
            "INPUT_OR_SOURCE_CHANGED_DURING_EXECUTION",
        )
        for name, expected in sources.items():
            value = bounded_bytes(Path(__file__).resolve().parents[1] / name, 2_000_000)
            require(sha256(value).hexdigest() == expected, "SOURCE_CHANGED_DURING_SNAPSHOT")
            destination = output / "source" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as handle:
                handle.write(value)
        result = signed(
            {
                **AUTHORITY,
                "schema_version": RESULT_SCHEMA,
                "run_id": digest([request, sources]),
                "engine": "FACTOR_BACKTEST",
                "job_id": request["job_id"],
                "request_sha256": digest(request),
                "strategy_id": definition["strategy_id"],
                "strategy_version": definition["strategy_version"],
                "strategy_contract_sha256": definition["evidence_sha256"],
                "registration_job_id": request["registration_job_id"],
                "evaluation_ref": definition["spec"]["evaluation_ref"],
                "data_binding": scores["data_binding"],
                "factor_ref": scores["factor_ref"],
                "execution_source_revision": source_revision,
                "execution_source_sha256": sources,
                "registered_source_revision": definition["source"]["revision"],
                "provider_inventory_sha256": digest(provider_inventory),
                "packages": packages,
                "metrics": metrics,
                "diagnostic_label": "RAW_CLOSE_TO_CLOSE_NOT_PORTFOLIO_RETURN",
                "schedule": scores["schedule"],
                "executed_sessions": len(portfolio),
                "selected_score_rows": sum(row["score"] is not None for row in scores["rows"]),
                "rejected_score_rows": sum(row["score"] is None for row in scores["rows"]),
                "unknown_or_late_input_clocks": sum(
                    not row["dependencies_known_at_barrier"] for row in scores["rows"]
                ),
                "backtest_implemented": True,
                "native_qlib_simulation_completed": True,
                "original_registration_modified": False,
                "unseen_test_claim": False,
                "paper_admission": False,
                "model_activation": False,
                "benchmark": "CASH_ZERO_REFERENCE",
                "state": "COMPLETE_NONPRODUCTION_DIAGNOSTIC",
                "limitations": LIMITATIONS,
                "completed_at": datetime.now(UTC).isoformat(),
            }
        )
        write_json_immutable(output / "result.json", result)
        files = {
            p.relative_to(output).as_posix(): file_hash(p) for p in output.rglob("*") if p.is_file()
        }
        write_json_immutable(
            output / "manifest.json",
            signed(
                {
                    **AUTHORITY,
                    "schema_version": "qtrade.factor-strategy-backtest-archive.v1",
                    "run_id": result["run_id"],
                    "files": files,
                }
            ),
        )
        require(verify_archive(output) == result, "OUTPUT_READBACK")
        return result
    except BaseException as error:
        write_json_immutable(
            output / "failure.json",
            signed(
                {
                    **AUTHORITY,
                    "state": "FAILED_NO_ACCEPTED_BACKTEST_RESULT",
                    "job_id": request["job_id"],
                    "error_class": type(error).__name__,
                    "automatic_retry": False,
                    "failed_at": datetime.now(UTC).isoformat(),
                }
            ),
        )
        raise


def verify_archive(root: Path) -> dict[str, Any]:
    """Independent reload of immutable output; checksums are not external signatures."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    require(not (root / "failure.json").exists(), "FAILED_OUTPUT_NOT_ACCEPTED")
    manifest = decode_json(bounded_bytes(root / "manifest.json", 16_000_000))
    verify_signed(manifest)
    require(
        manifest["schema_version"] == "qtrade.factor-strategy-backtest-archive.v1"
        and all(
            type(manifest.get(k)) is type(v) and manifest[k] == v for k, v in AUTHORITY.items()
        ),
        "ARCHIVE_AUTHORITY",
    )
    files = manifest["files"]
    require(type(files) is dict and 1 <= len(files) <= 50_100, "ARCHIVE_INVENTORY_BOUND")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    require(actual == set(files) | {"manifest.json"}, "ARCHIVE_INVENTORY_CHANGED")
    total = 0
    for name, expected in files.items():
        path = root / name
        require(
            path.resolve().is_relative_to(root.resolve())
            and not any(part.is_symlink() for part in (path, *path.parents)),
            "ARCHIVE_PATH_ALIAS",
        )
        total += path.stat().st_size
        require(
            path.stat().st_size <= 64 * 1024**2
            and total <= 2300 * 1024**2
            and file_hash(path) == expected,
            "ARCHIVE_PHYSICAL_HASH_OR_BOUND",
        )
    request = validate_request(decode_json(bounded_bytes(root / "request.json", 16_000)))
    result = decode_json(bounded_bytes(root / "result.json", 4_000_000))
    scores = decode_json(bounded_bytes(root / "scores.json", 64 * 1024**2))
    definition = validate_strategy(
        decode_json(bounded_bytes(root / "strategy-definition.json", 256_000))
    )
    for value in (result, scores):
        verify_signed(value)
        require(
            all(type(value.get(k)) is type(v) and value[k] == v for k, v in AUTHORITY.items()),
            "OUTPUT_AUTHORITY",
        )
    require(
        result["schema_version"] == RESULT_SCHEMA
        and result["engine"] == "FACTOR_BACKTEST"
        and result["state"] == "COMPLETE_NONPRODUCTION_DIAGNOSTIC"
        and result["native_qlib_simulation_completed"] is True
        and result["backtest_implemented"] is True
        and result["paper_admission"] is result["model_activation"] is False
        and result["original_registration_modified"] is result["unseen_test_claim"] is False
        and result["limitations"] == LIMITATIONS,
        "OUTPUT_SCOPE",
    )
    sources = result["execution_source_sha256"]
    require(
        result["run_id"] == manifest["run_id"] == digest([request, sources])
        and result["request_sha256"] == digest(request)
        and result["job_id"] == request["job_id"]
        and result["strategy_id"] == request["strategy_id"] == definition["strategy_id"]
        and result["strategy_version"]
        == request["strategy_version"]
        == definition["strategy_version"]
        and result["registration_job_id"] == request["registration_job_id"]
        and result["execution_source_revision"] == request["source_revision"]
        and set(sources) == set(SOURCES)
        and scores["schema_version"] == SCORE_SCHEMA
        and type(scores["rows"]) is list
        and 1 <= len(scores["rows"]) <= MAX_SCORE_ROWS
        and scores["labels_used_to_select_or_trade"] is False
        and result["strategy_contract_sha256"]
        == definition["evidence_sha256"]
        == request["strategy_contract_sha256"]
        == scores["strategy_contract_sha256"]
        and result["evaluation_ref"]
        == definition["spec"]["evaluation_ref"]
        == scores["evaluation_ref"]
        and result["data_binding"]
        == definition["evaluation_context"]["data_binding"]
        == scores["data_binding"]
        and result["schedule"] == scores["schedule"]
        and all(
            file_hash(root / "source" / name) == expected for name, expected in sources.items()
        ),
        "OUTPUT_RELATIONS",
    )
    with closing(
        sqlite3.connect(
            (root / "input-strategies.sqlite").resolve().as_uri() + "?mode=ro", uri=True, timeout=3
        )
    ) as connection:
        stored = connection.execute(
            "SELECT s.version,s.status,s.role,c.contract_json "
            "FROM strategies s JOIN strategy_research_contracts c USING(strategy_id) "
            "JOIN strategy_research_jobs j USING(strategy_id) WHERE s.strategy_id=? "
            "AND j.job_id=?",
            (request["strategy_id"], request["registration_job_id"]),
        ).fetchall()
    require(
        len(stored) == 1
        and stored[0][:3] == (request["strategy_version"], "EXPERIMENT", "NONE")
        and decode_json(stored[0][3]) == definition,
        "OUTPUT_REGISTRATION_ASSOCIATION",
    )
    parquet = pq.ParquetFile(root / "portfolio-report.parquet")
    require(
        parquet.metadata.num_rows <= 2520
        and parquet.metadata.num_columns <= 32
        and sum(
            parquet.metadata.row_group(i).total_byte_size
            for i in range(parquet.metadata.num_row_groups)
        )
        <= 16_000_000,
        "OUTPUT_PORTFOLIO_DECODE_BOUND",
    )
    portfolio = parquet.read().to_pandas()
    validate_portfolio_report(portfolio, [row["execution_session"] for row in scores["schedule"]])
    prediction, labels = score_series(scores)
    metrics, diagnostics = evaluate_metrics(portfolio, prediction, labels)
    require(
        result["metrics"] == metrics
        and decode_json(bounded_bytes(root / "diagnostics.json", 4_000_000)) == diagnostics,
        "OUTPUT_METRICS_MISMATCH",
    )
    return dict(result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "data-database", "strategy-database", "archive-root", "request", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args()
    result = run(
        base=args.base,
        data_database=args.data_database,
        strategy_database=args.strategy_database,
        archive_root=args.archive_root,
        request_path=args.request,
        output=args.output,
        source_revision=args.source_revision,
    )
    print(json.dumps({key: result[key] for key in ("run_id", "state", "executed_sessions")}))


if __name__ == "__main__":
    main()
