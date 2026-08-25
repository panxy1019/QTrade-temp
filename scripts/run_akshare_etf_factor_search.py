"""Run a bounded price-factor search with one locked confirmatory test."""

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
    MultiAssetWalkForwardRunner,
    PriceFactorSpec,
    RankedFactorAllocation,
    TimeSplit,
    WalkForwardCandidate,
    diagnose_price_factors,
    evaluate_backtest_window,
)


def _load_evidence(path: Path) -> tuple[dict[str, Any], DataManifest]:
    signed = json.loads(path.read_text(encoding="utf-8"))
    digest = signed.pop("evidence_sha256", None)
    canonical = json.dumps(
        signed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    if not isinstance(digest, str) or sha256(canonical.encode()).hexdigest() != digest:
        raise ValueError("AKShare history evidence hash mismatch")
    value = signed["manifest"]
    manifest = DataManifest(
        value["data_version"],
        value["source"],
        value["schema_version"],
        datetime.fromisoformat(value["ingestion_time"]),
        value["checksum"],
        QualityStatus(value["quality_status"]),
    )
    return signed, manifest


def _source_state() -> dict[str, object]:
    head = check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    names = check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    ).split(b"\0")
    digest = sha256()
    for raw_name in sorted(value for value in names if value):
        path = Path(raw_name.decode("utf-8"))
        if not path.is_file():
            continue
        digest.update(raw_name)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    status = check_output(["git", "status", "--porcelain"], text=True).splitlines()
    return {
        "git_head": head,
        "source_tree_sha256": digest.hexdigest(),
        "source_tree_dirty": bool(status),
        "dirty_entry_count": len(status),
    }


def _factor_definitions() -> tuple[PriceFactorSpec, ...]:
    return (
        PriceFactorSpec("momentum", 60),
        PriceFactorSpec("momentum", 120, 20),
        PriceFactorSpec("momentum", 252, 20),
        PriceFactorSpec("reversal", 5),
        PriceFactorSpec("low_volatility", 60),
    )


def _ranked_candidate(
    candidate_id: str,
    factors: tuple[PriceFactorSpec, ...],
    *,
    top_k: int = 3,
    rebalance_every: int = 20,
    trend_window: int | None = None,
) -> WalkForwardCandidate:
    parameters = (
        ("factors", ",".join(factor.factor_id for factor in factors)),
        ("rebalance_every", str(rebalance_every)),
        ("reserve", "0.05"),
        ("top_k", str(top_k)),
        ("trend_window", str(trend_window or 0)),
    )
    return WalkForwardCandidate(
        candidate_id,
        lambda: RankedFactorAllocation(
            tuple((factor, Decimal("1")) for factor in factors),
            top_k=top_k,
            rebalance_every=rebalance_every,
            reserve=Decimal("0.05"),
            trend_window=trend_window,
        ),
        parameters,
    )


def _candidates(factors: tuple[PriceFactorSpec, ...]) -> tuple[WalkForwardCandidate, ...]:
    if not factors:
        raise ValueError("no discovery factor passed the predeclared rule")
    candidates: list[WalkForwardCandidate] = []
    candidates.extend(
        _ranked_candidate(f"single-{factor.factor_id}", (factor,)) for factor in factors
    )
    if factors:
        candidates.extend(
            (
                _ranked_candidate("composite-top3-20", factors),
                _ranked_candidate("composite-top5-20", factors, top_k=5),
                _ranked_candidate(
                    "composite-top3-40", factors, rebalance_every=40
                ),
                _ranked_candidate(
                    "composite-top3-20-trend120", factors, trend_window=120
                ),
            )
        )
    reversal_refinements = (
        ("reversal-3-top3-5", PriceFactorSpec("reversal", 3), 3, 5),
        ("reversal-5-top3-5", PriceFactorSpec("reversal", 5), 3, 5),
        ("reversal-5-top5-5", PriceFactorSpec("reversal", 5), 5, 5),
        ("reversal-10-top3-10", PriceFactorSpec("reversal", 10), 3, 10),
        ("reversal-20-top3-20", PriceFactorSpec("reversal", 20), 3, 20),
    )
    candidates.extend(
        _ranked_candidate(name, (factor,), top_k=top_k, rebalance_every=rebalance)
        for name, factor, top_k, rebalance in reversal_refinements
    )
    if len(candidates) > 15:
        raise ValueError("predeclared validation candidate budget exceeded")
    return tuple(candidates)


def _write_json(path: Path, value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2, default=str
    ).encode("utf-8")
    path.write_bytes(encoded)
    return sha256(encoded).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-root", type=Path, default=Path("runtime/data/akshare"))
    parser.add_argument(
        "--data-version",
        default="akshare-etf30-factor-research-20220906-20260821-v1",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runtime/research/akshare-factor-search-v1"),
    )
    args = parser.parse_args()

    evidence, manifest = _load_evidence(
        args.dataset_root / args.data_version / "manifest.json"
    )
    bars = ParquetBarStore(args.dataset_root).read(manifest)
    universe = tuple(evidence["universe"]["symbols"])
    definitions = _factor_definitions()
    diagnostics = diagnose_price_factors(
        bars,
        definitions,
        start=date(2023, 10, 1),
        end=date(2024, 12, 31),
        sample_every=20,
        forward_sessions=20,
        quantiles=5,
    )
    diagnostic_by_id = {item.factor_id: item for item in diagnostics}
    passing = tuple(
        factor
        for factor in definitions
        if diagnostic_by_id[factor.factor_id].metrics.rank_ic > 0
        and diagnostic_by_id[factor.factor_id].metrics.long_short_spread > 0
    )
    candidates = _candidates(passing)
    split = TimeSplit(
        date(2022, 9, 6),
        date(2024, 12, 31),
        date(2025, 1, 1),
        date(2025, 12, 31),
        date(2026, 1, 1),
        date(2026, 8, 21),
    )
    def backtester() -> DailyMultiAssetBacktester:
        return DailyMultiAssetBacktester(
            transaction_cost_rate=Decimal("0.001"),
            slippage_rate=Decimal("0.0005"),
        )
    result = MultiAssetWalkForwardRunner(
        candidates, backtester_factory=backtester
    ).run(bars, (split,))
    selected_id = result.folds[0].selected_candidate_id
    benchmark_result = backtester().run(
        [bar for bar in bars if bar.trading_day <= split.test_end],
        EqualWeightBuyHold(universe, reserve=Decimal("0.05")),
    )
    benchmark_metrics = evaluate_backtest_window(
        benchmark_result, split.test_start, split.test_end
    )

    args.output_dir.mkdir(parents=True, exist_ok=False)
    diagnostics_payload = [asdict(item) for item in diagnostics]
    validation_payload = {
        "selected_candidate_id": selected_id,
        "selection_metric": "validation_sharpe",
        "scores": result.folds[0].validation_scores,
        "candidate_count": len(candidates),
        "candidates": [
            {"candidate_id": item.candidate_id, "parameters": item.parameters}
            for item in candidates
        ],
    }
    test_payload = {
        "selected_candidate_id": selected_id,
        "selected_metrics": asdict(result.aggregate_metrics),
        "buy_hold_metrics": asdict(benchmark_metrics),
        "terminal_excess_return": (
            result.aggregate_metrics.total_return - benchmark_metrics.total_return
        ),
        "walk_forward_result_hash": result.result_hash,
    }
    artifact_hashes = {
        "factor_diagnostics.json": _write_json(
            args.output_dir / "factor_diagnostics.json", diagnostics_payload
        ),
        "validation_scores.json": _write_json(
            args.output_dir / "validation_scores.json", validation_payload
        ),
        "test_metrics.json": _write_json(
            args.output_dir / "test_metrics.json", test_payload
        ),
    }
    report = {
        "classification": "bounded_factor_search_current_universe_non_pit",
        "data": {
            "data_version": manifest.data_version,
            "checksum": manifest.checksum,
            "bar_count": len(bars),
            "observed_start": min(bar.trading_day for bar in bars),
            "observed_end": max(bar.trading_day for bar in bars),
            "universe_size": len(universe),
        },
        "source_state": _source_state(),
        "research_budget": {
            "factor_trials": len(definitions),
            "validation_candidate_cap": 15,
            "validation_candidates_run": len(candidates),
            "confirmatory_test_runs": 1,
        },
        "factor_selection": {
            "discovery_start": "2023-10-01",
            "discovery_end": "2024-12-31",
            "forward_sessions": 20,
            "sample_every": 20,
            "pass_rule": "rank_ic > 0 and long_short_spread > 0",
            "passing_factor_ids": [factor.factor_id for factor in passing],
        },
        "portfolio_selection": {
            "validation_start": str(split.validation_start),
            "validation_end": str(split.validation_end),
            "selection_metric": "validation_sharpe_only",
            "selected_candidate_id": selected_id,
            "benchmark_excluded_from_active_selection": True,
            "iterations": [
                "factor_family_and_composite_screen",
                "bounded_short_reversal_neighborhood_refinement",
            ],
        },
        "confirmatory_test": {
            "start": str(split.test_start),
            "end": str(split.test_end),
            "candidate_locked_before_access": True,
            "period_previously_viewed_by_project": True,
        },
        "execution_contract": {
            "timing": "close_decision_next_observed_open",
            "transaction_cost_rate": "0.001",
            "slippage_rate": "0.0005",
            "long_only": True,
        },
        "artifacts": artifact_hashes,
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": [
            "CURRENT_UNIVERSE_SURVIVORSHIP_BIAS",
            "CONFIRMATORY_PERIOD_PREVIOUSLY_VIEWED_BY_PROJECT",
            "SHORT_SINGLE_PROVIDER_HISTORY",
            "NO_PAPER_OR_SHADOW_SESSIONS",
        ],
    }
    canonical = json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    envelope = {"sha256": sha256(canonical.encode()).hexdigest(), "report": report}
    _write_json(args.output_dir / "report.json", envelope)
    print(json.dumps(envelope, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
