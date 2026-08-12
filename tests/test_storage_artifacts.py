from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from qtrade.data import (
    Bar,
    DataManifest,
    DatasetIntegrityError,
    ParquetBarStore,
    QualityStatus,
)
from qtrade.research import ExperimentArtifactStore, ExperimentRecord, TimeSplit


def manifest() -> DataManifest:
    return DataManifest(
        data_version="bars-v1",
        source="test-fixture",
        schema_version="1",
        ingestion_time=datetime(2026, 8, 12, tzinfo=UTC),
        checksum="pending",
        quality_status=QualityStatus.PENDING,
    )


def test_parquet_store_requires_quality_and_checksum(tmp_path):
    store = ParquetBarStore(tmp_path)
    bar = Bar(
        "510300",
        "SSE",
        datetime(2026, 8, 12, 7, tzinfo=UTC),
        date(2026, 8, 12),
        Decimal("10"),
        Decimal("11"),
        Decimal("9"),
        Decimal("10.5"),
        Decimal("100"),
    )
    passed = store.write([bar], manifest())
    assert passed.quality_status is QualityStatus.PASSED
    assert store.read(passed) == [bar]
    target = tmp_path / "bars-v1" / "bars.parquet"
    target.write_bytes(target.read_bytes() + b"tampered")
    with pytest.raises(DatasetIntegrityError, match="checksum"):
        store.read(passed)


def test_experiment_artifacts_are_hashed_and_immutable(tmp_path):
    record = ExperimentRecord(
        experiment_id="exp-1",
        git_commit="abc123",
        data_version="bars-v1",
        universe=("510300",),
        factor_set=("trend",),
        label="next_return",
        split=TimeSplit(
            date(2020, 1, 1),
            date(2022, 12, 31),
            date(2023, 1, 1),
            date(2023, 12, 31),
            date(2024, 1, 1),
            date(2024, 12, 31),
        ),
        model="baseline",
        hyperparameters={},
        seed=7,
        transaction_cost=Decimal("0.001"),
        metrics={"return": Decimal("0.1")},
        artifacts=("report.json",),
    )
    store = ExperimentArtifactStore(tmp_path)
    target, digest = store.save(record)
    assert digest in target.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        store.save(record)
