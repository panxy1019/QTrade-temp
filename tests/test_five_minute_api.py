import shutil
from pathlib import Path

from qtrade.api.app import (
    _load_verified_five_minute_report,
    _load_verified_minute_dataset,
)
from qtrade.market_data import SINA_FIVE_MINUTE_SESSION_TIMES

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_dataset(path: Path) -> dict[str, object] | None:
    return _load_verified_minute_dataset(
        path,
        expected_classification=(
            "noncommercial_recent_historical_5m_non_pit_not_eligible"
        ),
        expected_interval="5m",
        expected_session_count=40,
        expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
    )


def test_packaged_five_minute_evidence_fails_closed_after_artifact_tampering(
    tmp_path: Path,
) -> None:
    dataset_root = tmp_path / "five-minute-market-data"
    report_root = tmp_path / "five-minute-walk-forward"
    shutil.copytree(
        PROJECT_ROOT / "deployment/evidence/five-minute-market-data", dataset_root
    )
    shutil.copytree(
        PROJECT_ROOT / "deployment/evidence/five-minute-walk-forward", report_root
    )

    dataset = _load_dataset(dataset_root / "manifest.json")
    assert dataset is not None
    assert dataset["bar_interval"] == "5m"
    assert dataset["bar_count"] == 193_920
    assert dataset["symbol_count"] == 101
    assert dataset["stock_count"] == 75
    assert dataset["session_count"] == 40

    report = _load_verified_five_minute_report(report_root / "report.json", dataset)
    assert report is not None
    assert len(report["folds"]) == 4
    assert report["aggregate"]["positive_test_fold_count"] == 1
    assert report["aggregate"]["excess_return"] < 0
    assert len(report["leave_one_sector_out"]) == 15

    with (report_root / "folds.json").open("ab") as stream:
        stream.write(b" ")
    assert _load_verified_five_minute_report(report_root / "report.json", dataset) is None

    with (dataset_root / "bars.parquet").open("ab") as stream:
        stream.write(b"tampered")
    assert _load_dataset(dataset_root / "manifest.json") is None
