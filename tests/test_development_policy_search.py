from datetime import date, timedelta

import pytest

from scripts.run_baostock_development_policy_search import (
    DEVELOPMENT_POLICIES,
    _development_split,
    _observed_leader,
)


def test_development_search_keeps_the_reserved_tail_out_of_the_engine() -> None:
    start = date(2025, 1, 1)
    sessions = tuple(start + timedelta(days=index) for index in range(368))

    development, reserved = _development_split(sessions)

    assert development == sessions[:360]
    assert reserved == sessions[360:]
    assert set(development).isdisjoint(reserved)
    with pytest.raises(ValueError, match="exactly 368"):
        _development_split(sessions[:-1])


def test_development_search_has_a_fixed_small_policy_set() -> None:
    assert [policy_id for policy_id, _ in DEVELOPMENT_POLICIES] == [
        "dev-floor-095-slow6x24-v1",
        "dev-floor-098-slow6x24-v1",
        "dev-floor-095-slow12x48-v1",
        "dev-floor-098-slow12x48-v1",
    ]


def test_observed_leader_applies_the_disclosed_constraints() -> None:
    candidates = [
        {
            "policy_id": "low-exposure",
            "return_delta_vs_base": -0.01,
            "drawdown_improvement": 0.04,
            "execution": {"mean_exposure": 0.89, "mean_turnover_per_symbol": 20},
        },
        {
            "policy_id": "eligible-slower",
            "return_delta_vs_base": -0.03,
            "drawdown_improvement": 0.03,
            "execution": {"mean_exposure": 0.91, "mean_turnover_per_symbol": 60},
        },
        {
            "policy_id": "eligible-better-return",
            "return_delta_vs_base": -0.02,
            "drawdown_improvement": 0.02,
            "execution": {"mean_exposure": 0.92, "mean_turnover_per_symbol": 80},
        },
    ]

    assert _observed_leader(candidates) == "eligible-better-return"
