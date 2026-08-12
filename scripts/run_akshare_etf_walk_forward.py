"""Run disjoint held-out walk-forward selection on accepted AKShare ETF bars."""

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
    EqualWeightBuyHold,
    MomentumAllocation,
    MultiAssetWalkForwardRunner,
    TimeSplit,
    WalkForwardCandidate,
)


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


def _candidates(universe: tuple[str, ...]) -> tuple[WalkForwardCandidate, ...]:
    def volatility_candidate(target: str) -> WalkForwardCandidate:
        def model() -> MomentumAllocation:
            return MomentumAllocation(
                (20, 60),
                top_k=3,
                rebalance_every=20,
                volatility_window=20,
                target_annual_volatility=Decimal(target),
            )

        return WalkForwardCandidate(
            f"momentum-vol-{target}",
            model,
            (
                ("horizons", "20,60"),
                ("rebalance_every", "20"),
                ("top_k", "3"),
                ("target_annual_volatility", target),
                ("volatility_window", "20"),
            ),
        )

    return (
        WalkForwardCandidate(
            "buy-hold",
            lambda: EqualWeightBuyHold(universe, reserve=Decimal("0.05")),
            (("reserve", "0.05"),),
        ),
        WalkForwardCandidate(
            "momentum-20",
            lambda: MomentumAllocation((20,), top_k=3, rebalance_every=20),
            (("horizons", "20"), ("rebalance_every", "20"), ("top_k", "3")),
        ),
        WalkForwardCandidate(
            "momentum-multi",
            lambda: MomentumAllocation((20, 60), top_k=3, rebalance_every=20),
            (("horizons", "20,60"), ("rebalance_every", "20"), ("top_k", "3")),
        ),
        WalkForwardCandidate(
            "momentum-trend",
            lambda: MomentumAllocation(
                (20, 60), top_k=3, rebalance_every=20, trend_window=60
            ),
            (
                ("horizons", "20,60"),
                ("rebalance_every", "20"),
                ("top_k", "3"),
                ("trend_window", "60"),
            ),
        ),
        *(volatility_candidate(target) for target in ("0.10", "0.15", "0.20")),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-root", type=Path, default=Path("runtime/data/akshare"))
    parser.add_argument(
        "--data-version",
        default="akshare-sina-etf30-current-20260812-v1-raw-daily-20240101-20260812",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("runtime/research/akshare-walk-forward-v1.json")
    )
    args = parser.parse_args()

    if check_output(["git", "status", "--porcelain"], text=True).strip():
        raise RuntimeError("walk-forward execution requires a clean tracked source tree")
    evidence_path = args.dataset_root / args.data_version / "manifest.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    manifest = _manifest(evidence)
    bars = ParquetBarStore(args.dataset_root).read(manifest)
    universe = tuple(evidence["universe"]["symbols"])
    commit = check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    splits = (
        TimeSplit(
            date(2024, 1, 2), date(2024, 6, 28),
            date(2024, 7, 1), date(2024, 9, 30),
            date(2024, 10, 1), date(2024, 12, 31),
        ),
        TimeSplit(
            date(2025, 1, 1), date(2025, 6, 30),
            date(2025, 7, 1), date(2025, 9, 30),
            date(2025, 10, 1), date(2025, 12, 31),
        ),
        TimeSplit(
            date(2026, 1, 1), date(2026, 3, 31),
            date(2026, 4, 1), date(2026, 5, 29),
            date(2026, 6, 1), date(2026, 8, 12),
        ),
    )
    runner = MultiAssetWalkForwardRunner(
        _candidates(universe),
        backtester_factory=lambda: DailyMultiAssetBacktester(
            transaction_cost_rate=Decimal("0.0015"),
            slippage_rate=Decimal("0"),
        ),
    )
    result = runner.run(bars, splits)
    report = {
        "classification": "exploratory_noncommercial_current_universe_not_pit",
        "data_checksum": manifest.checksum,
        "data_version": manifest.data_version,
        "git_commit": commit,
        "result": asdict(result),
        "selection_contract": {
            "candidate_selection": "validation_sharpe_only",
            "candidate_test_access_before_selection": False,
            "fold_ranges_disjoint": True,
            "execution_timing": "close_decision_next_observed_open",
            "cost_model": "research-flat-rate-v1",
            "transaction_cost_rate": "0.0015",
        },
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": [
            "CURRENT_UNIVERSE_SURVIVORSHIP_BIAS",
            "NO_PAPER_OR_SHADOW_SESSIONS",
        ],
    }
    canonical = json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    envelope = {"sha256": sha256(canonical.encode("utf-8")).hexdigest(), "report": report}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(envelope, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
