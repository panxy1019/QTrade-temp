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
    context.intraday_breadth_path = report_path
    return TestClient(create_app(context))


def _resign(report: dict[str, object]) -> None:
    report.pop("evidence_sha256", None)
    canonical = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    report["evidence_sha256"] = sha256(canonical.encode("utf-8")).hexdigest()


def test_intraday_breadth_endpoint_verifies_all_frozen_stocks() -> None:
    path = EVIDENCE_ROOT / "intraday-breadth" / "report.json"
    payload = _client(path).get("/intraday-breadth-observation").json()
    assert payload["classification"] == (
        "FREE_HISTORICAL_SOURCE_INTRADAY_BREADTH_NOT_REALTIME_ELIGIBLE"
    )
    assert payload["coverage_scope"] == "ALL_FROZEN_STOCKS_15_SECTORS_X5"
    assert payload["symbol_count"] == 75
    assert payload["sector_count"] == 15
    assert payload["symbols_per_sector"] == 5
    assert len(payload["observations"]) == 75
    assert len(payload["sector_summaries"]) == 15
    assert {row["symbol_count"] for row in payload["sector_summaries"]} == {5}
    assert all(
        "same_day_finalized_timestamps" not in row
        for row in payload["observations"]
    )
    assert payload["all_symbols_same_day_coverage"] is (
        payload["same_day_symbol_count"] == payload["symbol_count"]
    )
    assert payload["synchronized_finalized_timestamp_count"] <= min(
        row["same_day_finalized_bar_count"] for row in payload["observations"]
    )
    assert payload["realtime_readiness"] is False
    assert payload["real_data_paper_readiness"] is False
    assert payload["promotion"] == "NOT_ELIGIBLE"
    assert payload["external_order_transmission"] is False


def test_intraday_breadth_endpoint_rejects_resigned_sector_summary(
    tmp_path: Path,
) -> None:
    source = EVIDENCE_ROOT / "intraday-breadth" / "report.json"
    report = json.loads(source.read_text(encoding="utf-8"))
    report["sector_summaries"][0]["same_day_symbol_count"] -= 1
    _resign(report)
    target = tmp_path / "report.json"
    target.write_text(json.dumps(report), encoding="utf-8")

    rejected = _client(target).get("/intraday-breadth-observation").json()
    assert rejected["classification"] == (
        "NO_VERIFIED_INTRADAY_BREADTH_OBSERVATION"
    )
    assert rejected["promotion"] == "NOT_EVALUATED"


def test_intraday_breadth_endpoint_rejects_resigned_duplicate_timestamp(
    tmp_path: Path,
) -> None:
    source = EVIDENCE_ROOT / "intraday-breadth" / "report.json"
    report = json.loads(source.read_text(encoding="utf-8"))
    timestamps = report["observations"][0]["same_day_finalized_timestamps"]
    timestamps[-1] = timestamps[-2]
    _resign(report)
    target = tmp_path / "report.json"
    target.write_text(json.dumps(report), encoding="utf-8")

    rejected = _client(target).get("/intraday-breadth-observation").json()
    assert rejected["classification"] == (
        "NO_VERIFIED_INTRADAY_BREADTH_OBSERVATION"
    )
