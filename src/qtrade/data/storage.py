from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from qtrade.data.models import Bar, DataManifest, QualityStatus
from qtrade.data.quality import BarQualityGate


class DatasetIntegrityError(ValueError):
    pass


_BAR_PARQUET_SCHEMA = pa.schema(
    [
        ("symbol", pa.string()),
        ("exchange", pa.string()),
        ("timestamp", pa.string()),
        ("trading_day", pa.date32()),
        ("open", pa.string()),
        ("high", pa.string()),
        ("low", pa.string()),
        ("close", pa.string()),
        ("volume", pa.string()),
        ("data_version", pa.string()),
    ]
)


class ParquetBarStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def write(
        self,
        bars: list[Bar],
        manifest: DataManifest,
        *,
        quality_gate: BarQualityGate | None = None,
    ) -> DataManifest:
        if manifest.quality_status is not QualityStatus.PENDING:
            raise ValueError("new dataset manifest must begin PENDING")
        if not bars:
            raise ValueError("bars cannot be empty")
        issues = (quality_gate or BarQualityGate()).validate(bars)
        if issues:
            raise DatasetIntegrityError(
                "dataset failed quality gate: " + ",".join(issue.code for issue in issues)
            )
        dataset_dir = self._root / manifest.data_version
        dataset_dir.mkdir(parents=True, exist_ok=False)
        target = dataset_dir / "bars.parquet"
        table = pa.table(
            {
                "symbol": [bar.symbol for bar in bars],
                "exchange": [bar.exchange for bar in bars],
                "timestamp": [bar.timestamp.astimezone(UTC).isoformat() for bar in bars],
                "trading_day": [bar.trading_day for bar in bars],
                "open": [str(bar.open) for bar in bars],
                "high": [str(bar.high) for bar in bars],
                "low": [str(bar.low) for bar in bars],
                "close": [str(bar.close) for bar in bars],
                "volume": [str(bar.volume) for bar in bars],
                "data_version": [manifest.data_version] * len(bars),
            }
        )
        pq.write_table(table, target, compression="zstd")
        checksum = sha256(target.read_bytes()).hexdigest()
        return replace(manifest, checksum=checksum, quality_status=QualityStatus.PASSED)

    def read(self, manifest: DataManifest, *, symbol: str | None = None) -> list[Bar]:
        if manifest.quality_status is not QualityStatus.PASSED:
            raise DatasetIntegrityError("only PASSED datasets can be queried")
        target = self._root / manifest.data_version / "bars.parquet"
        if not target.is_file():
            raise DatasetIntegrityError("dataset file is missing")
        checksum = sha256(target.read_bytes()).hexdigest()
        if checksum != manifest.checksum:
            raise DatasetIntegrityError("dataset checksum mismatch")
        table = pq.read_table(target)
        if not table.schema.equals(_BAR_PARQUET_SCHEMA, check_metadata=False):
            raise DatasetIntegrityError("dataset schema mismatch")
        rows = table.to_pylist()
        if any(row.get("data_version") != manifest.data_version for row in rows):
            raise DatasetIntegrityError("dataset row data_version mismatch")
        records = [
            Bar(
                symbol=row["symbol"],
                exchange=row["exchange"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                trading_day=(
                    row["trading_day"]
                    if isinstance(row["trading_day"], date)
                    else date.fromisoformat(row["trading_day"])
                ),
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row["volume"]),
            )
            for row in rows
        ]
        issues = BarQualityGate().validate(records)
        if issues:
            raise DatasetIntegrityError(
                "dataset failed quality gate: "
                + ",".join(issue.code for issue in issues)
            )
        records.sort(key=lambda bar: bar.timestamp)
        return records if symbol is None else [bar for bar in records if bar.symbol == symbol]
