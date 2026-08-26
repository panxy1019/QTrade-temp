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
    context.public_minute_audit_path = report_path
    return TestClient(create_app(context))


def test_public_minute_audit_endpoint_verifies_live_evidence() -> None:
    path = EVIDENCE_ROOT / "public-minute-audit" / "report.json"
    payload = _client(path).get("/public-minute-audit").json()
    assert payload["classification"] == (
        "PUBLIC_READ_ONLY_MINUTE_BAR_AVAILABILITY_AUDIT"
    )
    assert payload["all_valid"] is True
    assert payload["sector_count"] == 15
    assert payload["first_round"]["vercel_cache_counts"] == {"MISS": 45}
    assert payload["repeat_round"]["vercel_cache_counts"] == {"HIT": 45}


def test_public_minute_audit_endpoint_rejects_resigned_claim(
    tmp_path: Path,
) -> None:
    source = EVIDENCE_ROOT / "public-minute-audit" / "report.json"
    report = json.loads(source.read_text(encoding="utf-8"))
    report["repeat_round"]["vercel_cache_counts"] = {"HIT": 44, "MISS": 1}
    report.pop("evidence_sha256")
    canonical = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    report["evidence_sha256"] = sha256(canonical.encode("utf-8")).hexdigest()
    target = tmp_path / "report.json"
    target.write_text(json.dumps(report), encoding="utf-8")

    rejected = _client(target).get("/public-minute-audit").json()
    assert rejected["classification"] == "NO_VERIFIED_PUBLIC_MINUTE_AUDIT"
    assert rejected["all_valid"] is False
