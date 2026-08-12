from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class QualityStatus(StrEnum):
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class DataManifest:
    data_version: str
    source: str
    schema_version: str
    ingestion_time: datetime
    checksum: str
    quality_status: QualityStatus

    def __post_init__(self) -> None:
        if not all((self.data_version, self.source, self.schema_version, self.checksum)):
            raise ValueError("data provenance fields cannot be empty")
        if self.ingestion_time.tzinfo is None:
            raise ValueError("ingestion_time must be timezone-aware")


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    exchange: str
    timestamp: datetime
    trading_day: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class IndexMembership:
    index_symbol: str
    member_symbol: str
    valid_from: date
    valid_to: date | None
    known_at: datetime


@dataclass(frozen=True, slots=True)
class CorporateAction:
    symbol: str
    action_type: str
    effective_date: date
    announced_at: datetime
    value: Decimal


@dataclass(frozen=True, slots=True)
class QualityIssue:
    code: str
    message: str
    row: int | None = None
