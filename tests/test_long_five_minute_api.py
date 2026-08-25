import shutil
from pathlib import Path

from qtrade.api.app import (
    _load_verified_five_minute_report,
    _load_verified_minute_dataset,
)
from qtrade.market_data import SINA_FIVE_MINUTE_SESSION_TIMES

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMOTION_REASONS = (
    "FIVE_MINUTE_INTERVAL_DOES_NOT_PROVE_ONE_MINUTE_READINESS",
    "LONGER_PROVIDER_CROSSCHECK_REUSES_PREVIOUSLY_OBSERVED_CALENDAR",
    "CURRENT_NON_POINT_IN_TIME_UNIVERSE",
    "QFQ_MAY_USE_FUTURE_ADJUSTMENT_KNOWLEDGE",
    "FREE_HISTORICAL_SOURCE_NOT_AUTHORIZED_REALTIME_FEED",
    "NO_PAPER_OR_SHADOW_SESSION_EVIDENCE",
)


def _load_dataset(path: Path) -> dict[str, object] | None:
    return _load_verified_minute_dataset(
        path,
        expected_classification=(
            "free_historical_qfq_5m_current_non_pit_not_eligible"
        ),
        expected_interval="5m",
        expected_session_count=368,
        expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
    )


def _load_report(
    path: Path, dataset: dict[str, object] | None
) -> dict[str, object] | None:
    return _load_verified_five_minute_report(
        path,
        dataset,
        expected_report_id=(
            "baostock-five-minute-long-expanding-walk-forward-v1"
        ),
        initial_train_sessions=120,
        test_sessions_per_fold=40,
        fold_count=6,
        expected_promotion_reasons=PROMOTION_REASONS,
    )


def test_packaged_long_five_minute_evidence_fails_closed_after_tampering(
    tmp_path: Path,
) -> None:
    dataset_root = tmp_path / "long-five-minute-market-data"
    report_root = tmp_path / "long-five-minute-walk-forward"
    shutil.copytree(
        PROJECT_ROOT / "deployment/evidence/long-five-minute-market-data",
        dataset_root,
    )
    shutil.copytree(
        PROJECT_ROOT / "deployment/evidence/long-five-minute-walk-forward",
        report_root,
    )

    dataset = _load_dataset(dataset_root / "manifest.json")
    assert dataset is not None
    assert dataset["provider"] == "BaoStock"
    assert dataset["bar_count"] == 1_324_800
    report = _load_report(report_root / "report.json", dataset)
    assert report is not None
    assert len(report["folds"]) == 6
    assert report["walk_forward"]["unused_trailing_sessions"] == 8
    assert report["aggregate"]["excess_return"] < 0

    with (report_root / "folds.json").open("ab") as stream:
        stream.write(b" ")
    assert _load_report(report_root / "report.json", dataset) is None

    with (dataset_root / "bars.parquet").open("ab") as stream:
        stream.write(b"tampered")
    assert _load_dataset(dataset_root / "manifest.json") is None
