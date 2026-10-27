"""Current chart and backtest inputs, separate from immutable research archives."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import lru_cache
from hashlib import sha256
from typing import Any
from urllib.request import Request, urlopen

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from fastapi import APIRouter, Header, HTTPException, Response

from qtrade.data import Bar, BarQualityGate
from qtrade.data.storage import _BAR_PARQUET_SCHEMA
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, freshness, signed

ROOT = "https://raw.githubusercontent.com/panxy1019/QTrade-observations/main/"
router = APIRouter(prefix="/current")


def download(name: str, limit: int) -> bytes:
    # Mutable pointers need a fresh CDN key; immutable Parquet remains cacheable.
    suffix = f"?v={int(datetime.now(UTC).timestamp()) // 60}" if name.endswith(".json") else ""
    with urlopen(
        Request(ROOT + name + suffix, headers={"User-Agent": "QTrade-Research/1"}), timeout=8
    ) as handle:
        value = bytes(handle.read(limit + 1))
    if len(value) > limit:
        raise ValueError("CURRENT_DATA_TOO_LARGE")
    return value


def validate_catalog(value: dict[str, Any]) -> dict[str, Any]:
    unsigned = {k: v for k, v in value.items() if k != "evidence_sha256"}
    if value.get("evidence_sha256") != signed(unsigned)["evidence_sha256"]:
        raise ValueError("CURRENT_CATALOG_CHECKSUM_MISMATCH")
    if value.get("schema_version") != "qtrade.current-datasets.v1" or any(
        value.get(k) != v for k, v in AUTHORITY.items()
    ):
        raise ValueError("CURRENT_CATALOG_AUTHORITY_MISMATCH")
    now = datetime.now(UTC)
    published = datetime.fromisoformat(value["published_at"])
    if published.tzinfo is None or published > now:
        raise ValueError("INVALID_CURRENT_PUBLICATION_CLOCK")
    expected = Calendar(value["calendar"]).expected(now, "daily").date()
    kinds = [("daily", 100), ("minute", 400)]
    if "intraday" in value:
        kinds.append(("intraday", 400))
    for kind, maximum in kinds:
        row = value[kind]
        symbols = row["universe"]["symbols"]
        if (
            not 1 <= len(symbols) <= maximum
            or len(set(symbols)) != len(symbols)
            or any(re.fullmatch(r"\d{6}", s) is None for s in symbols)
            or row["universe"].get("point_in_time") is not False
            or re.fullmatch(r"[a-f0-9]{64}", row["checksum"]) is None
            or not 0 < row["bar_count"] <= 250_000
            or row["quality_status"] != "PASSED"
            or row["snapshot_readiness"] is not False
            or row["real_data_paper_readiness"] is not False
            or set(row["latest_by_symbol"]) != set(symbols)
            or min(row["latest_by_symbol"].values()) != row["minimum_latest_day"]
            or date.fromisoformat(row["minimum_latest_day"]) > now.date()
        ):
            raise ValueError("INVALID_CURRENT_DATASET")
        row["expected_complete_day"] = str(expected)
        row["freshness"] = (
            "FRESH"
            if row["minimum_latest_day"] >= str(expected)
            and (now - published).total_seconds() <= 1200
            else "STALE"
        )
        if kind == "intraday":
            state = freshness(
                row["observation_report"],
                Calendar(value["calendar"]).expected(now, "minute"),
                now,
                "minute",
            )
            if state["status"] != "FRESH" or not value.get("observer_healthy"):
                row["freshness"] = "STALE"
    return {**value, "checked_at": now.isoformat(), "read_only": True}


def catalog() -> dict[str, Any]:
    try:
        return validate_catalog(json.loads(download("current.json", 500_000)))
    except Exception as error:
        raise HTTPException(503, "current research datasets unavailable") from error


@lru_cache(maxsize=4)
def parquet(checksum: str) -> bytes:
    if re.fullmatch(r"[a-f0-9]{64}", checksum) is None:
        raise ValueError("INVALID_DATASET_CHECKSUM")
    value = download("datasets/" + checksum + ".parquet", 20_000_000)
    if sha256(value).hexdigest() != checksum:
        raise ValueError("CURRENT_PARQUET_CHECKSUM_MISMATCH")
    return value


def bars(row: dict[str, Any], symbol: str | None = None) -> list[Bar]:
    file = pq.ParquetFile(pa.BufferReader(parquet(row["checksum"])))
    if (
        not file.schema_arrow.equals(_BAR_PARQUET_SCHEMA, check_metadata=False)
        or file.metadata.num_rows != row["bar_count"]
        or file.metadata.num_rows > 250_000
        or sum(file.metadata.row_group(i).total_byte_size for i in range(file.num_row_groups))
        > 128_000_000
    ):
        raise ValueError("CURRENT_PARQUET_SCHEMA_OR_SIZE_MISMATCH")
    table = pq.read_table(
        pa.BufferReader(parquet(row["checksum"])),
        filters=[("symbol", "=", symbol)] if symbol else None,
    )
    rows = table.to_pylist()
    if not rows or any(b["data_version"] != row["data_version"] for b in rows):
        raise ValueError("CURRENT_ROW_VERSION_MISMATCH")
    result = [
        Bar(
            b["symbol"],
            b["exchange"],
            datetime.fromisoformat(b["timestamp"]),
            b["trading_day"],
            *[Decimal(b[k]) for k in ("open", "high", "low", "close", "volume")],
        )
        for b in rows
    ]
    if BarQualityGate().validate(result):
        raise ValueError("CURRENT_BAR_QUALITY_FAILED")
    latest = {
        s: max(b.trading_day for b in result if b.symbol == s).isoformat()
        for s in {b.symbol for b in result}
    }
    if any(row["latest_by_symbol"].get(s) != day for s, day in latest.items()):
        raise ValueError("CURRENT_ROW_CLOCK_MISMATCH")
    return result


def resolve(kind: str, version: str | None = None) -> tuple[dict[str, Any], list[Bar]]:
    row = catalog()[kind]
    if version and row["data_version"] != version:
        raise HTTPException(409, "dataset advanced; refresh the workspace before running")
    try:
        return row, bars(row)
    except Exception as error:
        raise HTTPException(503, "current dataset integrity check failed") from error


@router.get("/datasets")
def datasets(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return catalog()


@router.get("/bars/{kind}/{symbol}")
def bar_series(
    kind: str,
    symbol: str,
    response: Response,
    trading_day: date | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    if kind not in {"daily", "minute", "intraday"}:
        raise HTTPException(404, "unknown interval")
    row = catalog().get(kind)
    if row is None:
        raise HTTPException(503, "current interval unavailable")
    if symbol not in row["universe"]["symbols"] or (version and row["data_version"] != version):
        raise HTTPException(409, "symbol or dataset version changed")
    try:
        source = bars(row, symbol)
        selected = trading_day or max(b.trading_day for b in source)
        if kind != "daily":
            source = [b for b in source if b.trading_day == selected]
        records = [
            {
                "symbol": b.symbol,
                "exchange": b.exchange,
                "timestamp": b.timestamp.isoformat(),
                "trading_day": str(b.trading_day),
                **{k: str(getattr(b, k)) for k in ("open", "high", "low", "close", "volume")},
                "data_version": row["data_version"],
                "observation_type": "HISTORICAL_DAILY_BAR"
                if kind == "daily"
                else "HISTORICAL_MINUTE_BAR",
            }
            for b in source
        ]
        response.headers["Cache-Control"] = "no-store"
        return {
            "symbol": symbol,
            "data_version": row["data_version"],
            "dataset_checksum": row["checksum"],
            "observation_type": "HISTORICAL_DAILY_BAR_SERIES"
            if kind == "daily"
            else "HISTORICAL_MINUTE_BAR_SERIES",
            "interval": row["interval"],
            "selected_trading_day": str(selected),
            "total_count": len(records),
            "truncated": False,
            "bars": records,
        }
    except Exception as error:
        raise HTTPException(503, "current chart data integrity check failed") from error


# Keep the independent minute simulator; legacy daily execution is retired.
from qtrade.api.app import (  # noqa: E402
    SingleStockFiveMinuteBacktestRequest,
    _single_stock_five_minute_policy,
    _single_stock_five_minute_preview,
    retired_legacy_daily_research,
)
from qtrade.research import run_minute_overlay_backtest  # noqa: E402


@router.post("/backtest-preview")
def preview() -> dict[str, Any]:
    retired_legacy_daily_research()


@router.post("/experiment-run-spec-draft")
def draft() -> dict[str, Any]:
    retired_legacy_daily_research()


@router.post("/experiment-run-spec-backtest")
def bound_preview() -> dict[str, Any]:
    retired_legacy_daily_research()


@router.post("/single-stock-five-minute-backtest")
def minute_preview(
    payload: SingleStockFiveMinuteBacktestRequest, x_qtrade_data_version: str = Header()
) -> dict[str, Any]:
    row = catalog()["minute"]
    if (
        row["data_version"] != x_qtrade_data_version
        or payload.symbol not in row["universe"]["symbols"]
    ):
        raise HTTPException(409, "dataset or symbol changed; refresh workspace")
    days = row["trading_days"]
    if str(payload.start) not in days or str(payload.end) not in days:
        raise HTTPException(422, "boundaries must be available complete trading days")
    first, last = days.index(str(payload.start)), days.index(str(payload.end))
    if not 2 <= last - first + 1 <= 120:
        raise HTTPException(422, "select between 2 and 120 complete sessions")
    try:
        source = [b for b in bars(row, payload.symbol) if b.trading_day <= payload.end]
        counts: dict[date, int] = {}
        for b in source:
            counts[b.trading_day] = counts.get(b.trading_day, 0) + 1
        if set(map(str, counts)) != set(days[: last + 1]) or any(n != 48 for n in counts.values()):
            raise ValueError("INCOMPLETE_MINUTE_SESSIONS")
        policy = _single_stock_five_minute_policy(payload)
        result = run_minute_overlay_backtest(
            source,
            policy_id="adhoc-current-five-minute-v1",
            policy=policy,
            cost_bps=float(payload.cost_bps),
        )
        preview = _single_stock_five_minute_preview(
            payload=payload,
            policy=policy,
            result=result,
            selected_days={date.fromisoformat(d) for d in days[first : last + 1]},
            selected_trading_days=days[first : last + 1],
            warmup_session_count=first,
            data_version=row["data_version"],
            dataset_checksum=row["checksum"],
            available_trading_days=days,
            verified_universe_symbol_count=len(row["universe"]["symbols"]),
            instrument=next(b for b in row["instruments"] if b["symbol"] == payload.symbol),
        )
        # This freshly computed preview uses a different source from the archived
        # qfq helper. Bind the correct warning into its own result hash, while
        # leaving the byte-pinned archival API module untouched.
        reasons = preview["promotion_reasons"]
        if not isinstance(reasons, list):
            raise ValueError("INVALID_PREVIEW_WARNINGS")
        preview["promotion_reasons"] = [
            "SOURCE_NATIVE_ADJUSTMENT_NOT_CERTIFIED"
            if reason == "QFQ_MAY_USE_FUTURE_ADJUSTMENT_KNOWLEDGE"
            else reason
            for reason in reasons
        ]
        unsigned = {k: v for k, v in preview.items() if k != "preview_hash"}
        preview["preview_hash"] = sha256(
            json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return preview
    except (ArithmeticError, ValueError, KeyError) as error:
        raise HTTPException(422, str(error)) from error
