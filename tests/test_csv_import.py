from datetime import UTC, datetime

import pytest

from qtrade.data import CsvBarImporter, ParquetBarStore, QualityStatus

HEADER = "symbol,exchange,timestamp,trading_day,open,high,low,close,volume\n"


def test_csv_import_produces_versioned_manifest_and_round_trips_to_parquet(tmp_path):
    source = tmp_path / "bars.csv"
    source.write_text(
        HEADER
        + "510300,SSE,2026-08-10T07:00:00+00:00,2026-08-10,10,11,9,10.5,1000\n"
        + "510300,SSE,2026-08-11T07:00:00+00:00,2026-08-11,10.5,11,10,10.8,1200\n",
        encoding="utf-8",
    )
    imported = CsvBarImporter().import_file(
        source,
        data_version="vendor-v1",
        source="licensed-vendor-export",
        ingestion_time=datetime(2026, 8, 12, tzinfo=UTC),
    )
    assert imported.manifest.quality_status is QualityStatus.PENDING
    assert imported.manifest.checksum == imported.source_checksum
    stored = ParquetBarStore(tmp_path / "datasets").write(
        list(imported.bars), imported.manifest
    )
    assert stored.quality_status is QualityStatus.PASSED
    assert ParquetBarStore(tmp_path / "datasets").read(stored) == list(imported.bars)


@pytest.mark.parametrize(
    "body,match",
    [
        ("510300,SSE,not-a-time,2026-08-10,10,11,9,10,100\n", "invalid bar CSV row"),
        (
            "510300,SSE,2026-08-10T07:00:00+00:00,2026-08-10,NaN,11,9,10,100\n",
            "NON_FINITE_VALUE",
        ),
    ],
)
def test_csv_import_fails_closed_on_malformed_or_invalid_data(tmp_path, body, match):
    source = tmp_path / "bars.csv"
    source.write_text(HEADER + body, encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        CsvBarImporter().import_file(source, data_version="bad", source="fixture")


@pytest.mark.parametrize(
    "row,match",
    [
        (
            "510300,SSE,2026-08-11T07:00:00+00:00,2026-08-10,10,11,9,10,100\n",
            "does not belong to trading_day",
        ),
        (
            " 510300,SSE,2026-08-10T07:00:00+00:00,2026-08-10,10,11,9,10,100\n",
            "canonical six-digit",
        ),
        (
            "510300,UNKNOWN,2026-08-10T07:00:00+00:00,2026-08-10,10,11,9,10,100\n",
            "SSE or SZSE",
        ),
    ],
)
def test_csv_import_rejects_identifier_or_china_trading_day_mismatch(tmp_path, row, match):
    source = tmp_path / "bars.csv"
    source.write_text(HEADER + row, encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        CsvBarImporter().import_file(source, data_version="bad", source="fixture")
