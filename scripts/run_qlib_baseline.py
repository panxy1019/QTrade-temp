"""Fixed Alpha158/LightGBM experiment; cluster-only, immutable artifacts, no orders."""

from __future__ import annotations

import argparse
import json
import os
import pickle
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.qlib_adapter import calendar_boundary_tail, causal_capacity
from qtrade.research.source_constraints import POLICY, SourceSnapshot, capture_source, restrict_rows
from scripts.atomic_json import write_json_atomic

REPO = Path(__file__).resolve().parents[1]


def checksum(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def make_provider(
    dataset_root: Path, target: Path, *, source_snapshot: SourceSnapshot | None = None,
) -> dict[str, Any]:
    import numpy as np
    import pandas as pd

    document: dict[str, Any] = json.loads((dataset_root / "dataset.json").read_text())
    if signed({k: v for k, v in document.items() if k != "evidence_sha256"}) != document:
        raise ValueError("QLIB_DATASET_SIGNATURE_MISMATCH")
    if document["accepted_symbols"] < 500:
        raise ValueError("LESS_THAN_500_QUALIFIED_STOCKS")
    benchmark_path = dataset_root / "benchmark/raw.json"
    if checksum(benchmark_path) != document["benchmark_sha256"]:
        raise ValueError("BENCHMARK_CHECKSUM_MISMATCH")
    raw_index = json.loads(benchmark_path.read_text())["rows"]
    dates = [str(r["date"])[:10] for r in raw_index]
    if dates != sorted(set(dates)):
        raise ValueError("INVALID_BENCHMARK_CALENDAR")
    # Qlib needs the next exchange boundary to close its final backtest step.
    # Append calendar-only sessions, leaving every price/volume field NaN there.
    tail = calendar_boundary_tail(
        dates[-1], json.loads((REPO / "configs/observer_calendar_2026.json").read_text())
    )
    dates += tail
    (target / "calendars").mkdir(parents=True, exist_ok=True)
    (target / "instruments").mkdir(exist_ok=True)
    (target / "calendars/day.txt").write_text("\n".join(dates) + "\n")
    (target / "calendars/day_future.txt").write_text("\n".join(dates) + "\n")
    instruments = []
    constraint_rows: list[dict[str, Any]] = []
    constraint_counts: list[dict[str, Any]] = []
    field_names = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "vwap",
        "factor",
        "amount",
        "buy_block",
        "sell_block",
        "capacity",
        "change",
    ]

    def dump(code: str, frame: Any, fields: list[str]) -> None:
        folder = target / "features" / code.lower()
        folder.mkdir(parents=True, exist_ok=True)
        aligned = frame.reindex(dates)
        for field in fields:
            values = np.r_[np.float32(0), aligned[field].to_numpy(dtype="<f4")].astype("<f4")
            values.tofile(folder / f"{field}.day.bin")
        instruments.append(f"{code}\t{frame.index[0]}\t{frame.index[-1]}")

    for row in document["stocks"]:
        folder = dataset_root / "stocks" / row["symbol"]
        for name, digest in row["files"].items():
            if checksum(folder / name) != digest:
                raise ValueError("QLIB_INPUT_FILE_CHECKSUM_MISMATCH")
        frame = pd.read_parquet(folder / "daily.parquet").set_index("date")
        if not set(frame.index) <= set(dates):
            raise ValueError("STOCK_DAY_OUTSIDE_BENCHMARK_CALENDAR")
        if source_snapshot is not None:
            restricted, counts, ledger = restrict_rows(
                frame.reset_index().to_dict("records"), row["symbol"],
                source_snapshot.statuses.get(row["symbol"]),
            )
            frame = pd.DataFrame(restricted).set_index("date")
            constraint_counts.append(counts)
            constraint_rows.extend(ledger)
        capacity = causal_capacity(frame.reset_index().to_dict("records"), dates)
        frame["capacity"] = pd.Series(capacity)
        frame["change"] = frame["close"] / frame["close"].shift(1) - 1
        code = ("SH" if row["symbol"].startswith("6") else "SZ") + row["symbol"]
        dump(code, frame, field_names)
    index_frame = pd.DataFrame(raw_index)
    index_frame["date"] = index_frame["date"].str[:10]
    index_frame = index_frame.set_index("date")
    for field in ("open", "high", "low", "close"):
        index_frame[field] = index_frame[field] / float(raw_index[0]["close"])
    index_frame["factor"] = 1 / float(raw_index[0]["close"])
    dump("SH000300", index_frame, ["open", "high", "low", "close", "volume", "factor"])
    (target / "instruments/all.txt").write_text("\n".join(instruments) + "\n")
    (target / "instruments/qtrade1000.txt").write_text("\n".join(instruments[:-1]) + "\n")
    if source_snapshot is not None:
        keys = [key for key in constraint_counts[0] if key != "symbol"]
        audit = signed({
            **AUTHORITY, "schema_version": "qtrade.source-constraints.v1", "policy": POLICY,
            "source_snapshot_sha256": source_snapshot.identity,
            "historical_pit_certified": False, "exact_price_limits_certified": False,
            "state": "APPLIED_DIAGNOSTIC_ADDITIONAL_RESTRICTIONS",
            "coverage_symbols": len(source_snapshot.statuses),
            "matched_input_symbols": sum(bool(r["reconciled_bars"]) for r in constraint_counts),
            "counts": {key: sum(r[key] for r in constraint_counts) for key in keys},
            "per_symbol": constraint_counts,
            "missing_state_policy": "BASE_PROXY_RETAINED_NOT_CERTIFIED_TRADABLE",
            "st_policy": "RESEARCH_BUY_EXCLUSION_NOT_EXCHANGE_PROHIBITION",
            "raw_price_tolerance_cny": "0.011", "raw_volume_tolerance": "0",
            "amount_tolerance": "max(CNY1,1ppm)",
            "historical_available_at": None,
        })
        write_json_atomic(target.parent / "execution-constraints.json", audit)
        pd.DataFrame(constraint_rows, columns=[
            "symbol", "date", "reason", "base_buy_block", "base_sell_block",
            "buy_block", "sell_block",
        ]).to_parquet(target.parent / "constraint-ledger.parquet", index=False)
    return document


def finite(value: Any) -> float | None:
    import math

    result = float(value)
    return result if math.isfinite(result) else None


def verify_constraint_masks(stage: Path, config: dict[str, Any]) -> None:
    """Reload provider masks through Qlib's actual directional limit implementation."""
    import pandas as pd
    from qlib.backtest.decision import Order
    from qlib.backtest.exchange import Exchange

    ledger = pd.read_parquet(stage / "constraint-ledger.parquet")
    # Cover each reason, including source disagreements, without selecting on returns.
    sample = ledger.groupby("reason", sort=True).head(2)
    checked = []
    if not sample.empty:
        options = dict(config["exchange"])
        for key in ("limit_threshold", "volume_threshold"):
            options[key] = tuple(options[key])
        codes = {("SH" if s.startswith("6") else "SZ") + s for s in sample["symbol"]}
        exchange = Exchange(start_time=sample["date"].min(), end_time=sample["date"].max(),
                            codes=sorted(codes), **options)
        for row in sample.to_dict("records"):
            code = ("SH" if row["symbol"].startswith("6") else "SZ") + row["symbol"]
            day = pd.Timestamp(row["date"])
            for side, direction in (("buy", Order.BUY), ("sell", Order.SELL)):
                actual = bool(exchange.check_stock_limit(code, day, day, direction))
                expected = bool(row[side + "_block"])
                if actual != expected:
                    raise ValueError("QLIB_SOURCE_CONSTRAINT_RELOAD_MISMATCH")
                checked.append({"symbol": row["symbol"], "date": row["date"],
                                "side": side, "blocked": actual, "reason": row["reason"]})
    write_json_atomic(stage / "constraint-reload-check.json", signed({
        "state": "VERIFIED" if checked else "NO_BACKFILLED_ROWS_AVAILABLE",
        "implementation": "qlib.backtest.exchange.Exchange.check_stock_limit",
        "checks": checked, "historical_pit_certified": False,
    }))


def run(
    data_root: Path, output: Path, tracking: Path, source_revision: str,
    *, config_override: dict[str, Any] | None = None, pit_evidence_root: Path | None = None,
    restore_proof: bool = False,
) -> dict[str, Any]:
    import importlib.metadata
    import random

    import numpy as np
    import pandas as pd
    import qlib
    from qlib.contrib.evaluate import backtest_daily
    from qlib.data import D
    from qlib.utils import init_instance_by_config
    from qlib.workflow import R

    config = config_override or json.loads(
        (REPO / "configs/qlib_daily_baseline.json").read_text()
    )
    if re.fullmatch(r"[a-f0-9]{40}", source_revision) is None:
        raise ValueError("FULL_SOURCE_REVISION_REQUIRED")
    output.mkdir(parents=True, exist_ok=True)
    source_snapshot = capture_source(pit_evidence_root)
    if config.get("workflow_spec", {}).get("source_snapshot_sha256") not in (
        None, source_snapshot.identity,
    ):
        raise ValueError("WORKFLOW_SOURCE_SNAPSHOT_MISMATCH")
    restore_request = {"restore_contract": "FITTED_DATASETH_RMDLOADER_V1"} if restore_proof else {}
    request = signed(
        {
            "config": config,
            "source_revision": source_revision,
            "data_manifest_sha256": checksum(data_root / "dataset.json"),
            "source_snapshot_sha256": source_snapshot.identity,
            **restore_request,
        }
    )
    run_id = config["name"] + "-" + request["evidence_sha256"][:16]
    archive = output / "runs" / run_id
    if archive.exists():
        from qtrade.research.qlib_publication import verify_archive

        verify_archive(archive)
        archived_summary: dict[str, Any] = json.loads((archive / "summary.json").read_text())
        return archived_summary
    attempt = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    stage = output / "runs" / (run_id + "." + attempt + ".partial")
    stage.mkdir(parents=True, exist_ok=False)
    pit_coverage = source_snapshot.coverage
    source_snapshot.freeze(stage / "pit-sources")
    write_json_atomic(stage / "request.json", request)
    write_json_atomic(stage / "pit-repair-coverage.json", pit_coverage)

    def status(state: str) -> None:
        write_json_atomic(
            output / "status.json",
            {
                "state": state,
                "run_id": run_id,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )

    status("BUILDING_PROVIDER")
    apply_constraints = config.get("source_constraint_policy") == POLICY
    dataset = make_provider(
        data_root, stage / "provider",
        source_snapshot=source_snapshot if apply_constraints else None,
    )
    constraints = (json.loads((stage / "execution-constraints.json").read_text())
                   if apply_constraints else None)
    if dataset["end"] < config["segments"]["test"][1] or (
        config_override is None and dataset["end"] != config["segments"]["test"][1]
    ):
        raise ValueError("FIXED_TEST_END_DOES_NOT_MATCH_DATASET")
    calendar = (stage / "provider/calendars/day.txt").read_text().splitlines()
    for left, right in (("train", "valid"), ("valid", "test")):
        left_days = [day for day in calendar if day <= config["segments"][left][1]]
        right_days = [day for day in calendar if day >= config["segments"][right][0]]
        if not left_days or not right_days or (
            calendar.index(left_days[-1]) + 2 >= calendar.index(right_days[0])
        ):
            raise ValueError("FUTURE_LABEL_CROSSES_SEGMENT_BOUNDARY")
    instruments: str | list[str] = "qtrade1000"
    if config.get("universe_policy") == "supported_a_share":
        from qtrade.research.pit_repair import causal_intervals

        histories = {
            row["symbol"]: pd.read_parquet(
                data_root / "stocks" / row["symbol"] / "daily.parquet",
                columns=["date", "amount"],
            ).to_dict("records")
            for row in dataset["stocks"]
        }
        actual_calendar = [day for day in calendar if day <= dataset["end"]]
        intervals, universe_audit = causal_intervals(histories, actual_calendar)
        lines = [
            f"{('SH' if symbol.startswith('6') else 'SZ') + symbol}\t{start}\t{end}"
            for symbol, windows in intervals.items() for start, end in windows
        ]
        (stage / "provider/instruments/workflow_supported.txt").write_text(
            "\n".join(lines) + "\n"
        )
        instruments = [
            ("SH" if row["symbol"].startswith("6") else "SZ") + row["symbol"]
            for row in dataset["stocks"]
            if row["symbol"] in intervals
        ]
        if not instruments:
            raise ValueError("NO_SUPPORTED_INSTRUMENTS")
        write_json_atomic(stage / "universe-audit.json", universe_audit)
    tracking.mkdir(parents=True, exist_ok=True)
    qlib.init(
        provider_uri=str(stage / "provider"),
        region="cn",
        kernels=2,
        exp_manager={
            "class": "MLflowExpManager",
            "module_path": "qlib.workflow.expm",
            "kwargs": {"uri": tracking.resolve().as_uri(), "default_exp_name": config["name"]},
        },
    )
    if apply_constraints:
        verify_constraint_masks(stage, config)
    random.seed(config["seed"])
    np.random.seed(config["seed"])
    handler = {
        "class": "Alpha158",
        "module_path": "qlib.contrib.data.handler",
        "kwargs": {
            "instruments": "workflow_supported" if config.get(
                "universe_policy"
            ) == "supported_a_share" else instruments,
            "start_time": "2016-10-24",
            "end_time": dataset["end"],
            "fit_start_time": config["segments"]["train"][0],
            "fit_end_time": config["segments"]["train"][1],
            "label": [config["label"]],
            "infer_processors": config.get("infer_processors", [{"class": "ProcessInf"}]),
            "learn_processors": [
                {"class": "DropnaLabel"},
                {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}},
            ],
        },
    }
    task = {
        "model": config["model"],
        "dataset": {
            "class": "DatasetH",
            "module_path": "qlib.data.dataset",
            "kwargs": {"handler": handler, "segments": config["segments"]},
        },
    }
    write_json_atomic(stage / "run-spec.json", signed({
        **{k: v for k, v in request.items() if k != "evidence_sha256"}, "task": task,
    }))
    if config.get("workflow_spec"):
        write_json_atomic(stage / "workflow-spec.json", config["workflow_spec"])
    shutil.copyfile(data_root / "dataset.json", stage / "data-manifest.json")
    for relative in (
        "configs/qlib_daily_baseline.json",
        "scripts/run_qlib_baseline.py",
        "scripts/prepare_qlib_data.py",
        "src/qtrade/research/qlib_adapter.py",
        "src/qtrade/research/workflow.py",
        "src/qtrade/research/pit_repair.py",
        "src/qtrade/research/source_constraints.py",
        *(["scripts/qlib_restore_bundle.py"] if restore_proof else []),
    ):
        target = stage / "source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / relative, target)
    (stage / "pip-freeze.txt").write_text(
        subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True)
    )
    status("COMPUTING_ALPHA158")
    data = init_instance_by_config(task["dataset"])
    features = data.prepare("train", col_set="feature")
    feature_names = list(features.columns)
    if len(feature_names) != 158 or not np.isfinite(features.to_numpy()).any():
        raise ValueError("ALPHA158_FEATURE_CONTRACT_FAILED")
    split_rows = {key: len(data.prepare(key, col_set="label")) for key in config["segments"]}
    del features
    model = init_instance_by_config(config["model"])
    status("TRAINING_MODEL" if config_override else "TRAINING_LIGHTGBM")
    with R.start(experiment_name=config["name"], recorder_name=run_id):
        R.log_params(
            **{
                "run_id": run_id,
                "source_revision": source_revision,
                "seed": config["seed"],
                "data_manifest_sha256": request["data_manifest_sha256"],
            }
        )
        model.fit(data, **config["fit"])
        prediction = model.predict(data, segment="test").rename("score")
        labels = data.prepare("test", col_set="label", data_key="raw").iloc[:, 0].rename("label")
        paired = pd.concat([prediction, labels], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
        daily = []
        for day, group in paired.groupby(level="datetime"):
            if len(group) < 30 or group["score"].nunique() < 2 or group["label"].nunique() < 2:
                continue
            daily.append(
                {
                    "date": str(day.date()),
                    "ic": group["score"].corr(group["label"]),
                    "rank_ic": group["score"].corr(group["label"], method="spearman"),
                    "stocks": len(group),
                }
            )
        signals = pd.DataFrame(daily)
        if signals.empty:
            raise ValueError("NO_VALID_OUT_OF_SAMPLE_SIGNAL_DAYS")
        status("BACKTESTING")
        strategy = {
            "class": "TopkDropoutStrategy",
            "module_path": "qlib.contrib.strategy.signal_strategy",
            "kwargs": {**config["portfolio"], "signal": prediction},
        }
        exchange = dict(config["exchange"])
        exchange["limit_threshold"] = tuple(exchange["limit_threshold"])
        exchange["volume_threshold"] = tuple(exchange["volume_threshold"])
        report, positions = backtest_daily(
            start_time=config["segments"]["test"][0],
            end_time=config["segments"]["test"][1],
            strategy=strategy,
            account=config["account"],
            benchmark=config["benchmark"],
            exchange_kwargs=exchange,
        )
        net = (report["return"] - report["cost"]).astype(float)
        equity = (1 + net).cumprod()
        benchmark = (1 + report["bench"]).cumprod()
        drawdown = equity / equity.cummax().clip(lower=1) - 1
        metrics = {
            "ic": finite(signals["ic"].mean()),
            "rank_ic": finite(signals["rank_ic"].mean()),
            "icir": finite(signals["ic"].mean() / signals["ic"].std())
            if signals["ic"].std()
            else None,
            "rank_icir": finite(signals["rank_ic"].mean() / signals["rank_ic"].std())
            if signals["rank_ic"].std()
            else None,
            "total_return_net": finite(equity.iloc[-1] - 1),
            "annual_return_net": finite(equity.iloc[-1] ** (252 / len(net)) - 1),
            "sharpe_net": finite(net.mean() / net.std() * np.sqrt(252)) if net.std() else None,
            "max_drawdown": finite(drawdown.min()),
            "mean_daily_turnover": finite(report["turnover"].mean()),
            "benchmark_return": finite(benchmark.iloc[-1] - 1),
            "annualized_volatility": finite(net.std() * np.sqrt(252)),
        }
        prediction.to_frame().to_parquet(stage / "predictions.parquet")
        labels.to_frame().to_parquet(stage / "labels.parquet")
        signals.to_parquet(stage / "signal-diagnostics.parquet", index=False)
        report.to_parquet(stage / "portfolio-report.parquet")
        with (stage / "positions.pkl").open("wb") as handle:
            pickle.dump(positions, handle)
        with (stage / "model.pkl").open("wb") as handle:
            pickle.dump(model, handle)
        # Qlib independently reloads converted fields: a split sample catches
        # binary calendar/field conversion errors before publishing a result.
        sample = D.features(
            ["SH600036"],
            ["$close/$factor", "$vwap*$volume", "$amount"],
            start_time="2026-10-19",
            end_time=dataset["end"],
        )
        if sample.empty or not np.allclose(sample.iloc[:, 1], sample.iloc[:, 2], rtol=2e-6):
            raise ValueError("QLIB_FIELD_RELOAD_VALIDATION_FAILED")
        sample.to_parquet(stage / "provider-reload-check.parquet")
        R.log_metrics(**{k: v for k, v in metrics.items() if v is not None})
        R.save_objects(**{"task": task, "prediction": prediction, "portfolio_report": report})
        if restore_proof:
            from scripts.qlib_restore_bundle import freeze

            freeze(stage / "online-restore", dataset=data, model=model, prediction=prediction,
                   provider=stage / "provider", data_sha256=request["data_manifest_sha256"],
                   source_revision=source_revision, source_snapshot_sha256=source_snapshot.identity)
            R.save_objects(**{"params.pkl": model, "dataset": data})
        recorder_id = R.get_recorder().id
    if restore_proof:
        with (stage / "independent-restore.log").open("w") as log:
            subprocess.run([
                sys.executable, "-m", "scripts.qlib_restore_bundle", "--bundle",
                str(stage / "online-restore"), "--provider", str(stage / "provider"),
                "--output", str(stage / "independent-restore.json"),
            ], cwd=REPO, env=os.environ, stdout=log, stderr=subprocess.STDOUT,
                timeout=900, check=True)
    restore_result = json.loads((stage / "independent-restore.json").read_text()) if (
        restore_proof) else None
    curve = [
        {
            "date": str(day.date()),
            "equity": float(equity.loc[day]),
            "benchmark_equity": float(benchmark.loc[day]),
            "drawdown": float(drawdown.loc[day]),
            "turnover": float(report.loc[day, "turnover"]),
            "cost": float(report.loc[day, "cost"]),
        }
        for day in report.index
    ]
    summary = signed(
        {
            **AUTHORITY,
            "schema_version": "qtrade.qlib-run.v1",
            "run_id": run_id,
            "completed_at": datetime.now(UTC).isoformat(),
            "source_revision": source_revision,
            "data_end": dataset["end"],
            "dataset_sha256": request["data_manifest_sha256"],
            "data_rows": dataset["rows"],
            "symbols": dataset["accepted_symbols"],
            "requested_symbols": dataset["requested_symbols"],
            "excluded_symbols": dataset["failures"],
            "feature_count": 158,
            "features": feature_names,
            "model": "Alpha158 + " + (
                "LightGBM" if config["model"]["class"] == "LGBModel" else "Ridge"
            ),
            "packages": {
                k: importlib.metadata.version(k)
                for k in ("pyqlib", "lightgbm", "numpy", "pandas", "mlflow")
            },
            "segments": config["segments"],
            "split_rows": split_rows,
            "metrics": metrics,
            "signal_evaluation": {
                "start": signals["date"].iloc[0],
                "end": signals["date"].iloc[-1],
                "days": len(signals),
                "label": config["label"],
            },
            "portfolio_evaluation": {
                "start": curve[0]["date"],
                "end": curve[-1]["date"],
                "days": len(curve),
                "benchmark": config["benchmark"],
            },
            "curve": curve,
            "cost_assumptions": config["exchange"],
            "cost_description": config["cost_description"],
            "portfolio": config["portfolio"],
            "workflow_spec": config.get("workflow_spec"),
            "pit_repair_coverage": pit_coverage,
            "source_snapshot_sha256": source_snapshot.identity,
            "execution_constraints": constraints,
            "effective_universe_size": len(instruments) if isinstance(
                instruments, list
            ) else dataset["accepted_symbols"],
            "limitations": config["limitations"] + dataset["limitations"],
            "selection_bias": dataset["selection_bias"],
            "mlflow_recorder_id": recorder_id,
            **({"independent_restore": restore_result} if restore_proof else {}),
            "persisted": True,
            "persistence_kind": "FORMAL_IMMUTABLE_QLIB_RUN",
            "conclusion": "DIAGNOSTIC_NON_PIT_NOT_ELIGIBLE",
        }
    )
    write_json_atomic(stage / "summary.json", summary)
    # Hash every artifact including converted provider, not just the summary.
    files = {
        p.relative_to(stage).as_posix(): checksum(p)
        for p in sorted(stage.rglob("*"))
        if p.is_file()
    }
    write_json_atomic(stage / "checksums.json", signed({"run_id": run_id, "files": files}))
    stage.rename(archive)
    status("COMPLETE")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tracking", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--workflow-spec", type=Path)
    parser.add_argument("--pit-evidence-root", type=Path)
    parser.add_argument("--restore-proof", action="store_true")
    args = parser.parse_args()
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("live trading must remain disabled")
    try:
        override = None
        if args.workflow_spec:
            from qtrade.research.workflow import build_config

            override = build_config(
                json.loads(args.workflow_spec.read_text()),
                json.loads((REPO / "configs/qlib_daily_baseline.json").read_text()),
            )
        result = run(
            args.data_root, args.output, args.tracking, args.source_revision,
            config_override=override, pit_evidence_root=args.pit_evidence_root,
            restore_proof=args.restore_proof,
        )
        print(json.dumps({"run_id": result["run_id"], "metrics": result["metrics"]}), flush=True)
    except Exception:
        write_json_atomic(
            args.output / "status.json",
            {
                "state": "FAILED",
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        raise
