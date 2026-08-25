import json
import shutil
from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient

from qtrade.api.app import AppContext, bootstrap_context, create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = PROJECT_ROOT / "deployment" / "evidence"


def _context(root: Path) -> AppContext:
    context = bootstrap_context()
    context.long_five_minute_manifest_path = (
        EVIDENCE_ROOT / "long-five-minute-market-data" / "manifest.json"
    )
    context.long_five_minute_research_report_path = (
        EVIDENCE_ROOT / "long-five-minute-walk-forward" / "report.json"
    )
    context.reserved_tail_preregistration_path = root / "preregistration.json"
    context.reserved_tail_diagnostic_report_path = root / "report.json"
    return context


def test_reserved_tail_endpoint_rejects_tampered_artifact(tmp_path: Path) -> None:
    source = EVIDENCE_ROOT / "reserved-tail-diagnostic"
    evidence = tmp_path / "reserved-tail-diagnostic"
    shutil.copytree(source, evidence)
    client = TestClient(create_app(_context(evidence)))

    verified = client.get("/reserved-tail-diagnostic").json()
    assert verified["promotion"] == "NOT_ELIGIBLE"
    assert verified["all_required_gates_passed"] is False
    assert sum(bool(gate["passed"]) for gate in verified["gates"]) == 2

    daily_path = evidence / "daily.json"
    daily = json.loads(daily_path.read_text(encoding="utf-8"))
    daily[0]["candidate"]["total_return"] = 1.0
    daily_path.write_text(json.dumps(daily), encoding="utf-8")

    rejected = client.get("/reserved-tail-diagnostic").json()
    assert rejected["classification"] == (
        "NO_VERIFIED_RESERVED_TAIL_DIAGNOSTIC"
    )
    assert rejected["promotion"] == "NOT_EVALUATED"
    assert rejected["curve"] == []


def test_reserved_tail_endpoint_rejects_resigned_gate_rewrite(
    tmp_path: Path,
) -> None:
    source = EVIDENCE_ROOT / "reserved-tail-diagnostic"
    evidence = tmp_path / "reserved-tail-diagnostic"
    shutil.copytree(source, evidence)
    report_path = evidence / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["gates"][0]["passed"] = True
    report.pop("evidence_sha256")
    canonical = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    report["evidence_sha256"] = sha256(canonical.encode("utf-8")).hexdigest()
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    rejected = TestClient(create_app(_context(evidence))).get(
        "/reserved-tail-diagnostic"
    ).json()
    assert rejected["promotion"] == "NOT_EVALUATED"
