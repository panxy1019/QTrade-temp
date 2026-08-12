from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
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
_SYMBOL_PATTERN = re.compile(r"^[0-9]{6}$")
_VENUES = {"SSE", "SZSE"}
_CHINA_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


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
                symbol = row["symbol"]
                exchange = row["exchange"]
                if symbol != symbol.strip() or not _SYMBOL_PATTERN.fullmatch(symbol):
                    raise ValueError("symbol must be a canonical six-digit code")
                if exchange != exchange.strip() or exchange not in _VENUES:
                    raise ValueError("exchange must be SSE or SZSE")
                timestamp = datetime.fromisoformat(row["timestamp"])
                if timestamp.tzinfo is None:
                    raise ValueError("timestamp must be timezone-aware")
                trading_day = datetime.strptime(row["trading_day"], "%Y-%m-%d").date()
                if timestamp.astimezone(_CHINA_TZ).date() != trading_day:
                    raise ValueError("timestamp does not belong to trading_day in Asia/Shanghai")
                bars.append(
                    Bar(
                        symbol=symbol,
                        exchange=exchange,
                        timestamp=timestamp,
                        trading_day=trading_day,
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
