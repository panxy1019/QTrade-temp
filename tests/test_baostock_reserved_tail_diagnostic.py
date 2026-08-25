from scripts.run_baostock_reserved_tail_diagnostic import (
    DEFAULT_PREREGISTRATION,
    _gate_rows,
    _load_preregistration,
)


def test_reserved_tail_runner_requires_the_signed_lock() -> None:
    preregistration = _load_preregistration(DEFAULT_PREREGISTRATION)

    assert preregistration["preregistration_id"] == (
        "baostock-reserved-tail-high-exposure-v1"
    )


def test_reserved_tail_gates_are_frozen_and_all_must_pass() -> None:
    rows = _gate_rows(
        candidate_five={
            "base": {"maximum_drawdown": -0.05},
            "overlay": {"total_return": 0.02, "maximum_drawdown": -0.04},
            "execution": {"mean_exposure": 0.9, "mean_turnover_per_symbol": 2.0},
        },
        comparator_five={
            "overlay": {"total_return": 0.01},
            "execution": {"mean_turnover_per_symbol": 5.0},
        },
        candidate_zero={"overlay": {"total_return": 0.03}},
        sector_wins=12,
        sector_count=15,
    )

    assert len(rows) == 6
    assert all(row["passed"] is True for row in rows)
