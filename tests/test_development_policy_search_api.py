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
    context.development_policy_search_path = report_path
    return TestClient(create_app(context))


def test_development_policy_search_endpoint_verifies_packaged_evidence() -> None:
    path = EVIDENCE_ROOT / "development-policy-search" / "report.json"
    payload = _client(path).get("/development-policy-search").json()

    assert payload["classification"] == (
        "RETROSPECTIVE_DEVELOPMENT_ONLY_POLICY_SEARCH_NOT_INDEPENDENT"
    )
    assert payload["candidate_count"] == 4
    assert payload["development_contract"]["session_count"] == 360
    assert payload["reserved_tail"]["entered_strategy_engine"] is False
    assert payload["observed_leader"]["policy_id"] == (
        "dev-floor-098-slow12x48-v1"
    )
    assert payload["future_observation_lock"]["minimum_complete_future_sessions"] == 20
    assert payload["promotion"] == "NOT_ELIGIBLE"
    assert payload["external_order_transmission"] is False


def test_development_policy_search_endpoint_rejects_resigned_metric_tamper(
    tmp_path: Path,
) -> None:
    source = EVIDENCE_ROOT / "development-policy-search" / "report.json"
    report = json.loads(source.read_text(encoding="utf-8"))
    report["candidates"][0]["return_delta_vs_base"] = 0.5
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

    rejected = _client(target).get("/development-policy-search").json()
    assert rejected["classification"] == "NO_VERIFIED_DEVELOPMENT_POLICY_SEARCH"
    assert rejected["promotion"] == "NOT_EVALUATED"
