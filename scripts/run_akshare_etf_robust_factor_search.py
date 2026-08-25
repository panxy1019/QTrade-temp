"""Run a frozen multi-window factor search and fail-closed robustness audit."""

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
    PriceFactorSpec,
    RankedFactorAllocation,
    RobustnessGateInputs,
    ValidationWindow,
    WalkForwardCandidate,
    deterministic_universe_subsets,
    evaluate_backtest_window,
    evaluate_candidate_grid,
    evaluate_robustness_gate,
    nearest_parameter_neighbor_ids,
    rank_candidate_summaries,
    stationary_bootstrap_total_returns,
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


def _load_parent_report(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    envelope = json.loads(raw.decode("utf-8"))
    report = envelope.get("report")
    digest = envelope.get("sha256")
    if not isinstance(report, dict) or not isinstance(digest, str):
        raise ValueError("parent factor report envelope is malformed")
    canonical = json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    if sha256(canonical.encode()).hexdigest() != digest:
        raise ValueError("parent factor report hash mismatch")
    required = {
        "momentum-120-skip-20",
        "momentum-252-skip-20",
        "reversal-5",
        "low_volatility-60",
    }
    factor_selection = report.get("factor_selection")
    passing = (
        factor_selection.get("passing_factor_ids")
        if isinstance(factor_selection, dict)
        else None
    )
    if not isinstance(passing, list) or set(passing) != required:
        raise ValueError("parent report does not bind the frozen passing-factor set")
    return report, sha256(raw).hexdigest()


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


def _candidate(
    candidate_id: str,
    factors: tuple[PriceFactorSpec, ...],
    *,
    top_k: int,
    rebalance_every: int,
    trend_window: int | None = None,
) -> WalkForwardCandidate:
    return WalkForwardCandidate(
        candidate_id,
        lambda: RankedFactorAllocation(
            tuple((factor, Decimal("1")) for factor in factors),
            top_k=top_k,
            rebalance_every=rebalance_every,
            reserve=Decimal("0.05"),
            trend_window=trend_window,
        ),
        (
            ("factors", ",".join(factor.factor_id for factor in factors)),
            ("rebalance_every", str(rebalance_every)),
            ("reserve", "0.05"),
            ("top_k", str(top_k)),
            ("trend_window", str(trend_window or 0)),
        ),
    )


def _candidate_grid() -> tuple[WalkForwardCandidate, ...]:
    candidates: list[WalkForwardCandidate] = []
    for lookback in (5, 10, 20, 40):
        for top_k in (3, 5):
            for rebalance_every in (5, 10, 20):
                candidates.append(
                    _candidate(
                        f"reversal-{lookback}-top{top_k}-{rebalance_every}",
                        (PriceFactorSpec("reversal", lookback),),
                        top_k=top_k,
                        rebalance_every=rebalance_every,
                    )
                )
    for top_k in (3, 5):
        for rebalance_every in (20, 40):
            candidates.append(
                _candidate(
                    f"momentum-252-skip20-top{top_k}-{rebalance_every}",
                    (PriceFactorSpec("momentum", 252, 20),),
                    top_k=top_k,
                    rebalance_every=rebalance_every,
                )
            )
            candidates.append(
                _candidate(
                    f"low-volatility-60-top{top_k}-{rebalance_every}",
                    (PriceFactorSpec("low_volatility", 60),),
                    top_k=top_k,
                    rebalance_every=rebalance_every,
                )
            )
    composite = (
        PriceFactorSpec("momentum", 120, 20),
        PriceFactorSpec("momentum", 252, 20),
        PriceFactorSpec("reversal", 5),
        PriceFactorSpec("low_volatility", 60),
    )
    candidates.extend(
        (
            _candidate("composite-top3-20", composite, top_k=3, rebalance_every=20),
            _candidate("composite-top5-20", composite, top_k=5, rebalance_every=20),
            _candidate("composite-top3-40", composite, top_k=3, rebalance_every=40),
            _candidate(
                "composite-top3-20-trend120",
                composite,
                top_k=3,
                rebalance_every=20,
                trend_window=120,
            ),
        )
    )
    if len(candidates) != 36 or len({item.candidate_id for item in candidates}) != 36:
        raise ValueError("frozen candidate grid must contain exactly 36 unique candidates")
    return tuple(candidates)


def _validation_windows() -> tuple[ValidationWindow, ...]:
    return (
        ValidationWindow("2024Q1", date(2024, 1, 1), date(2024, 3, 31)),
        ValidationWindow("2024Q2", date(2024, 4, 1), date(2024, 6, 30)),
        ValidationWindow("2024Q3", date(2024, 7, 1), date(2024, 9, 30)),
        ValidationWindow("2024Q4", date(2024, 10, 1), date(2024, 12, 31)),
        ValidationWindow("2025Q1", date(2025, 1, 1), date(2025, 3, 31)),
        ValidationWindow("2025Q2", date(2025, 4, 1), date(2025, 6, 30)),
        ValidationWindow("2025Q3", date(2025, 7, 1), date(2025, 9, 30)),
        ValidationWindow("2025Q4", date(2025, 10, 1), date(2025, 12, 31)),
    )


def _backtester(multiplier: Decimal = Decimal("1")) -> DailyMultiAssetBacktester:
    return DailyMultiAssetBacktester(
        transaction_cost_rate=Decimal("0.001") * multiplier,
        slippage_rate=Decimal("0.0005") * multiplier,
    )


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
        "--parent-report",
        type=Path,
        default=Path("runtime/research/akshare-factor-search-v3/report.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runtime/research/akshare-robust-factor-search-v1"),
    )
    args = parser.parse_args()

    evidence, manifest = _load_evidence(
        args.dataset_root / args.data_version / "manifest.json"
    )
    parent_report, parent_report_sha256 = _load_parent_report(args.parent_report)
    bars = ParquetBarStore(args.dataset_root).read(manifest)
    universe = tuple(evidence["universe"]["symbols"])
    candidates = _candidate_grid()
    windows = _validation_windows()
    selection_start = date(2024, 1, 1)
    selection_end = date(2025, 12, 31)

    evaluations = evaluate_candidate_grid(
        bars,
        candidates,
        windows,
        selection_start=selection_start,
        selection_end=selection_end,
        backtester_factory=_backtester,
    )
    ranked = rank_candidate_summaries(tuple(item.summary for item in evaluations))
    best_observed_summary = ranked[0]
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    evaluation_by_id = {item.candidate_id: item for item in evaluations}
    window_eligible = tuple(
        summary
        for summary in ranked
        if summary.positive_window_ratio >= Decimal("0.75")
        and summary.median_window_sharpe > 0
        and summary.worst_window_sharpe >= Decimal("-1")
    )
    subsets = deterministic_universe_subsets(
        universe, subset_count=6, keep_ratio=Decimal("0.8"), seed=20260825
    )
    stress_evaluations: list[dict[str, object]] = []
    bootstrap_evaluations: list[dict[str, object]] = []
    gate_by_id = {}
    for candidate_summary in window_eligible:
        candidate = candidate_by_id[candidate_summary.candidate_id]
        cost_stresses: list[dict[str, object]] = []
        base_result = None
        doubled_cost_return: Decimal | None = None
        for scenario_id, multiplier in (
            ("half_cost", Decimal("0.5")),
            ("base_cost", Decimal("1")),
            ("double_cost", Decimal("2")),
            ("triple_cost", Decimal("3")),
        ):
            result = _backtester(multiplier).run(
                [bar for bar in bars if bar.trading_day <= selection_end],
                candidate.model_factory(),
            )
            if multiplier == 1:
                base_result = result
            metrics = evaluate_backtest_window(result, selection_start, selection_end)
            if multiplier == 2:
                doubled_cost_return = metrics.total_return
            cost_stresses.append(
                {
                    "scenario_id": scenario_id,
                    "cost_multiplier": multiplier,
                    "metrics": asdict(metrics),
                }
            )
        if base_result is None or doubled_cost_return is None:
            raise RuntimeError("required cost-stress results were not produced")

        neighbor_ids = nearest_parameter_neighbor_ids(candidate, candidates)
        if not neighbor_ids:
            raise ValueError("window-eligible candidate has no parameter neighbors")
        neighbor_results = [
            {
                "candidate_id": candidate_id,
                "full_period_return": evaluation_by_id[
                    candidate_id
                ].summary.full_period_return,
                "full_period_sharpe": evaluation_by_id[
                    candidate_id
                ].summary.full_period_sharpe,
            }
            for candidate_id in neighbor_ids
        ]
        neighbor_positive_ratio = Decimal(
            sum(Decimal(str(item["full_period_return"])) > 0 for item in neighbor_results)
        ) / len(neighbor_results)

        universe_stresses: list[dict[str, object]] = []
        universe_positive_count = 0
        for index, subset in enumerate(subsets, start=1):
            result = _backtester().run(
                [
                    bar
                    for bar in bars
                    if bar.trading_day <= selection_end and bar.symbol in subset
                ],
                candidate.model_factory(),
            )
            metrics = evaluate_backtest_window(result, selection_start, selection_end)
            universe_positive_count += metrics.total_return > 0
            universe_stresses.append(
                {
                    "scenario_id": f"universe_80pct_{index}",
                    "symbols": subset,
                    "metrics": asdict(metrics),
                }
            )
        universe_positive_ratio = Decimal(universe_positive_count) / len(
            universe_stresses
        )

        selection_returns = tuple(
            base_result.returns[index]
            for index, session in enumerate(base_result.sessions)
            if selection_start <= session <= selection_end
        )
        bootstrap = stationary_bootstrap_total_returns(
            selection_returns,
            replications=1000,
            expected_block_length=20,
            seed=20260825,
        )
        gate = evaluate_robustness_gate(
            RobustnessGateInputs(
                candidate_summary=candidate_summary,
                doubled_cost_total_return=doubled_cost_return,
                parameter_neighbor_positive_ratio=neighbor_positive_ratio,
                universe_subset_positive_ratio=universe_positive_ratio,
                bootstrap_probability_positive=bootstrap.probability_positive,
            )
        )
        gate_by_id[candidate.candidate_id] = gate
        stress_evaluations.append(
            {
                "candidate_id": candidate.candidate_id,
                "cost_stresses": cost_stresses,
                "parameter_neighbors": {
                    "definition": "exactly_one_frozen_parameter_differs",
                    "positive_ratio": neighbor_positive_ratio,
                    "results": neighbor_results,
                },
                "universe_subsets": {
                    "method": "sha256_ranked_deterministic_80_percent_subsets",
                    "seed": 20260825,
                    "positive_ratio": universe_positive_ratio,
                    "results": universe_stresses,
                },
                "gate": asdict(gate),
            }
        )
        bootstrap_evaluations.append(
            {"candidate_id": candidate.candidate_id, **asdict(bootstrap)}
        )

    robust_summaries = tuple(
        summary for summary in window_eligible if gate_by_id[summary.candidate_id].passed
    )
    robust_candidate_summary = robust_summaries[0] if robust_summaries else None
    audited_summary = (
        robust_candidate_summary
        or (window_eligible[0] if window_eligible else best_observed_summary)
    )
    audited_candidate = candidate_by_id[audited_summary.candidate_id]

    audit_start = date(2026, 1, 1)
    audit_end = date(2026, 8, 21)
    audit_bars = [bar for bar in bars if bar.trading_day <= audit_end]
    audit_result = _backtester().run(audit_bars, audited_candidate.model_factory())
    audit_metrics = evaluate_backtest_window(audit_result, audit_start, audit_end)
    benchmark_result = _backtester().run(
        audit_bars, EqualWeightBuyHold(universe, reserve=Decimal("0.05"))
    )
    benchmark_metrics = evaluate_backtest_window(benchmark_result, audit_start, audit_end)

    args.output_dir.mkdir(parents=True, exist_ok=False)
    candidate_windows_payload = [
        {
            "candidate_id": item.candidate_id,
            "parameters": item.parameters,
            "windows": [asdict(window) for window in item.windows],
        }
        for item in evaluations
    ]
    candidate_summaries_payload = [
        {"selection_rank": rank, **asdict(summary)}
        for rank, summary in enumerate(ranked, start=1)
    ]
    stress_payload = {
        "window_eligible_candidate_ids": [
            summary.candidate_id for summary in window_eligible
        ],
        "evaluations": stress_evaluations,
    }
    audit_payload = {
        "classification": "POLLUTED_AUDIT_ONLY",
        "audited_candidate_id": audited_candidate.candidate_id,
        "robust_candidate_id": (
            robust_candidate_summary.candidate_id if robust_candidate_summary else None
        ),
        "start": audit_start,
        "end": audit_end,
        "excluded_from_selection": True,
        "period_previously_viewed_by_project": True,
        "selected_metrics": asdict(audit_metrics),
        "buy_hold_metrics": asdict(benchmark_metrics),
        "terminal_excess_return": audit_metrics.total_return - benchmark_metrics.total_return,
    }
    artifact_hashes = {
        "candidate_windows.json": _write_json(
            args.output_dir / "candidate_windows.json", candidate_windows_payload
        ),
        "candidate_summaries.json": _write_json(
            args.output_dir / "candidate_summaries.json", candidate_summaries_payload
        ),
        "stress_tests.json": _write_json(
            args.output_dir / "stress_tests.json", stress_payload
        ),
        "bootstrap.json": _write_json(
            args.output_dir / "bootstrap.json", {"evaluations": bootstrap_evaluations}
        ),
        "polluted_audit_2026.json": _write_json(
            args.output_dir / "polluted_audit_2026.json", audit_payload
        ),
    }
    parent_budget = parent_report.get("research_budget", {})
    report = {
        "classification": "post_selection_robust_factor_search_current_universe_non_pit",
        "data": {
            "data_version": manifest.data_version,
            "checksum": manifest.checksum,
            "bar_count": len(bars),
            "observed_start": min(bar.trading_day for bar in bars),
            "observed_end": max(bar.trading_day for bar in bars),
            "universe_size": len(universe),
            "point_in_time_universe": False,
        },
        "source_state": _source_state(),
        "parent_factor_search": {
            "path": str(args.parent_report),
            "file_sha256": parent_report_sha256,
            "passing_factor_ids": parent_report["factor_selection"]["passing_factor_ids"],
        },
        "research_budget": {
            "prior_factor_trials": parent_budget.get("factor_trials"),
            "prior_validation_candidates": parent_budget.get("validation_candidates_run"),
            "frozen_candidates_run": len(candidates),
            "validation_windows_per_candidate": len(windows),
            "window_eligible_candidates": len(window_eligible),
            "cost_scenarios_per_eligible_candidate": 4,
            "universe_subsets_per_eligible_candidate": len(subsets),
            "bootstrap_replications_per_eligible_candidate": 1000,
            "polluted_audit_runs": 1,
        },
        "selection": {
            "start": selection_start,
            "end": selection_end,
            "window_ids": [window.window_id for window in windows],
            "candidate_count": len(candidates),
            "best_observed_candidate_id": best_observed_summary.candidate_id,
            "window_gate_eligible_candidate_ids": [
                summary.candidate_id for summary in window_eligible
            ],
            "robust_candidate_id": (
                robust_candidate_summary.candidate_id
                if robust_candidate_summary
                else None
            ),
            "audited_candidate_id": audited_candidate.candidate_id,
            "ranking_priority": [
                "positive_window_ratio_desc",
                "median_window_sharpe_desc",
                "worst_window_sharpe_desc",
                "sharpe_dispersion_asc",
                "median_window_return_desc",
                "full_period_turnover_asc",
                "candidate_id_asc",
            ],
            "benchmark_excluded_from_selection": True,
            "polluted_2026_audit_excluded_from_selection": True,
            "best_observed_summary": asdict(best_observed_summary),
            "window_gate": {
                "positive_window_ratio_minimum": "0.75",
                "median_window_sharpe_strictly_above": "0",
                "worst_window_sharpe_minimum": "-1",
            },
        },
        "robustness_gate": {
            "passed": robust_candidate_summary is not None,
            "candidate_results": [
                {
                    "candidate_id": summary.candidate_id,
                    **asdict(gate_by_id[summary.candidate_id]),
                }
                for summary in window_eligible
            ],
            "conclusion": (
                "ROBUST_CANDIDATE_PASSED_PREDECLARED_GATE"
                if robust_candidate_summary
                else "NO_CANDIDATE_PASSED_PREDECLARED_ROBUSTNESS_GATE"
            ),
        },
        "execution_contract": {
            "timing": "close_decision_next_observed_open",
            "base_transaction_cost_rate": "0.001",
            "base_slippage_rate": "0.0005",
            "long_only": True,
        },
        "artifacts": artifact_hashes,
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": [
            *(
                []
                if robust_candidate_summary
                else ["PREDECLARED_ROBUSTNESS_GATE_FAILED"]
            ),
            "POST_SELECTION_REUSE_NOT_NEW_HELD_OUT_EVIDENCE",
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
