from scripts.run_akshare_etf_factor_search import _candidates, _factor_definitions


def test_active_factor_search_is_bounded_and_excludes_buy_hold_benchmark():
    candidates = _candidates(_factor_definitions())
    identities = {candidate.candidate_id for candidate in candidates}
    assert len(candidates) == 14
    assert "buy-hold" not in identities
    assert "reversal-5-top3-5" in identities
    assert "composite-top3-20" in identities
