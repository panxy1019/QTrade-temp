from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path

from qtrade.data.models import Bar, DataManifest, QualityStatus
from qtrade.data.quality import BarQualityGate

_COLUMNS = (
    "symbol",
    "exchange",
    "timestamp",
    "trading_day",
    "open",
    "high",
    "low",
    "close",
    "volume",
)


@dataclass(frozen=True, slots=True)
class CsvImportResult:
    bars: tuple[Bar, ...]
    manifest: DataManifest
    source_checksum: str


class CsvBarImporter:
    """Strict canonical CSV ingestion; vendor-specific mapping belongs in adapters."""

    def import_file(
        self,
        path: Path,
        *,
        data_version: str,
        source: str,
        ingestion_time: datetime | None = None,
        schema_version: str = "1",
    ) -> CsvImportResult:
        target = path.resolve()
        raw = target.read_bytes()
        source_checksum = sha256(raw).hexdigest()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("bar CSV must be UTF-8 encoded") from exc
        reader = csv.DictReader(text.splitlines())
        if reader.fieldnames is None or tuple(reader.fieldnames) != _COLUMNS:
            raise ValueError(f"bar CSV columns must exactly equal {','.join(_COLUMNS)}")
        bars: list[Bar] = []
        for row_number, row in enumerate(reader, start=2):
            if None in row or any(value is None or not value.strip() for value in row.values()):
                raise ValueError(f"CSV row {row_number} has missing or extra values")
            try:
                timestamp = datetime.fromisoformat(row["timestamp"])
                if timestamp.tzinfo is None:
                    raise ValueError("timestamp must be timezone-aware")
                bars.append(
                    Bar(
                        symbol=row["symbol"],
                        exchange=row["exchange"],
                        timestamp=timestamp,
                        trading_day=datetime.strptime(row["trading_day"], "%Y-%m-%d").date(),
                        open=Decimal(row["open"]),
                        high=Decimal(row["high"]),
                        low=Decimal(row["low"]),
                        close=Decimal(row["close"]),
                        volume=Decimal(row["volume"]),
                    )
                )
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"invalid bar CSV row {row_number}: {exc}") from exc
        if not bars:
            raise ValueError("bar CSV contains no data rows")
        issues = BarQualityGate().validate(bars)
        if issues:
            summary = ",".join(f"{issue.code}@{issue.row}" for issue in issues)
            raise ValueError(f"bar CSV failed quality gate: {summary}")
        observed_at = ingestion_time or datetime.now(UTC)
        if observed_at.tzinfo is None:
            raise ValueError("ingestion_time must be timezone-aware")
        manifest = DataManifest(
            data_version=data_version,
            source=source,
            schema_version=schema_version,
            ingestion_time=observed_at,
            checksum=source_checksum,
            quality_status=QualityStatus.PENDING,
        )
        return CsvImportResult(tuple(bars), manifest, source_checksum)
