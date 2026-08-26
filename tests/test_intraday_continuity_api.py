import json
from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient

from qtrade.api.app import bootstrap_context, create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = PROJECT_ROOT / "deployment" / "evidence"


def _canonical(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _client(path: Path) -> TestClient:
    context = bootstrap_context()
    context.long_five_minute_manifest_path = (
        EVIDENCE_ROOT / "long-five-minute-market-data" / "manifest.json"
    )
    context.intraday_observation_path = (
        EVIDENCE_ROOT / "intraday-observation" / "report.json"
    )
    context.intraday_breadth_path = (
        EVIDENCE_ROOT / "intraday-breadth" / "report.json"
    )
    context.intraday_continuity_path = path
    return TestClient(create_app(context))


def _resign_checkpoint_and_report(report: dict[str, object]) -> None:
    checkpoint = report["checkpoints"][-1]
    checkpoint.pop("checkpoint_sha256")
    checkpoint["checkpoint_sha256"] = _canonical(checkpoint)
    report.pop("evidence_sha256")
    report["evidence_sha256"] = _canonical(report)


def test_intraday_continuity_endpoint_verifies_current_checkpoint_chain() -> None:
    path = EVIDENCE_ROOT / "intraday-continuity" / "report.json"
    payload = _client(path).get("/intraday-continuity").json()
    assert payload["classification"] == (
        "SIGNED_INTRADAY_SOURCE_CONTINUITY_NOT_REALTIME_ELIGIBLE"
    )
    assert payload["checkpoint_count"] == len(payload["checkpoints"])
    assert payload["checkpoint_count"] >= 1
    assert payload["all_checkpoints_monotonic"] is True
    assert payload["directional_breadth_first_sequence"] == 5
    latest = payload["checkpoints"][-1]
    assert latest["detail"]["symbol_count"] == 15
    assert latest["breadth"]["symbol_count"] == 75
    assert latest["detail"]["same_day_symbol_count"] == 15
    assert latest["breadth"]["same_day_symbol_count"] == 75
    assert latest["breadth"]["directional_breadth"]["eligible_symbol_count"] == 75
    assert payload["realtime_readiness"] is False
    assert payload["real_data_paper_readiness"] is False
    assert payload["promotion"] == "NOT_ELIGIBLE"
    assert payload["external_order_transmission"] is False


def test_intraday_continuity_rejects_resigned_false_monotonic_claim(
    tmp_path: Path,
) -> None:
    source = EVIDENCE_ROOT / "intraday-continuity" / "report.json"
    report = json.loads(source.read_text(encoding="utf-8"))
    report["checkpoints"][-1]["monotonic_vs_previous"] = False
    report["all_checkpoints_monotonic"] = False
    _resign_checkpoint_and_report(report)
    target = tmp_path / "report.json"
    target.write_text(json.dumps(report), encoding="utf-8")

    rejected = _client(target).get("/intraday-continuity").json()
    assert rejected["classification"] == "NO_VERIFIED_INTRADAY_CONTINUITY"
    assert rejected["checkpoint_count"] == 0


def test_intraday_continuity_rejects_resigned_stale_latest_binding(
    tmp_path: Path,
) -> None:
    source = EVIDENCE_ROOT / "intraday-continuity" / "report.json"
    report = json.loads(source.read_text(encoding="utf-8"))
    report["checkpoints"][-1]["detail"]["evidence_sha256"] = "0" * 64
    _resign_checkpoint_and_report(report)
    target = tmp_path / "report.json"
    target.write_text(json.dumps(report), encoding="utf-8")

    rejected = _client(target).get("/intraday-continuity").json()
    assert rejected["classification"] == "NO_VERIFIED_INTRADAY_CONTINUITY"


def test_intraday_continuity_rejects_resigned_directional_gap(tmp_path: Path) -> None:
    source = EVIDENCE_ROOT / "intraday-continuity" / "report.json"
    report = json.loads(source.read_text(encoding="utf-8"))
    report["checkpoints"][-1]["breadth"].pop("directional_breadth")
    _resign_checkpoint_and_report(report)
    target = tmp_path / "report.json"
    target.write_text(json.dumps(report), encoding="utf-8")

    rejected = _client(target).get("/intraday-continuity").json()
    assert rejected["classification"] == "NO_VERIFIED_INTRADAY_CONTINUITY"
