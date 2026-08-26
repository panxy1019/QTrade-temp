import json
from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient

from qtrade.api.app import bootstrap_context, create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = PROJECT_ROOT / "deployment" / "evidence"


def _canonical(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _client(path: Path, report_path: Path | None = None) -> TestClient:
    context = bootstrap_context()
    context.long_five_minute_manifest_path = (
        EVIDENCE_ROOT / "long-five-minute-market-data" / "manifest.json"
    )
    context.development_policy_search_path = (
        EVIDENCE_ROOT / "development-policy-search" / "report.json"
    )
    context.forward_observation_preregistration_path = path
    context.forward_observation_report_path = report_path
    return TestClient(create_app(context))


def _empty_forward_report(preregistration: dict[str, object]) -> dict[str, object]:
    warmup = preregistration["warmup_dataset"]
    development = preregistration["development_evidence"]
    unsigned: dict[str, object] = {
        "schema_version": 1,
        "report_id": "baostock-locked-forward-observation-v1",
        "classification": "LOCKED_FUTURE_COMPLETE_SESSION_OBSERVATION",
        "generated_at": "2026-08-26T11:40:00+08:00",
        "preregistration_sha256": preregistration["preregistration_sha256"],
        "policy_id": development["policy_id"],
        "policy_hash": development["policy_hash"],
        "policy": development["parameters"],
        "warmup_dataset": {
            "data_version": warmup["data_version"],
            "checksum": warmup["checksum"],
            "session_count": 368,
            "warmup_returns_enter_metrics": False,
        },
        "future_session_count": 0,
        "accepted_future_session_count": 0,
        "evaluated_future_session_count": 0,
        "minimum_complete_future_sessions": 20,
        "future_sessions": [],
        "observation_status": "INCOMPLETE",
        "all_required_gates_passed": False,
        "gates": [],
        "cost_stress": [],
        "leave_one_sector_out": [],
        "causal_contract": preregistration["causal_contract"],
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": [
            "FUTURE_TWENTY_SESSION_OBSERVATION_INCOMPLETE",
            "CURRENT_NON_POINT_IN_TIME_UNIVERSE",
            "QFQ_MAY_USE_FUTURE_ADJUSTMENT_KNOWLEDGE",
            "NO_PAPER_OR_SHADOW_SESSION_EVIDENCE",
        ],
        "realtime_readiness": False,
        "real_data_paper_readiness": False,
        "external_order_transmission": False,
    }
    return {**unsigned, "evidence_sha256": _canonical(unsigned)}


def test_forward_observation_endpoint_exposes_verified_lock() -> None:
    path = EVIDENCE_ROOT / "forward-observation" / "preregistration.json"
    payload = _client(path).get("/forward-observation").json()

    assert payload["classification"] == "LOCKED_FORWARD_OBSERVATION_PREREGISTRATION"
    assert payload["policy_id"] == "dev-floor-098-slow12x48-v1"
    assert payload["future_session_count"] == 0
    assert payload["minimum_complete_future_sessions"] == 20
    assert payload["required_symbol_count"] == 75
    assert payload["required_sector_count"] == 15
    assert payload["required_bars_per_symbol_session"] == 48
    assert payload["observation_status"] == "INCOMPLETE"
    assert payload["all_required_gates_passed"] is False
    assert [row["passed"] for row in payload["gates"]] == [None] * 5
    assert payload["promotion"] == "NOT_ELIGIBLE"
    assert payload["realtime_readiness"] is False
    assert payload["real_data_paper_readiness"] is False
    assert payload["external_order_transmission"] is False


def test_forward_observation_endpoint_rejects_resigned_weaker_completeness(
    tmp_path: Path,
) -> None:
    source = EVIDENCE_ROOT / "forward-observation" / "preregistration.json"
    report = json.loads(source.read_text(encoding="utf-8"))
    report["collection_lock"]["required_symbol_count"] = 15
    report.pop("preregistration_sha256")
    report["preregistration_sha256"] = _canonical(report)
    target = tmp_path / "preregistration.json"
    target.write_text(json.dumps(report), encoding="utf-8")

    rejected = _client(target).get("/forward-observation").json()
    assert rejected["classification"] == "NO_VERIFIED_FORWARD_OBSERVATION_LOCK"
    assert rejected["observation_status"] == "UNAVAILABLE"
    assert rejected["promotion"] == "NOT_EVALUATED"


def test_forward_observation_endpoint_verifies_empty_evaluator_report(
    tmp_path: Path,
) -> None:
    preregistration_path = (
        EVIDENCE_ROOT / "forward-observation" / "preregistration.json"
    )
    preregistration = json.loads(preregistration_path.read_text(encoding="utf-8"))
    report = _empty_forward_report(preregistration)
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    payload = _client(preregistration_path, report_path).get(
        "/forward-observation"
    ).json()
    assert payload["classification"] == (
        "LOCKED_FUTURE_COMPLETE_SESSION_OBSERVATION"
    )
    assert payload["accepted_future_session_count"] == 0
    assert payload["evaluated_future_session_count"] == 0
    assert payload["observation_status"] == "INCOMPLETE"
    assert payload["first_eligible_trading_day"] == "2026-08-26"
    assert payload["created_at"] == preregistration["created_at"]

    report["accepted_future_session_count"] = 20
    report["future_session_count"] = 20
    report.pop("evidence_sha256")
    report["evidence_sha256"] = _canonical(report)
    report_path.write_text(json.dumps(report), encoding="utf-8")
    rejected = _client(preregistration_path, report_path).get(
        "/forward-observation"
    ).json()
    assert rejected["classification"] == "LOCKED_FORWARD_OBSERVATION_PREREGISTRATION"
