"""Run causal baseline experiments on a previously accepted AKShare dataset."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from subprocess import check_output
from typing import Any

from qtrade.data import DataManifest, ParquetBarStore, QualityStatus
from qtrade.persistence import SQLiteMetadataCatalog
from qtrade.research import (
    DailyMultiAssetBacktester,
    EqualWeightBuyHold,
    ExperimentOutput,
    ExperimentRunner,
    ExperimentRunSpec,
    MomentumAllocation,
    TimeSplit,
    evaluate_returns,
    write_experiment_artifacts,
)
from qtrade.research.multi_asset import AllocationModel


def _manifest(evidence: dict[str, Any]) -> DataManifest:
    value = evidence["manifest"]
    return DataManifest(
        value["data_version"],
        value["source"],
        value["schema_version"],
        datetime.fromisoformat(value["ingestion_time"]),
        value["checksum"],
        QualityStatus(value["quality_status"]),
    )


def _metrics(
    result: Any, test_start: date, test_end: date
) -> tuple[tuple[str, Decimal], ...]:
    indices = [
        index
        for index, session in enumerate(result.sessions)
        if test_start <= session <= test_end
    ]
    if not indices:
        raise ValueError("experiment has no observations in the declared test window")
    test_returns = [result.returns[index] for index in indices]
    test_exposure = sum(
        (result.exposure_curve[index] for index in indices), start=Decimal("0")
    ) / len(indices)
    test_turnover = sum(
        (result.turnover_curve[index] for index in indices), start=Decimal("0")
    )
    test_cost = sum((result.cost_curve[index] for index in indices), start=Decimal("0"))
    test = evaluate_returns(
        test_returns,
        exposure=test_exposure,
        turnover=test_turnover,
        transaction_costs=test_cost,
    )
    return tuple(
        sorted(
            {
                "annualized_return": test.annualized_return,
                "annualized_volatility": test.annualized_volatility,
                "calmar": test.calmar,
                "maximum_drawdown": test.maximum_drawdown,
                "sharpe": test.sharpe,
                "sortino": test.sortino,
                "total_return": test.total_return,
                "test_turnover": test_turnover,
                "test_transaction_cost": test_cost,
                "test_exposure": test_exposure,
            }.items()
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=Path("runtime/data/akshare"),
    )
    parser.add_argument(
        "--data-version",
        default="akshare-sina-etf30-current-20260812-v1-raw-daily-20240101-20260812",
    )
    parser.add_argument("--output-root", type=Path, default=Path("runtime/research/akshare"))
    parser.add_argument("--run-version", default="v2")
    args = parser.parse_args()

    status = check_output(["git", "status", "--porcelain"], text=True).strip()
    if status:
        raise RuntimeError("experiment execution requires a clean tracked source tree")

    evidence_path = args.dataset_root / args.data_version / "manifest.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    manifest = _manifest(evidence)
    all_bars = ParquetBarStore(args.dataset_root).read(manifest)
    universe = tuple(evidence["universe"]["symbols"])
    commit = check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    split = TimeSplit(
        date(2024, 1, 2),
        date(2024, 12, 31),
        date(2025, 1, 1),
        date(2025, 12, 31),
        date(2026, 1, 1),
        date(2026, 8, 12),
    )
    bars = [
        bar
        for bar in all_bars
        if split.train_start <= bar.trading_day <= split.test_end
    ]
    configurations: tuple[
        tuple[str, AllocationModel, tuple[tuple[str, str], ...], Decimal, str], ...
    ] = (
        (
            "buy-hold",
            EqualWeightBuyHold(universe, reserve=Decimal("0.05")),
            (),
            Decimal("0.0015"),
            "PRIMARY",
        ),
        (
            "momentum-60",
            MomentumAllocation((60,), top_k=3, rebalance_every=20),
            (("horizons", "60"), ("top_k", "3")),
            Decimal("0.0015"),
            "PRIMARY",
        ),
        (
            "momentum-multi",
            MomentumAllocation((20, 60, 120), top_k=3, rebalance_every=20),
            (("horizons", "20,60,120"), ("top_k", "3")),
            Decimal("0.0015"),
            "PRIMARY",
        ),
        (
            "momentum-vol",
            MomentumAllocation(
                (20, 60, 120),
                top_k=3,
                rebalance_every=20,
                volatility_window=20,
                target_annual_volatility=Decimal("0.15"),
            ),
            (("horizons", "20,60,120"), ("target_volatility", "0.15")),
            Decimal("0.0015"),
            "PRIMARY",
        ),
        (
            "momentum-trend",
            MomentumAllocation(
                (20, 60, 120),
                top_k=3,
                rebalance_every=20,
                trend_window=120,
            ),
            (("horizons", "20,60,120"), ("trend_window", "120")),
            Decimal("0.0015"),
            "PRIMARY",
        ),
        (
            "momentum-vol-low",
            MomentumAllocation(
                (20, 60, 120), top_k=3, rebalance_every=20,
                volatility_window=20, target_annual_volatility=Decimal("0.10"),
            ),
            (("target_volatility", "0.10"),), Decimal("0.0015"), "SENSITIVITY",
        ),
        (
            "momentum-vol-high",
            MomentumAllocation(
                (20, 60, 120), top_k=3, rebalance_every=20,
                volatility_window=20, target_annual_volatility=Decimal("0.20"),
            ),
            (("target_volatility", "0.20"),), Decimal("0.0015"), "SENSITIVITY",
        ),
        (
            "momentum-vol-fast",
            MomentumAllocation(
                (20, 60, 120), top_k=3, rebalance_every=10,
                volatility_window=20, target_annual_volatility=Decimal("0.15"),
            ),
            (("rebalance_every", "10"),), Decimal("0.0015"), "SENSITIVITY",
        ),
        (
            "momentum-vol-slow",
            MomentumAllocation(
                (20, 60, 120), top_k=3, rebalance_every=40,
                volatility_window=20, target_annual_volatility=Decimal("0.15"),
            ),
            (("rebalance_every", "40"),), Decimal("0.0015"), "SENSITIVITY",
        ),
        (
            "momentum-vol-cost-low",
            MomentumAllocation(
                (20, 60, 120), top_k=3, rebalance_every=20,
                volatility_window=20, target_annual_volatility=Decimal("0.15"),
            ),
            (("cost_stress", "0.00075"),), Decimal("0.00075"), "COST_STRESS",
        ),
        (
            "momentum-vol-cost-high",
            MomentumAllocation(
                (20, 60, 120), top_k=3, rebalance_every=20,
                volatility_window=20, target_annual_volatility=Decimal("0.15"),
            ),
            (("cost_stress", "0.003"),), Decimal("0.003"), "COST_STRESS",
        ),
    )
    runner = ExperimentRunner(
        args.output_root,
        catalog=SQLiteMetadataCatalog(args.dataset_root / "metadata.sqlite"),
    )
    summaries: list[dict[str, object]] = []
    for name, model, parameters, transaction_cost, scenario_type in configurations:
        experiment_id = f"akshare-etf30-{name}-20260812-{args.run_version}"
        artifact_dir = args.output_root / f"{experiment_id}-artifacts"

        def evaluator(
            spec: ExperimentRunSpec,
            model: AllocationModel = model,
            artifact_dir: Path = artifact_dir,
        ) -> ExperimentOutput:
            result = DailyMultiAssetBacktester(
                transaction_cost_rate=spec.transaction_cost,
                slippage_rate=Decimal("0"),
            ).run(bars, model)
            artifacts = write_experiment_artifacts(result, artifact_dir)
            return ExperimentOutput(
                _metrics(result, spec.split.test_start, spec.split.test_end), artifacts
            )

        spec = ExperimentRunSpec(
            experiment_id,
            experiment_id,
            commit,
            manifest.data_version,
            universe,
            ("close_momentum",),
            "daily_portfolio_close_to_close_return_with_next_open_rebalance",
            split,
            name,
            parameters,
            "research-flat-rate-v1",
            transaction_cost,
            7,
        )
        result = runner.run(
            spec,
            manifest=manifest,
            evaluator=evaluator,
            completed_at=datetime.fromisoformat("2026-08-12T15:00:00+00:00"),
        )
        summaries.append(
            {
                "experiment_id": result.experiment_id,
                "spec_hash": result.spec_hash,
                "result_hash": result.result_hash,
                "metrics": {key: str(value) for key, value in result.metrics},
                "scenario_type": scenario_type,
            }
        )
    report = {
        "classification": "exploratory_noncommercial_current_universe_not_pit",
        "data_version": manifest.data_version,
        "git_commit": commit,
        "split": asdict(split),
        "experiments": summaries,
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": [
            "CURRENT_UNIVERSE_SURVIVORSHIP_BIAS",
            "NO_WALK_FORWARD_ROBUSTNESS_EVIDENCE",
            "NO_PAPER_OR_SHADOW_SESSIONS",
        ],
    }
    target = args.output_root / "comparison.json"
    canonical = json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    envelope = {"sha256": sha256(canonical.encode("utf-8")).hexdigest(), "report": report}
    target.write_text(
        json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(envelope, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
