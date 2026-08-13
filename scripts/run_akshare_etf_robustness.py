"""Run bounded post-rejection robustness diagnostics on verified AKShare ETF bars."""

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
from qtrade.research import (
    DailyMultiAssetBacktester,
    ExperimentOutput,
    ExperimentRunSpec,
    MomentumAllocation,
    RobustnessScenario,
    RobustnessSuite,
    TimeSplit,
    evaluate_backtest_window,
)


def _load_evidence(path: Path) -> tuple[dict[str, Any], DataManifest]:
    evidence = json.loads(path.read_text(encoding="utf-8"))
    digest = evidence.pop("evidence_sha256", None)
    canonical = json.dumps(
        evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    if not isinstance(digest, str) or sha256(canonical.encode()).hexdigest() != digest:
        raise ValueError("AKShare history evidence hash mismatch")
    value = evidence["manifest"]
    manifest = DataManifest(
        value["data_version"],
        value["source"],
        value["schema_version"],
        datetime.fromisoformat(value["ingestion_time"]),
        value["checksum"],
        QualityStatus(value["quality_status"]),
    )
    return evidence, manifest


def _model(parameters: dict[str, str]) -> MomentumAllocation:
    trend = int(parameters["trend_window"])
    volatility = int(parameters["volatility_window"])
    target = Decimal(parameters["target_annual_volatility"])
    return MomentumAllocation(
        tuple(int(value) for value in parameters["horizons"].split(",")),
        top_k=int(parameters["top_k"]),
        rebalance_every=int(parameters["rebalance_every"]),
        trend_window=trend or None,
        volatility_window=volatility or None,
        target_annual_volatility=target if volatility else None,
    )


def _scenarios(universe: tuple[str, ...]) -> tuple[RobustnessScenario, ...]:
    return (
        RobustnessScenario("base"),
        RobustnessScenario("cost-half", cost_multiplier=Decimal("0.5")),
        RobustnessScenario("cost-double", cost_multiplier=Decimal("2")),
        RobustnessScenario("horizon-fast", (("horizons", "20"),)),
        RobustnessScenario("horizon-slow", (("horizons", "60"),)),
        RobustnessScenario("rebalance-fast", (("rebalance_every", "10"),)),
        RobustnessScenario("rebalance-slow", (("rebalance_every", "40"),)),
        RobustnessScenario("no-trend", (("trend_window", "0"),)),
        RobustnessScenario(
            "volatility-scaled",
            (("volatility_window", "20"), ("target_annual_volatility", "0.15")),
        ),
        RobustnessScenario("subperiod-2024", regime="2024"),
        RobustnessScenario("subperiod-2025", regime="2025"),
        RobustnessScenario("subperiod-2026", regime="2026"),
        RobustnessScenario("universe-odd", universe_override=universe[::2]),
        RobustnessScenario("universe-even", universe_override=universe[1::2]),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-root", type=Path, default=Path("runtime/data/akshare"))
    parser.add_argument(
        "--data-version",
        default="akshare-sina-etf30-current-20260812-v1-raw-daily-20240101-20260812",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("runtime/research/akshare-robustness-v1.json")
    )
    args = parser.parse_args()
    if check_output(["git", "status", "--porcelain"], text=True).strip():
        raise RuntimeError("robustness execution requires a clean tracked source tree")

    evidence, manifest = _load_evidence(
        args.dataset_root / args.data_version / "manifest.json"
    )
    bars = ParquetBarStore(args.dataset_root).read(manifest)
    universe = tuple(evidence["universe"]["symbols"])
    commit = check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    split = TimeSplit(
        date(2024, 1, 2), date(2024, 12, 31),
        date(2025, 1, 1), date(2025, 12, 31),
        date(2026, 1, 1), date(2026, 8, 12),
    )
    spec = ExperimentRunSpec(
        "akshare-etf30-momentum-trend-robustness-v1",
        "akshare-etf-momentum-trend",
        commit,
        manifest.data_version,
        universe,
        ("close_momentum", "trend_filter"),
        "post_rejection_diagnostic_daily_return",
        split,
        "momentum-trend",
        (
            ("horizons", "20,60"),
            ("rebalance_every", "20"),
            ("target_annual_volatility", "0"),
            ("top_k", "3"),
            ("trend_window", "60"),
            ("volatility_window", "0"),
        ),
        "research-flat-rate-v1",
        Decimal("0.0015"),
        7,
    )
    regimes = {
        "ALL": (date(2024, 1, 2), date(2026, 8, 12)),
        "2024": (date(2024, 1, 2), date(2024, 12, 31)),
        "2025": (date(2025, 1, 1), date(2025, 12, 31)),
        "2026": (date(2026, 1, 1), date(2026, 8, 12)),
    }

    def evaluate(varied: ExperimentRunSpec, scenario: RobustnessScenario) -> ExperimentOutput:
        allowed = set(varied.universe)
        selected_bars = [bar for bar in bars if bar.symbol in allowed]
        result = DailyMultiAssetBacktester(
            transaction_cost_rate=varied.transaction_cost,
            slippage_rate=Decimal("0"),
        ).run(selected_bars, _model(dict(varied.parameters)))
        start, end = regimes[scenario.regime]
        metrics = evaluate_backtest_window(result, start, end)
        values = asdict(metrics)
        return ExperimentOutput(
            tuple(
                sorted(
                    (key, value)
                    for key, value in values.items()
                    if isinstance(value, Decimal)
                )
            ),
            (),
        )

    scenarios = _scenarios(universe)
    result = RobustnessSuite(scenarios).run(spec, evaluate)
    report = {
        "classification": "post_rejection_diagnostic_reuses_observations_not_new_heldout",
        "data_checksum": manifest.checksum,
        "data_version": manifest.data_version,
        "git_commit": commit,
        "hypothesis": (
            "Cross-sectional ETF momentum with a trend filter should remain directionally "
            "stable under bounded cost, parameter, subperiod and universe perturbations."
        ),
        "known_biases": [
            "CURRENT_UNIVERSE_SURVIVORSHIP_BIAS",
            "POST_REJECTION_OBSERVATIONS_REUSED_FOR_DIAGNOSTICS",
        ],
        "scenarios": [asdict(scenario) for scenario in scenarios],
        "result": asdict(result),
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": [
            "NEGATIVE_HELD_OUT_WALK_FORWARD",
            "CURRENT_UNIVERSE_SURVIVORSHIP_BIAS",
            "NO_PAPER_OR_SHADOW_SESSIONS",
        ],
    }
    canonical = json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    envelope = {"sha256": sha256(canonical.encode()).hexdigest(), "report": report}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(envelope, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
