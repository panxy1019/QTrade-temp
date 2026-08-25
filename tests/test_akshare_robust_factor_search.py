from scripts.run_akshare_etf_robust_factor_search import (
    _candidate_grid,
    _validation_windows,
)


def test_robust_factor_grid_is_frozen_bounded_and_excludes_benchmark():
    candidates = _candidate_grid()
    identities = {candidate.candidate_id for candidate in candidates}
    assert len(candidates) == 36
    assert len(identities) == 36
    assert "buy-hold" not in identities
    assert "reversal-20-top3-20" in identities
    assert "momentum-252-skip20-top5-40" in identities
    assert "composite-top3-20-trend120" in identities


def test_robust_selection_windows_end_before_polluted_audit_period():
    windows = _validation_windows()
    assert len(windows) == 8
    assert windows[0].window_id == "2024Q1"
    assert windows[-1].window_id == "2025Q4"
    assert max(window.end for window in windows).year == 2025
