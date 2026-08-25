from datetime import date, timedelta

import pytest

from scripts.run_sina_five_minute_walk_forward import (
    FIVE_MINUTE_POLICIES,
    WALK_FORWARD_PROFILES,
    _combine_execution_metrics,
    _curve_metrics,
    _walk_forward_folds,
)


def test_walk_forward_uses_four_disjoint_six_session_test_windows() -> None:
    first = date(2026, 1, 1)
    days = tuple(first + timedelta(days=offset) for offset in range(40))

    folds = _walk_forward_folds(days)

    assert len(folds) == 4
    assert [len(fold["train_days"]) for fold in folds] == [16, 22, 28, 34]
    assert [len(fold["test_days"]) for fold in folds] == [6, 6, 6, 6]
    test_days = [day for fold in folds for day in fold["test_days"]]
    assert test_days == list(days[16:])
    assert len(set(test_days)) == 24


def test_walk_forward_rejects_wrong_session_count() -> None:
    with pytest.raises(ValueError, match="at least 40"):
        _walk_forward_folds((date(2026, 1, 1),) * 39)


def test_long_walk_forward_leaves_trailing_sessions_unread() -> None:
    first = date(2025, 1, 1)
    days = tuple(first + timedelta(days=offset) for offset in range(368))
    profile = WALK_FORWARD_PROFILES["baostock-long"]

    folds = _walk_forward_folds(
        days,
        initial_train_sessions=profile.initial_train_sessions,
        test_sessions_per_fold=profile.test_sessions_per_fold,
        fold_count=profile.fold_count,
    )

    assert len(folds) == 6
    assert [len(fold["train_days"]) for fold in folds] == [120, 160, 200, 240, 280, 320]
    assert [len(fold["test_days"]) for fold in folds] == [40] * 6
    assert folds[-1]["test_days"][-1] == days[359]
    assert days[360:] == tuple(first + timedelta(days=offset) for offset in range(360, 368))


def test_stitched_curve_metrics_are_content_bound() -> None:
    metrics = _curve_metrics((1.1, 0.99, 1.2))

    assert metrics["total_return"] == pytest.approx(0.2)
    assert metrics["maximum_drawdown"] == pytest.approx(-0.1)
    assert metrics["point_count"] == 3
    assert len(FIVE_MINUTE_POLICIES) == 7
    assert len({policy_id for policy_id, _ in FIVE_MINUTE_POLICIES}) == 7


def test_execution_diagnostics_combine_from_disjoint_folds() -> None:
    combined = _combine_execution_metrics(
        [
            {
                "symbol_count": 2,
                "observation_count": 8,
                "reduced_exposure_observation_count": 2,
                "exposure_sum": 6.0,
                "turnover": 1.0,
                "transaction_cost": 0.01,
                "reason_counts": {"WEAK_TREND": 2},
            },
            {
                "symbol_count": 2,
                "observation_count": 12,
                "reduced_exposure_observation_count": 6,
                "exposure_sum": 7.0,
                "turnover": 2.0,
                "transaction_cost": 0.02,
                "reason_counts": {"WEAK_TREND": 1, "HIGH_VOLATILITY": 3},
            },
        ]
    )

    assert combined["observation_count"] == 20
    assert combined["mean_exposure"] == pytest.approx(0.65)
    assert combined["reduced_exposure_ratio"] == pytest.approx(0.4)
    assert combined["mean_turnover_per_symbol"] == pytest.approx(1.5)
    assert combined["mean_transaction_cost_per_symbol"] == pytest.approx(0.015)
    assert combined["reason_counts"] == {"HIGH_VOLATILITY": 3, "WEAK_TREND": 3}
