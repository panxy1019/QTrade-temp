from scripts.akshare_history_ingest import _sign_evidence


def test_history_evidence_signature_is_deterministic_and_content_bound():
    first = _sign_evidence({"manifest": {"data_version": "v1"}, "bar_count": 30})
    second = _sign_evidence({"bar_count": 30, "manifest": {"data_version": "v1"}})
    changed = _sign_evidence({"manifest": {"data_version": "v1"}, "bar_count": 31})
    assert first == second
    assert first["evidence_sha256"] != changed["evidence_sha256"]
