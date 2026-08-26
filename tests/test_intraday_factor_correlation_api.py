from pathlib import Path

from fastapi.testclient import TestClient

from qtrade.api.app import bootstrap_context, create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    PROJECT_ROOT / "deployment/evidence/long-five-minute-market-data/manifest.json"
)


def _client() -> TestClient:
    context = bootstrap_context()
    context.long_five_minute_manifest_path = MANIFEST_PATH
    return TestClient(create_app(context))


def test_intraday_factor_correlation_exposes_a_read_only_redundancy_matrix() -> None:
    response = _client().post(
        "/intraday-factor-correlation",
        json={
            "start": "2026-07-28",
            "end": "2026-08-24",
            "rules": [
                {
                    "directions": ["BULLISH", "BULLISH", "BULLISH"],
                    "combination": "ALL",
                },
                {
                    "directions": ["BULLISH", "BULLISH", "BULLISH"],
                    "combination": "ALL",
                },
                {
                    "directions": ["BULLISH", "BEARISH", "IGNORE"],
                    "combination": "ANY",
                },
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["classification"] == (
        "AD_HOC_INTRADAY_FACTOR_CORRELATION_CURRENT_NON_PIT"
    )
    assert payload["observation_count"] == 20 * 75
    assert [rule["rule_id"] for rule in payload["rules"]] == ["R01", "R02", "R03"]
    assert payload["matrix"][0]["correlations"]["R01"] == "1"
    assert payload["matrix"][0]["correlations"]["R02"] == "1"
    duplicate = next(
        pair
        for pair in payload["pairs"]
        if pair["left_rule_id"] == "R01" and pair["right_rule_id"] == "R02"
    )
    assert duplicate["redundant"] is True
    assert duplicate["correlation"] == "1"
    assert payload["redundancy_threshold"] == "0.90"
    assert payload["maximum_absolute_correlation"] == "1"
    assert payload["passed"] is False
    assert payload["promotion"] == "NOT_ELIGIBLE"
    assert payload["state_mutation"] is False
    assert payload["external_order_transmission"] is False
    assert len(payload["preview_hash"]) == 64


def test_intraday_factor_correlation_rejects_unbounded_or_empty_rules() -> None:
    client = _client()
    one_rule = client.post(
        "/intraday-factor-correlation",
        json={
            "start": "2026-07-28",
            "end": "2026-08-24",
            "rules": [
                {
                    "directions": ["BULLISH", "BULLISH", "BULLISH"],
                    "combination": "ALL",
                }
            ],
        },
    )
    assert one_rule.status_code == 422
    assert "between two and eight" in one_rule.text

    empty = client.post(
        "/intraday-factor-correlation",
        json={
            "start": "2026-07-28",
            "end": "2026-08-24",
            "rules": [
                {
                    "directions": ["IGNORE", "IGNORE", "IGNORE"],
                    "combination": "ALL",
                },
                {
                    "directions": ["BULLISH", "BULLISH", "BULLISH"],
                    "combination": "ALL",
                },
            ],
        },
    )
    assert empty.status_code == 422
    assert "at least one completed ten-minute bar" in empty.text

    too_many = client.post(
        "/intraday-factor-correlation",
        json={
            "start": "2026-07-28",
            "end": "2026-08-24",
            "rules": [
                {
                    "directions": ["BULLISH", "BEARISH", "IGNORE"],
                    "combination": "ANY",
                }
                for _ in range(9)
            ],
        },
    )
    assert too_many.status_code == 422
    assert "between two and eight" in too_many.text
