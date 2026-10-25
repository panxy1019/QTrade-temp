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
from scripts.atomic_json import write_json_atomic

REPO = Path(__file__).resolve().parents[1]


def checksum(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def make_provider(dataset_root: Path, target: Path) -> dict[str, Any]:
    import numpy as np
    import pandas as pd

    document = json.loads((dataset_root / "dataset.json").read_text())
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
    (target / "calendars").mkdir(parents=True, exist_ok=True)
    (target / "instruments").mkdir(exist_ok=True)
    (target / "calendars/day.txt").write_text("\n".join(dates) + "\n")
    instruments = []
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
    return document


def finite(value: Any) -> float | None:
    import math

    result = float(value)
    return result if math.isfinite(result) else None


def run(data_root: Path, output: Path, tracking: Path, source_revision: str) -> dict[str, Any]:
    import importlib.metadata
    import random

    import numpy as np
    import pandas as pd
    import qlib
    from qlib.contrib.evaluate import backtest_daily
    from qlib.data import D
    from qlib.utils import init_instance_by_config
    from qlib.workflow import R

    config = json.loads((REPO / "configs/qlib_daily_baseline.json").read_text())
    if re.fullmatch(r"[a-f0-9]{40}", source_revision) is None:
        raise ValueError("FULL_SOURCE_REVISION_REQUIRED")
    output.mkdir(parents=True, exist_ok=True)
    request = signed(
        {
            "config": config,
            "source_revision": source_revision,
            "data_manifest_sha256": checksum(data_root / "dataset.json"),
        }
    )
    run_id = config["name"] + "-" + request["evidence_sha256"][:16]
    archive = output / "runs" / run_id
    if archive.exists():
        from qtrade.research.qlib_publication import verify_archive

        verify_archive(archive)
        return json.loads((archive / "summary.json").read_text())
    attempt = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    stage = output / "runs" / (run_id + "." + attempt + ".partial")
    stage.mkdir(parents=True, exist_ok=False)

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
    dataset = make_provider(data_root, stage / "provider")
    if dataset["end"] != config["segments"]["test"][1]:
        raise ValueError("FIXED_TEST_END_DOES_NOT_MATCH_DATASET")
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
    random.seed(config["seed"])
    np.random.seed(config["seed"])
    handler = {
        "class": "Alpha158",
        "module_path": "qlib.contrib.data.handler",
        "kwargs": {
            "instruments": "qtrade1000",
            "start_time": "2016-10-24",
            "end_time": dataset["end"],
            "fit_start_time": config["segments"]["train"][0],
            "fit_end_time": config["segments"]["train"][1],
            "label": [config["label"]],
            "infer_processors": [{"class": "ProcessInf"}],
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
    write_json_atomic(stage / "run-spec.json", {**request, "task": task})
    shutil.copyfile(data_root / "dataset.json", stage / "data-manifest.json")
    for relative in (
        "configs/qlib_daily_baseline.json",
        "scripts/run_qlib_baseline.py",
        "scripts/prepare_qlib_data.py",
        "src/qtrade/research/qlib_adapter.py",
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
    status("TRAINING_LIGHTGBM")
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
            end_time=dataset["end"],
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
        recorder_id = R.get_recorder().id
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
            "model": "Alpha158 + LightGBM",
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
            "limitations": config["limitations"] + dataset["limitations"],
            "selection_bias": dataset["selection_bias"],
            "mlflow_recorder_id": recorder_id,
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
    args = parser.parse_args()
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("live trading must remain disabled")
    try:
        result = run(args.data_root, args.output, args.tracking, args.source_revision)
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
