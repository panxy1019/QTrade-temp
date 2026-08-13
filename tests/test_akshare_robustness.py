from scripts.run_akshare_etf_robustness import _model, _scenarios


def test_robustness_matrix_is_bounded_and_contains_real_perturbations():
    universe = tuple(f"{index:06d}" for index in range(30))
    scenarios = _scenarios(universe)
    assert len(scenarios) == 14
    assert {scenario.regime for scenario in scenarios} == {"ALL", "2024", "2025", "2026"}
    sizes = {
        len(scenario.universe_override)
        for scenario in scenarios
        if scenario.universe_override
    }
    assert sizes == {15}
    assert any(scenario.cost_multiplier == 2 for scenario in scenarios)


def test_robustness_parameter_model_applies_overrides():
    model = _model(
        {
            "horizons": "20",
            "top_k": "2",
            "rebalance_every": "10",
            "trend_window": "0",
            "volatility_window": "20",
            "target_annual_volatility": "0.15",
        }
    )
    assert model is not None
