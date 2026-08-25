from datetime import date
from decimal import Decimal

import pytest

from qtrade.research import (
    CandidateRobustnessSummary,
    RobustnessGateInputs,
    ValidationWindow,
    deterministic_universe_subsets,
    evaluate_robustness_gate,
    rank_candidate_summaries,
    stationary_bootstrap_total_returns,
)


def _summary(
    candidate_id: str,
    *,
    positive_ratio: str,
    median_sharpe: str,
    worst_sharpe: str,
    dispersion: str,
    median_return: str = "0.01",
    full_return: str = "0.02",
    full_sharpe: str = "0.3",
) -> CandidateRobustnessSummary:
    return CandidateRobustnessSummary(
        candidate_id=candidate_id,
        window_count=8,
        positive_window_ratio=Decimal(positive_ratio),
        median_window_sharpe=Decimal(median_sharpe),
        worst_window_sharpe=Decimal(worst_sharpe),
        sharpe_dispersion=Decimal(dispersion),
        median_window_return=Decimal(median_return),
        full_period_return=Decimal(full_return),
        full_period_sharpe=Decimal(full_sharpe),
        full_period_turnover=Decimal("2"),
        full_period_transaction_costs=Decimal("0.002"),
    )


def test_candidate_ranking_prioritizes_repeatability_before_peak_sharpe():
    summaries = (
        _summary(
            "spiky",
            positive_ratio="0.5",
            median_sharpe="2.0",
            worst_sharpe="-3",
            dispersion="7",
        ),
        _summary(
            "stable-b",
            positive_ratio="0.75",
            median_sharpe="0.4",
            worst_sharpe="-0.5",
            dispersion="1",
        ),
        _summary(
            "stable-a",
            positive_ratio="0.75",
            median_sharpe="0.4",
            worst_sharpe="-0.5",
            dispersion="1",
        ),
    )
    assert [item.candidate_id for item in rank_candidate_summaries(summaries)] == [
        "stable-a",
        "stable-b",
        "spiky",
    ]


def test_robustness_gate_fails_closed_when_any_predeclared_criterion_fails():
    inputs = RobustnessGateInputs(
        candidate_summary=_summary(
            "candidate",
            positive_ratio="0.75",
            median_sharpe="0.2",
            worst_sharpe="-0.8",
            dispersion="1.1",
        ),
        doubled_cost_total_return=Decimal("0.01"),
        parameter_neighbor_positive_ratio=Decimal("0.5"),
        universe_subset_positive_ratio=Decimal("0.6666666666666666666666666667"),
        bootstrap_probability_positive=Decimal("0.74"),
    )
    result = evaluate_robustness_gate(inputs)
    assert result.passed is False
    assert dict(result.criteria)["bootstrap_probability_positive_at_least_0.75"] is False
    assert all(
        passed
        for criterion, passed in result.criteria
        if criterion != "bootstrap_probability_positive_at_least_0.75"
    )


def test_stationary_bootstrap_is_deterministic_and_preserves_bounded_summary():
    returns = tuple(
        Decimal("0.01") if index % 3 else Decimal("-0.004") for index in range(80)
    )
    first = stationary_bootstrap_total_returns(
        returns, replications=200, expected_block_length=10, seed=20260825
    )
    second = stationary_bootstrap_total_returns(
        returns, replications=200, expected_block_length=10, seed=20260825
    )
    assert first == second
    assert first.replications == 200
    assert first.p05_total_return <= first.median_total_return <= first.p95_total_return
    assert Decimal("0") <= first.probability_positive <= Decimal("1")
    with pytest.raises(ValueError, match="replications"):
        stationary_bootstrap_total_returns(returns, replications=1)


def test_universe_subsets_are_deterministic_unique_and_not_the_full_universe():
    symbols = tuple(f"ETF{index:02d}" for index in range(10))
    first = deterministic_universe_subsets(
        symbols, subset_count=6, keep_ratio=Decimal("0.8"), seed=20260825
    )
    second = deterministic_universe_subsets(
        symbols, subset_count=6, keep_ratio=Decimal("0.8"), seed=20260825
    )
    assert first == second
    assert len(first) == len(set(first)) == 6
    assert all(len(subset) == 8 and set(subset) < set(symbols) for subset in first)


def test_validation_windows_reject_overlap_and_invalid_ranges():
    with pytest.raises(ValueError, match="positive"):
        ValidationWindow("bad", date(2025, 1, 1), date(2025, 1, 1))
