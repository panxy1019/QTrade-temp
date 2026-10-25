"""Persist operator-run research experiments; this is not a public write API."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from qtrade.api.app import (
    BacktestPreviewRequest,
    ExperimentRunSpecDraftRequest,
    _backtest_preview_spec,
    _experiment_run_spec_draft,
    _load_verified_experiment_catalog,
)
from qtrade.data import DataManifest, ParquetBarStore, QualityStatus
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.persistence import ExperimentCatalogEntry, SQLiteMetadataCatalog
from qtrade.research import (
    DailyMultiAssetBacktester,
    EqualWeightBuyHold,
    ExperimentOutput,
    ExperimentRunner,
    ExperimentRunSpec,
    MomentumAllocation,
    TimeSplit,
    experiment_run_spec_hash,
    write_experiment_artifacts,
)
from qtrade.research.factors import PriceFactorSpec
from qtrade.research.multi_asset import RankedFactorAllocation
from scripts.atomic_json import write_json_immutable
from scripts.run_akshare_etf_experiments import _manifest, _metrics
from scripts.run_data_observer import read_report

REPO = Path(__file__).resolve().parents[1]


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def seal_archive(root: Path) -> None:
    files = {
        str(path.relative_to(root)).replace("\\", "/"): sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    for path in root.rglob("*"):
        if path.is_file():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
    write_json_immutable(root / "archive-manifest.json", signed({"files": files}))


def verify_archive(root: Path) -> dict[str, Any]:
    document = json.loads((root / "archive-manifest.json").read_text(encoding="utf-8"))
    if document != signed({"files": document["files"]}):
        raise ValueError("ARCHIVE_MANIFEST_CHECKSUM_MISMATCH")
    if not document["files"] or "publication.json" not in document["files"]:
        raise ValueError("INCOMPLETE_ARCHIVE")
    for name, digest in document["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError("INVALID_ARCHIVE_PATH")
        if sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("ARCHIVE_FILE_CHECKSUM_MISMATCH")
    return cast(dict[str, Any], document)


def prepare_daily_dataset(observer_root: Path, dataset_root: Path) -> Path:
    old_root = REPO / "deployment/evidence/market-data"
    old = json.loads((old_root / "manifest.json").read_text(encoding="utf-8"))
    state = json.loads((observer_root / "collector-state.json").read_text())
    report_path = Path(state["daily"]["report_path"])
    report = read_report(report_path)
    old_bars = (
        ParquetBarStore(old_root.parent).read(_manifest(old))
        if (old_root.name == old["manifest"]["data_version"])
        else None
    )
    # Bundled evidence uses a stable directory name rather than the data-version name.
    if old_bars is None:
        import tempfile

        with tempfile.TemporaryDirectory() as stage:
            version_dir = Path(stage) / old["manifest"]["data_version"]
            version_dir.mkdir()
            shutil.copy2(old_root / "bars.parquet", version_dir / "bars.parquet")
            old_bars = ParquetBarStore(Path(stage)).read(_manifest(old))
    new_manifest = DataManifest(
        report["manifest"]["data_version"],
        report["manifest"]["source"],
        report["manifest"]["schema_version"],
        datetime.fromisoformat(report["completed_at"]),
        report["manifest"]["checksum"],
        QualityStatus.PASSED,
    )
    recent = ParquetBarStore(observer_root / "data/daily").read(new_manifest)
    combined = {(bar.symbol, bar.timestamp): bar for bar in old_bars}
    for bar in recent:
        key = (bar.symbol, bar.timestamp)
        if key in combined and combined[key] != bar:
            raise ValueError("CONFLICTING_BAR_REVISION")
        combined[key] = bar
    bars = sorted(combined.values(), key=lambda bar: (bar.timestamp, bar.exchange, bar.symbol))
    source_hash = sha256(
        canonical([old["manifest"]["checksum"], new_manifest.checksum])
    ).hexdigest()
    version = f"etf30-continuation-{max(bar.trading_day for bar in bars):%Y%m%d}-{source_hash[:12]}"
    target = dataset_root / version / "manifest.json"
    if target.exists():
        previous = json.loads(target.read_text())
        ParquetBarStore(dataset_root).read(_manifest(previous))
        return target
    manifest = ParquetBarStore(dataset_root).write(
        bars,
        DataManifest(
            version,
            "AKShare raw daily: frozen archive plus checksum-bound continuation",
            "qtrade.bar.v1",
            datetime.now(UTC),
            "pending",
            QualityStatus.PENDING,
        ),
    )
    document = {
        **AUTHORITY,
        "manifest": json.loads(canonical(asdict(manifest))),
        "universe": old["universe"],
        "bar_count": len(bars),
        "session_count": len({bar.trading_day for bar in bars}),
        "observed_start": min(bar.timestamp for bar in bars).isoformat(),
        "observed_end": max(bar.timestamp for bar in bars).isoformat(),
        "source_checksums": [old["manifest"]["checksum"], new_manifest.checksum],
    }
    write_json_immutable(target, signed(document))
    return target


def require_local_catalog(path: Path) -> None:
    mounts = Path("/proc/mounts")
    if mounts.exists():
        matches = []
        for line in mounts.read_text().splitlines():
            fields = line.split()
            if len(fields) >= 3 and path.resolve().is_relative_to(Path(fields[1])):
                matches.append((len(fields[1]), fields[2]))
        if matches and max(matches)[1].startswith(("nfs", "cifs")):
            raise ValueError("SQLITE_CATALOG_REQUIRES_LOCAL_FILESYSTEM")


def register_archive(root: Path, catalog_path: Path) -> None:
    require_local_catalog(catalog_path)
    verify_archive(root)
    if not (root / "run-spec.json").exists():
        return  # Legacy archives do not contain independently reconstructible complete specs.
    spec = json.loads((root / "run-spec.json").read_text())
    raw = spec["spec"]
    catalog = SQLiteMetadataCatalog(catalog_path)
    catalog.register_dataset(_manifest(json.loads((root / "dataset-manifest.json").read_text())))
    catalog.register_experiment(
        ExperimentCatalogEntry(
            raw["experiment_id"],
            spec["spec_hash"],
            raw["git_commit"],
            raw["data_version"],
            str((root / f"{raw['experiment_id']}.json").resolve()),
        )
    )


def persist_experiment(
    request: dict[str, Any],
    manifest_path: Path,
    output_root: Path,
    catalog_path: Path,
    git_commit: str,
) -> dict[str, Any]:
    require_local_catalog(catalog_path)
    if re.fullmatch(r"[0-9a-f]{40}", git_commit) is None:
        raise ValueError("FULL_SOURCE_COMMIT_REQUIRED")
    evidence = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = _manifest(evidence)
    bars = ParquetBarStore(manifest_path.parent.parent).read(manifest)
    dataset = {
        **evidence,
        "data_version": manifest.data_version,
        "checksum": manifest.checksum,
    }
    payload = ExperimentRunSpecDraftRequest.model_validate({**request, "git_commit": git_commit})
    draft = _experiment_run_spec_draft(payload, dataset)
    raw = cast(dict[str, Any], draft["spec"])
    spec = ExperimentRunSpec(
        raw["experiment_id"],
        raw["strategy_id"],
        raw["git_commit"],
        raw["data_version"],
        tuple(raw["universe"]),
        tuple(raw["factor_set"]),
        raw["label"],
        TimeSplit(**{key: date.fromisoformat(value) for key, value in raw["split"].items()}),
        raw["model"],
        tuple((str(pair[0]), str(pair[1])) for pair in raw["parameters"]),
        raw["cost_model_id"],
        Decimal(raw["transaction_cost"]),
        raw["seed"],
    )
    destination = output_root / spec.experiment_id
    if destination.exists():
        verify_archive(destination)
        previous = json.loads((destination / "run-spec.json").read_text())
        if previous["spec_hash"] != experiment_run_spec_hash(spec):
            raise ValueError("IMMUTABLE_EXPERIMENT_ID_CONFLICT")
        register_archive(destination, catalog_path)
        return cast(dict[str, Any], json.loads((destination / "publication.json").read_text()))
    destination.mkdir(parents=True)
    write_json_immutable(destination / "dataset-manifest.json", evidence)
    write_json_immutable(
        destination / "run-spec.json",
        {
            "spec": raw,
            "spec_hash": experiment_run_spec_hash(spec),
            "historical_test_window_previously_available": True,
            "independent_leakage_review_passed": False,
        },
    )
    preview = _backtest_preview_spec(BacktestPreviewRequest.model_validate(request["backtest"]))
    kwargs: dict[str, Any] = dict(
        top_k=preview.top_k,
        rebalance_every=preview.rebalance_every,
        reserve=preview.reserve,
        trend_window=preview.trend_window,
        volatility_window=preview.volatility_window,
        target_annual_volatility=preview.target_annual_volatility,
    )
    model = (
        MomentumAllocation(preview.horizons, **kwargs)
        if preview.factor == "momentum"
        else (
            RankedFactorAllocation(
                tuple(
                    (PriceFactorSpec(preview.factor, horizon), Decimal("1"))
                    for horizon in preview.horizons
                ),
                **kwargs,
            )
        )
    )
    catalog = SQLiteMetadataCatalog(catalog_path)
    runner = ExperimentRunner(destination, catalog=catalog)
    eligible = [
        bar for bar in bars if spec.split.train_start <= bar.trading_day <= spec.split.test_end
    ]

    def evaluate(spec: ExperimentRunSpec) -> ExperimentOutput:
        result = DailyMultiAssetBacktester(
            initial_cash=preview.initial_cash,
            transaction_cost_rate=preview.transaction_cost_rate,
            slippage_rate=preview.slippage_rate,
        ).run(eligible, model)
        artifacts = write_experiment_artifacts(result, destination / "artifacts")
        # A same-window benchmark is persisted independently from active-strategy artifacts.
        benchmark = DailyMultiAssetBacktester(
            initial_cash=preview.initial_cash,
            transaction_cost_rate=preview.transaction_cost_rate,
            slippage_rate=preview.slippage_rate,
        ).run(eligible, EqualWeightBuyHold(tuple(raw["universe"]), reserve=preview.reserve))
        write_experiment_artifacts(benchmark, destination / "benchmark")
        write_json_immutable(
            destination / "benchmark-metrics.json",
            json.loads(
                canonical(dict(_metrics(benchmark, spec.split.test_start, spec.split.test_end)))
            ),
        )
        return ExperimentOutput(
            _metrics(result, spec.split.test_start, spec.split.test_end), artifacts
        )

    result = runner.run(spec, manifest=manifest, evaluator=evaluate)
    write_json_immutable(destination / "result.json", json.loads(canonical(asdict(result))))
    publication = {
        "experiment_id": spec.experiment_id,
        "model": spec.model,
        "persistence_kind": "FORMAL_IMMUTABLE_RUN",
        "persisted": True,
        "spec_hash": result.spec_hash,
        "result_hash": result.result_hash,
        "git_commit": git_commit,
        "data_version": manifest.data_version,
        "data_checksum": manifest.checksum,
        "universe_size": len(spec.universe),
        "split": raw["split"],
        "parameters": dict(spec.parameters),
        "metrics": {key: str(value) for key, value in result.metrics},
        "benchmark_metrics": json.loads((destination / "benchmark-metrics.json").read_text()),
        "completed_at": result.completed_at.isoformat(),
        "research_classification": "POST_HOC_CONTINUATION_DIAGNOSTIC_NON_PIT",
        "fresh_unseen_test_evidence": False,
        "promotion": "NOT_ELIGIBLE",
        "external_order_transmission": False,
    }
    write_json_immutable(destination / "publication.json", publication)
    # Create SQLite backups locally, then store them as immutable archival bytes on NFS.
    import tempfile

    with tempfile.TemporaryDirectory(dir=catalog_path.parent) as stage:
        backup = Path(stage) / "metadata.sqlite"
        with sqlite3.connect(catalog_path) as source, sqlite3.connect(backup) as target:
            source.backup(target)
        shutil.copy2(backup, destination / "metadata-backup.sqlite")
    seal_archive(destination)
    return publication


def archive_legacy(output_root: Path) -> int:
    source = REPO / "deployment/evidence/akshare-v4"
    verified = _load_verified_experiment_catalog(source / "comparison.json", source)
    if verified is None:
        raise ValueError("LEGACY_EXPERIMENT_VERIFICATION_FAILED")
    rows = cast(dict[str, Any], verified["report"])["experiments"]
    for row in rows:
        destination = output_root / row["experiment_id"]
        if destination.exists():
            verify_archive(destination)
            continue
        destination.mkdir(parents=True)
        identity = row["experiment_id"]
        record = source / f"{identity}.json"
        shutil.copy2(record, destination / record.name)
        shutil.copytree(source / f"{identity}-artifacts", destination / "artifacts")
        write_json_immutable(
            destination / "publication.json",
            {
                **{
                    key: row[key]
                    for key in (
                        "experiment_id",
                        "model",
                        "parameters",
                        "split",
                        "metrics",
                        "spec_hash",
                        "result_hash",
                        "record_hash",
                        "git_commit",
                        "data_version",
                        "universe_size",
                    )
                },
                "persisted": True,
                "persistence_kind": "VERIFIED_LEGACY_ARCHIVE",
                "legacy_spec_hash_independently_recomputed": False,
                "research_classification": "LEGACY_CURRENT_UNIVERSE_NON_PIT",
                "promotion": "NOT_ELIGIBLE",
                "external_order_transmission": False,
            },
        )
        seal_archive(destination)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="action", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--observer-root", type=Path, required=True)
    prepare.add_argument("--dataset-root", type=Path, required=True)
    legacy = commands.add_parser("archive-legacy")
    legacy.add_argument("--output-root", type=Path, required=True)
    rebuild = commands.add_parser("rebuild-catalog")
    rebuild.add_argument("--output-root", type=Path, required=True)
    rebuild.add_argument("--catalog", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--request", type=Path, required=True)
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--catalog", type=Path, required=True)
    run.add_argument("--git-commit", required=True)
    args = parser.parse_args()
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("live trading must remain disabled")
    if args.action == "prepare":
        print(prepare_daily_dataset(args.observer_root, args.dataset_root))
    elif args.action == "archive-legacy":
        print(json.dumps({"archived_experiments": archive_legacy(args.output_root)}))
    elif args.action == "rebuild-catalog":
        for archive in sorted(args.output_root.glob("*/archive-manifest.json")):
            register_archive(archive.parent, args.catalog)
        print("CATALOG_REBUILT_FROM_VERIFIED_ARCHIVES")
    else:
        result = persist_experiment(
            json.loads(args.request.read_text(encoding="utf-8")),
            args.manifest,
            args.output_root,
            args.catalog,
            args.git_commit,
        )
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
