import json
from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient

from qtrade.api.app import bootstrap_context, create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = PROJECT_ROOT / "deployment" / "evidence"


def _client(report_path: Path) -> TestClient:
    context = bootstrap_context()
    context.long_five_minute_manifest_path = (
        EVIDENCE_ROOT / "long-five-minute-market-data" / "manifest.json"
    )
    context.intraday_observation_path = report_path
    return TestClient(create_app(context))


def test_intraday_observation_endpoint_verifies_signed_evidence() -> None:
    path = EVIDENCE_ROOT / "intraday-observation" / "report.json"
    payload = _client(path).get("/intraday-observation").json()
    assert payload["classification"] == (
        "FREE_HISTORICAL_SOURCE_INTRADAY_OBSERVATION_NOT_REALTIME_ELIGIBLE"
    )
    assert payload["symbol_count"] == 15
    assert payload["sector_count"] == 15
    assert len(payload["observations"]) == 15
    assert payload["source_request_window_seconds"] >= 0
    assert payload["all_symbols_same_day_coverage"] is (
        payload["same_day_symbol_count"] == payload["symbol_count"]
    )
    observed_ages = [
        row["latest_same_day_finalized_age_seconds"]
        for row in payload["observations"]
        if row["latest_same_day_finalized_age_seconds"] is not None
    ]
    assert payload["maximum_same_day_latest_age_seconds"] == (
        max(observed_ages) if observed_ages else None
    )
    assert payload["realtime_readiness"] is False
    assert payload["real_data_paper_readiness"] is False
    assert payload["external_order_transmission"] is False


def test_intraday_observation_endpoint_rejects_resigned_phase_claim(
    tmp_path: Path,
) -> None:
    source = EVIDENCE_ROOT / "intraday-observation" / "report.json"
    report = json.loads(source.read_text(encoding="utf-8"))
    report["session_phase"] = "POST_CLOSE"
    report.pop("evidence_sha256")
    canonical = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    report["evidence_sha256"] = sha256(canonical.encode("utf-8")).hexdigest()
    target = tmp_path / "report.json"
    target.write_text(json.dumps(report), encoding="utf-8")

    rejected = _client(target).get("/intraday-observation").json()
    assert rejected["classification"] == "NO_VERIFIED_INTRADAY_OBSERVATION"
    assert rejected["promotion"] == "NOT_EVALUATED"


def test_intraday_observation_endpoint_rejects_resigned_request_window(
    tmp_path: Path,
) -> None:
    source = EVIDENCE_ROOT / "intraday-observation" / "report.json"
    report = json.loads(source.read_text(encoding="utf-8"))
    report["source_request_window_seconds"] += 1
    report.pop("evidence_sha256")
    canonical = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    report["evidence_sha256"] = sha256(canonical.encode("utf-8")).hexdigest()
    target = tmp_path / "report.json"
    target.write_text(json.dumps(report), encoding="utf-8")

    rejected = _client(target).get("/intraday-observation").json()
    assert rejected["classification"] == "NO_VERIFIED_INTRADAY_OBSERVATION"
    assert rejected["promotion"] == "NOT_EVALUATED"
