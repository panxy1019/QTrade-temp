import shutil
from pathlib import Path

from qtrade.api.app import _load_verified_minute_dataset, _load_verified_minute_report

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_packaged_minute_evidence_fails_closed_after_artifact_tampering(
    tmp_path: Path,
) -> None:
    dataset_root = tmp_path / "minute-market-data"
    report_root = tmp_path / "minute-overlay"
    shutil.copytree(PROJECT_ROOT / "deployment/evidence/minute-market-data", dataset_root)
    shutil.copytree(PROJECT_ROOT / "deployment/evidence/minute-overlay", report_root)

    dataset = _load_verified_minute_dataset(dataset_root / "manifest.json")
    assert dataset is not None
    assert dataset["bar_count"] == 115_430
    assert dataset["symbol_count"] == 97
    assert dataset["stock_count"] == 75
    report = _load_verified_minute_report(report_root / "report.json", dataset)
    assert report is not None
    assert report["selected"]["policy_id"] == "drawdown-tight-v1"
    assert {row["excluded_symbol_count"] for row in report["leave_one_sector_out"]} == {
        5
    }

    with (report_root / "curve.json").open("ab") as stream:
        stream.write(b" ")
    assert _load_verified_minute_report(report_root / "report.json", dataset) is None

    with (dataset_root / "bars.parquet").open("ab") as stream:
        stream.write(b"tampered")
    assert _load_verified_minute_dataset(dataset_root / "manifest.json") is None
