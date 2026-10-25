import json
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from qtrade.data import Bar, DataManifest, ParquetBarStore, QualityStatus
from qtrade.persistence import SQLiteMetadataCatalog
from scripts.persist_research_experiment import (
    archive_legacy, persist_experiment, register_archive, verify_archive,
)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="durable publishers require Linux fsync")


def test_existing_experiments_are_archived_without_fabricating_legacy_specs(tmp_path):
    assert archive_legacy(tmp_path) == 11
    assert archive_legacy(tmp_path) == 11
    archives = list(tmp_path.glob("*/publication.json"))
    assert len(archives) == 11
    row = json.loads(archives[0].read_text())
    assert row["persisted"] is True
    assert row["legacy_spec_hash_independently_recomputed"] is False
    (archives[0].parent / "artifacts/equity.json").write_text("[]")
    with pytest.raises(ValueError, match="CHECKSUM"):
        verify_archive(archives[0].parent)


def test_formal_experiment_persists_is_idempotent_and_rejects_conflicting_identity(tmp_path):
    dataset_root = tmp_path / "data"
    bars = []
    for day in range(12):
        session = date(2024, 1, 1) + timedelta(days=day)
        for symbol in ("510300", "510500"):
            price = Decimal("10") + Decimal(day) / 100
            bars.append(Bar(symbol, "SSE", datetime.combine(session, datetime.min.time(), UTC),
                            session, price, price, price, price, Decimal("10000")))
    manifest = ParquetBarStore(dataset_root).write(bars, DataManifest(
        "fixture", "synthetic deterministic daily", "qtrade.bar.v1",
        datetime.now(UTC), "pending", QualityStatus.PENDING,
    ))
    path = dataset_root / "fixture/manifest.json"
    path.write_text(json.dumps({
        "manifest": {"data_version": manifest.data_version, "source": manifest.source,
                     "schema_version": manifest.schema_version,
                     "ingestion_time": manifest.ingestion_time.isoformat(),
                     "checksum": manifest.checksum, "quality_status": "PASSED"},
        "observed_start": "2024-01-01", "observed_end": "2024-01-12",
        "universe": {"symbols": ["510300", "510500"], "point_in_time": False},
    }))
    request = {
        "template_id": "etf-momentum-rotation-v1", "experiment_id": "fixture-001", "seed": 7,
        "split": {"train_start": "2024-01-01", "train_end": "2024-01-03",
                  "validation_start": "2024-01-04", "validation_end": "2024-01-05",
                  "test_start": "2024-01-08", "test_end": "2024-01-12"},
        "backtest": {"start": "2024-01-08", "end": "2024-01-12", "horizons": [1],
                     "top_k": 1, "rebalance_every": 1, "trend_window": None,
                     "volatility_window": None, "target_annual_volatility": None},
    }
    arguments = (request, path, tmp_path / "experiments", tmp_path / "metadata.sqlite", "a" * 40)
    result = persist_experiment(*arguments)
    assert result["persisted"] is True
    assert result["fresh_unseen_test_evidence"] is False
    assert result["promotion"] == "NOT_ELIGIBLE"
    assert persist_experiment(*arguments) == result
    assert (tmp_path / "experiments/fixture-001/metadata-backup.sqlite").exists()
    (tmp_path / "metadata.sqlite").unlink()
    register_archive(tmp_path / "experiments/fixture-001", tmp_path / "metadata.sqlite")
    assert len(SQLiteMetadataCatalog(tmp_path / "metadata.sqlite").experiments()) == 1
    request["seed"] = 8
    with pytest.raises(ValueError, match="CONFLICT"):
        persist_experiment(*arguments)
