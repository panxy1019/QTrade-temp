import json
from hashlib import sha256

from fastapi.testclient import TestClient

from qtrade.api.app import (
    ApiAuthConfig,
    _load_verified_robust_factor_search_report,
    bootstrap_context,
    create_app,
)

WINDOW_IDS = (
    "2024Q1",
    "2024Q2",
    "2024Q3",
    "2024Q4",
    "2025Q1",
    "2025Q2",
    "2025Q3",
    "2025Q4",
)
STARTS = (
    "2024-01-01",
    "2024-04-01",
    "2024-07-01",
    "2024-10-01",
    "2025-01-01",
    "2025-04-01",
    "2025-07-01",
    "2025-10-01",
)
ENDS = (
    "2024-03-31",
    "2024-06-30",
    "2024-09-30",
    "2024-12-31",
    "2025-03-31",
    "2025-06-30",
    "2025-09-30",
    "2025-12-31",
)
CRITERIA = [
    ["positive_window_ratio_at_least_0.75", True],
    ["median_window_sharpe_above_zero", True],
    ["worst_window_sharpe_at_least_minus_1", True],
    ["doubled_cost_total_return_above_zero", True],
    ["parameter_neighbor_positive_ratio_at_least_0.5", True],
    ["universe_subset_positive_ratio_at_least_two_thirds", True],
    ["bootstrap_probability_positive_at_least_0.75", True],
]


def _write_report(root):
    summaries = []
    candidate_windows = []
    sharpes = ("-0.5", "0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7")
    for candidate_index in range(36):
        candidate_id = f"candidate-{candidate_index:02d}"
        positive_count = 6 if candidate_index == 0 else 5
        returns = ("0.01",) * positive_count + ("-0.01",) * (8 - positive_count)
        candidate_windows.append(
            {
                "candidate_id": candidate_id,
                "parameters": [["lookback", str(candidate_index + 1)]],
                "windows": [
                    {
                        "candidate_id": candidate_id,
                        "window_id": window_id,
                        "start": start,
                        "end": end,
                        "total_return": returns[index],
                        "sharpe": sharpes[index],
                        "maximum_drawdown": "-0.1",
                        "turnover": "1",
                        "transaction_costs": "0.001",
                        "number_of_trades": 2,
                    }
                    for index, (window_id, start, end) in enumerate(
                        zip(WINDOW_IDS, STARTS, ENDS, strict=True)
                    )
                ],
            }
        )
        summaries.append(
            {
                "selection_rank": candidate_index + 1,
                "candidate_id": candidate_id,
                "window_count": 8,
                "positive_window_ratio": "0.75" if candidate_index == 0 else "0.625",
                "median_window_sharpe": "0.35",
                "worst_window_sharpe": "-0.5",
                "sharpe_dispersion": "1.2",
                "median_window_return": "0.01",
                "full_period_return": "0.1",
                "full_period_sharpe": "0.5",
                "full_period_turnover": "2",
                "full_period_transaction_costs": "0.002",
            }
        )
    metric = {"total_return": "0.1", "sharpe": "0.5", "maximum_drawdown": "-0.1"}
    stress = {
        "window_eligible_candidate_ids": ["candidate-00"],
        "evaluations": [
            {
                "candidate_id": "candidate-00",
                "cost_stresses": [
                    {
                        "scenario_id": scenario,
                        "cost_multiplier": multiplier,
                        "metrics": metric,
                    }
                    for scenario, multiplier in (
                        ("half_cost", "0.5"),
                        ("base_cost", "1"),
                        ("double_cost", "2"),
                        ("triple_cost", "3"),
                    )
                ],
                "parameter_neighbors": {
                    "definition": "exactly_one_frozen_parameter_differs",
                    "positive_ratio": "1",
                    "results": [
                        {"candidate_id": "candidate-01", "full_period_return": "0.1"}
                    ],
                },
                "universe_subsets": {
                    "method": "sha256_ranked_deterministic_80_percent_subsets",
                    "seed": 20260825,
                    "positive_ratio": "1",
                    "results": [
                        {"scenario_id": f"universe_80pct_{index}", "metrics": metric}
                        for index in range(1, 7)
                    ],
                },
                "gate": {"passed": True, "criteria": CRITERIA},
            }
        ],
    }
    bootstrap = {
        "evaluations": [
            {
                "candidate_id": "candidate-00",
                "method": "stationary_bootstrap",
                "replications": 1000,
                "expected_block_length": 20,
                "seed": 20260825,
                "observed_total_return": "0.1",
                "p05_total_return": "-0.1",
                "median_total_return": "0.1",
                "p95_total_return": "0.4",
                "probability_positive": "0.8",
            }
        ]
    }
    audit = {
        "classification": "POLLUTED_AUDIT_ONLY",
        "audited_candidate_id": "candidate-00",
        "robust_candidate_id": "candidate-00",
        "start": "2026-01-01",
        "end": "2026-08-21",
        "excluded_from_selection": True,
        "period_previously_viewed_by_project": True,
        "selected_metrics": {**metric, "turnover": "1", "transaction_costs": "0.001"},
        "buy_hold_metrics": {**metric, "total_return": "0.02"},
        "terminal_excess_return": "0.08",
    }
    artifacts = {
        "candidate_windows.json": candidate_windows,
        "candidate_summaries.json": summaries,
        "stress_tests.json": stress,
        "bootstrap.json": bootstrap,
        "polluted_audit_2026.json": audit,
    }
    artifact_hashes = {}
    for filename, payload in artifacts.items():
        encoded = json.dumps(payload, sort_keys=True, indent=2).encode()
        (root / filename).write_bytes(encoded)
        artifact_hashes[filename] = sha256(encoded).hexdigest()
    report = {
        "classification": "post_selection_robust_factor_search_current_universe_non_pit",
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": ["CURRENT_UNIVERSE_SURVIVORSHIP_BIAS"],
        "artifacts": artifact_hashes,
        "research_budget": {
            "frozen_candidates_run": 36,
            "validation_windows_per_candidate": 8,
            "window_eligible_candidates": 1,
            "cost_scenarios_per_eligible_candidate": 4,
            "universe_subsets_per_eligible_candidate": 6,
            "bootstrap_replications_per_eligible_candidate": 1000,
        },
        "selection": {
            "start": "2024-01-01",
            "end": "2025-12-31",
            "window_ids": list(WINDOW_IDS),
            "candidate_count": 36,
            "best_observed_candidate_id": "candidate-00",
            "window_gate_eligible_candidate_ids": ["candidate-00"],
            "robust_candidate_id": "candidate-00",
            "audited_candidate_id": "candidate-00",
            "benchmark_excluded_from_selection": True,
            "polluted_2026_audit_excluded_from_selection": True,
            "best_observed_summary": {
                key: value for key, value in summaries[0].items() if key != "selection_rank"
            },
            "window_gate": {
                "positive_window_ratio_minimum": "0.75",
                "median_window_sharpe_strictly_above": "0",
                "worst_window_sharpe_minimum": "-1",
            },
        },
        "robustness_gate": {
            "passed": True,
            "candidate_results": [
                {"candidate_id": "candidate-00", "passed": True, "criteria": CRITERIA}
            ],
            "conclusion": "ROBUST_CANDIDATE_PASSED_PREDECLARED_GATE",
        },
    }
    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    target = root / "report.json"
    target.write_text(
        json.dumps({"sha256": sha256(canonical.encode()).hexdigest(), "report": report}),
        encoding="utf-8",
    )
    return target, report


def test_robust_factor_verifier_recomputes_selection_and_rejects_tampering(tmp_path):
    target, report = _write_report(tmp_path)
    assert _load_verified_robust_factor_search_report(target) is not None
    context = bootstrap_context()
    context.robust_factor_search_report_path = target
    api = TestClient(create_app(context, ApiAuthConfig()))
    response = api.get("/factor-robustness").json()
    assert response["selection"]["robust_candidate_id"] == "candidate-00"

    report["selection"]["robust_candidate_id"] = "candidate-01"
    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    target.write_text(
        json.dumps({"sha256": sha256(canonical.encode()).hexdigest(), "report": report}),
        encoding="utf-8",
    )
    assert _load_verified_robust_factor_search_report(target) is None
    assert api.get("/factor-robustness").json()["classification"] == (
        "NO_VERIFIED_ROBUST_FACTOR_RESULT"
    )
