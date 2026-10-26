"""Verify production archives/collection receipts before forward research admission."""

from __future__ import annotations

import io
import json
import re
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from qtrade.market_data.akshare import SHANGHAI
from qtrade.market_data.sina_incremental import completed_klines, completed_quotes, reconcile_day
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.forward_paper import (
    ForwardPaperBook,
    ForwardTarget,
    ObservedSecurity,
    ObservedSession,
)
from qtrade.research.signal_baselines import allocation
from qtrade.research.signal_publication import verify_signal_archive
from scripts.run_qlib_prediction import verify_prediction
from scripts.unified_store import UnifiedBatchStore


def _symbol(code: str) -> str:
    if re.fullmatch(r"(?:SH|SZ)\d{6}", code) is None:
        raise ValueError("FORWARD_UNSUPPORTED_INSTRUMENT")
    # ETFs require a separate data/rule adapter; STAR remains observation-only.
    if not code.startswith(
        (
            "SH600",
            "SH601",
            "SH603",
            "SH605",
            "SH688",
            "SH689",
            "SZ000",
            "SZ001",
            "SZ002",
            "SZ003",
            "SZ300",
            "SZ301",
        )
    ):
        raise ValueError("FORWARD_STOCK_RULE_NOT_ADMITTED")
    return code[2:]


def _weights(positions: list[dict[str, Any]]) -> dict[str, Decimal]:
    if not 0 <= len(positions) <= 6000:
        raise ValueError("FORWARD_TARGET_SIZE_LIMIT")
    result = {}
    for row in positions:
        code = row["instrument"]
        _symbol(code)
        if code in result:
            raise ValueError("FORWARD_DUPLICATE_TARGET")
        result[code] = Decimal(row["target_weight"]).quantize(
            Decimal("0.000000000001"), rounding=ROUND_DOWN
        )
    return result


def signal_targets(
    root: Path, *, data_version: str, source_revision: str
) -> list[tuple[ForwardTarget, dict[str, Any]]]:
    value = verify_signal_archive(root)
    manifest = value["manifest"]
    if (
        manifest["bindings"]["data_version_id"] != data_version
        or manifest["bindings"]["code_revision"] != source_revision
    ):
        raise ValueError("FORWARD_CURRENT_SIGNAL_CONTEXT_MISMATCH")
    result: list[tuple[ForwardTarget, dict[str, Any]]] = []
    for strategy, allocation_record in sorted(value["targets"].items()):
        target = ForwardTarget(
            strategy,
            manifest["signal_family_id"],
            data_version,
            source_revision,
            datetime.fromisoformat(manifest["generated_at"]),
            datetime.fromisoformat(manifest["expected_execution_at"]),
            _weights(allocation_record["positions"]),
            tuple(manifest["limitations"]) + ("Weight precision floored to 1e-12",),
        )
        target.validate()
        result.append(
            (
                target,
                {
                    "kind": "signal-family",
                    "artifact_id": manifest["signal_family_id"],
                    "manifest_sha256": manifest["evidence_sha256"],
                    "strategy_id": strategy,
                    "model_id": None,
                },
            )
        )
    return result


def prediction_target(
    root: Path, *, active_model_id: str, data_version: str, source_revision: str
) -> tuple[ForwardTarget, dict[str, Any]]:
    manifest = verify_prediction(root)
    bindings = manifest["bindings"]
    if (
        bindings["model_id"] != active_model_id
        or bindings["data_version_id"] != data_version
        or bindings["code_revision"] != source_revision
    ):
        raise ValueError("FORWARD_ACTIVE_PREDICTION_CONTEXT_MISMATCH")
    scores_path = root / "calculation" / manifest["calculation_validation_id"] / "scores.parquet"
    scores = pq.read_table(scores_path).to_pylist()
    if len(scores) != manifest["score_count"] or not 1 <= len(scores) <= 6000:
        raise ValueError("FORWARD_MODEL_SCORE_COUNT_INVALID")
    positions = allocation(scores, method="TOPK_EQUAL_WEIGHT")
    contract = manifest["contract"]
    target = ForwardTarget(
        "approved_model_" + active_model_id,
        manifest["prediction_id"],
        data_version,
        source_revision,
        datetime.fromisoformat(contract["generated_at"]),
        datetime.fromisoformat(contract["expected_execution_at"]),
        _weights(positions),
        tuple(manifest["limitations"])
        + ("Top50 equal-weight research allocation; weight precision floored to 1e-12",),
    )
    target.validate()
    return target, {
        "kind": "approved-prediction",
        "artifact_id": manifest["prediction_id"],
        "manifest_sha256": manifest["evidence_sha256"],
        "strategy_id": target.strategy_id,
        "model_id": active_model_id,
    }


def _reparse(
    manifest: dict[str, Any],
    row: dict[str, Any],
    calendar: Calendar,
    captures: list[dict[str, Any]],
) -> None:
    """Reproduce the actual collector's day kline/quote reconciliation from raw bytes."""
    symbol = row["symbol"]
    code = ("sh" if row["exchange"] == "SSE" else "sz") + symbol
    klines = [
        c for c in captures if c["source_id"] == "sina_klines" and c["request"]["symbol"] == code
    ]
    quotes = [
        c
        for c in captures
        if c["source_id"] == "sina_quote" and code in c["request"]["list"].split(",")
    ]
    if len(klines) != 1 or len(quotes) != 1:
        raise ValueError("FORWARD_RAW_DAY_SOURCE_AMBIGUOUS")
    kline, quote = klines[0], quotes[0]
    if any(
        c["status_code"] != 200
        or c["transport_error"] is not None
        or c["body_complete"] is not True
        for c in (kline, quote)
    ):
        raise ValueError("FORWARD_RAW_DAY_SOURCE_INCOMPLETE")
    completed = datetime.fromisoformat(manifest["completed_at"])
    if any(
        datetime.fromisoformat(c["received_at"]).tzinfo is None
        or datetime.fromisoformat(c["received_at"]) > completed
        for c in (kline, quote)
    ):
        raise ValueError("FORWARD_RAW_RECEIPT_CLOCK_MISMATCH")
    day = date.fromisoformat(row["trading_day"])
    bars = completed_klines(
        kline["body"],
        symbol=symbol,
        security_type="STOCK",
        frequency="day",
        requested_day=day,
        observed_at=datetime.fromisoformat(kline["received_at"]),
        calendar=calendar,
    )
    lines = [
        line
        for line in quote["body"].splitlines()
        if line.startswith(f"var hq_str_{code}=".encode())
    ]
    if len(bars) != 1 or len(lines) != 1:
        raise ValueError("FORWARD_RAW_DAY_ROWS_AMBIGUOUS")
    parsed_quote = completed_quotes(
        lines[0],
        requested={symbol: "STOCK"},
        requested_day=day,
        observed_at=datetime.fromisoformat(quote["received_at"]),
        calendar=calendar,
    )[symbol]
    expected = reconcile_day(bars[0], parsed_quote).row()
    if expected != {
        key: value for key, value in row.items() if key not in {"source_hashes", "raw_pack_sha256"}
    }:
        raise ValueError("FORWARD_RAW_PARQUET_REPLAY_MISMATCH")
    if row["raw_pack_sha256"] != manifest["raw"]["sha256"] or sorted(
        row["source_hashes"]
    ) != sorted([kline["body_sha256"], quote["body_sha256"]]):
        raise ValueError("FORWARD_RAW_DAY_LINEAGE_MISMATCH")


def observed_session(
    store_root: Path,
    batch_ids: list[str],
    *,
    day: date,
    instruments: list[str],
    calendar: Calendar,
    now: datetime | None = None,
    previous_batch_ids: list[str] | None = None,
) -> tuple[ObservedSession, dict[str, Any]]:
    stamp = now or datetime.now(UTC)
    if stamp.tzinfo is None or not calendar.is_open(day):
        raise ValueError("FORWARD_OPEN_SESSION_AND_CLOCK_REQUIRED")
    close_at = datetime.combine(day, time(15), SHANGHAI)
    if stamp < close_at or not 1 <= len(batch_ids) <= 300 or len(set(batch_ids)) != len(batch_ids):
        raise ValueError("FORWARD_COMPLETED_BOUNDED_BATCHES_REQUIRED")
    if not 1 <= len(instruments) <= 6000 or len(set(instruments)) != len(instruments):
        raise ValueError("FORWARD_INSTRUMENT_SET_INVALID")
    requested = {_symbol(code): code for code in instruments}
    previous_day = calendar.previous(day - timedelta(days=1))
    open_at = datetime.combine(day, time(9, 30), SHANGHAI)
    previous_market, previous_evidence = None, None
    if previous_batch_ids is not None:
        previous_market, previous_evidence = observed_session(
            store_root,
            previous_batch_ids,
            day=previous_day,
            instruments=instruments,
            calendar=calendar,
            now=open_at - timedelta(microseconds=1),
        )
    store = UnifiedBatchStore(store_root)
    rows: dict[str, dict[str, Any]] = {}
    receipts, observations = [], []
    for batch_id in sorted(batch_ids):
        manifest = store.load(batch_id)
        completed = datetime.fromisoformat(manifest["completed_at"])
        if (
            manifest["spec"]["frequency"] != "day"
            or completed.tzinfo is None
            or not close_at <= completed <= stamp
            or datetime.fromisoformat(manifest["spec"]["target_end"]) != close_at
        ):
            raise ValueError("FORWARD_BATCH_DAY_OR_CLOCK_MISMATCH")
        receipts.append(
            {
                "batch_id": batch_id,
                "receipt_sha256": manifest["evidence_sha256"],
                "raw_sha256": manifest["raw"]["sha256"],
                "normalized_sha256": (manifest["normalized"] or {}).get("sha256"),
            }
        )
        observations.append(completed)
        if manifest["normalized"] is None:
            continue
        captures = store.read_captures(manifest["raw"])
        source_rows = pq.read_table(io.BytesIO(store.read_blob(manifest["normalized"]))).to_pylist()
        for row in source_rows:
            symbol = row["symbol"]
            if symbol not in requested:
                continue
            if (
                row["trading_day"] != day.isoformat()
                or row["frequency"] != "day"
                or row["completion_status"] != "CLOSED"
                or datetime.fromisoformat(row["timestamp"]) != close_at
                or row["exchange"] != ("SSE" if requested[symbol].startswith("SH") else "SZSE")
            ):
                raise ValueError("FORWARD_ROW_DAY_OR_IDENTITY_MISMATCH")
            _reparse(manifest, row, calendar, captures)
            if symbol in rows:
                if _row_content(rows[symbol]) != _row_content(row):
                    raise ValueError("FORWARD_CONFLICTING_DAILY_OBSERVATIONS")
            else:
                rows[symbol] = row
    if not rows:
        raise ValueError("FORWARD_NO_COMPLETE_REQUESTED_ROWS")
    next_day = day + timedelta(days=1)
    while not calendar.is_open(next_day):
        next_day += timedelta(days=1)
    securities = {}
    for symbol, row in rows.items():
        volume = Decimal(row["volume"])
        if not volume.is_finite() or volume < 0 or volume != volume.to_integral_value():
            raise ValueError("FORWARD_NONINTEGER_VOLUME")
        code = requested[symbol]
        # Daily quote/kline agreement does NOT prove suspension/ST/limit/action clearance.
        securities[code] = ObservedSecurity(
            code,
            row["exchange"],
            Decimal(row["open"]),
            Decimal(row["close"]),
            int(volume),
            0 if code.startswith(("SH688", "SH689")) else 100,
            Decimal("0.01"),
            None,
            None,
            None,
            None,
            None,
            signed(row)["evidence_sha256"],
            previous_session_volume=(
                previous_market.securities[code].volume
                if previous_market is not None and code in previous_market.securities
                else None
            ),
        )
    evidence = signed(
        {
            **AUTHORITY,
            "schema_version": "qtrade.forward-market-input.v1",
            "day": day.isoformat(),
            "receipts": receipts,
            "requested": instruments,
            "observed": sorted(securities),
            "missing": sorted(set(instruments) - set(securities)),
            "calendar_sha256": signed(calendar.document)["evidence_sha256"],
            "price_basis": "SINA_NATIVE_ADJUSTMENT_UNCERTIFIED",
            "raw_replay_verified": True,
            "constraints_certified": False,
            "previous_session_volume_evidence": previous_evidence,
        }
    )
    session = ObservedSession(
        day,
        previous_day,
        next_day,
        open_at,
        close_at,
        max(observations),
        evidence["calendar_sha256"],
        evidence["evidence_sha256"],
        securities,
    )
    session.validate(stamp)
    return session, evidence


def _row_content(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in {"source_hashes", "raw_pack_sha256"}}


def public_book_summary(
    book: ForwardPaperBook, *, archive_link: dict[str, Any], limit: int = 30
) -> dict[str, Any]:
    if not 1 <= limit <= 30:
        raise ValueError("FORWARD_PUBLIC_SUMMARY_LIMIT")
    if (
        archive_link.get("strategy_id") != book.strategy_id
        or re.fullmatch("[a-f0-9]{64}", str(archive_link.get("artifact_id"))) is None
        or re.fullmatch("[a-f0-9]{64}", str(archive_link.get("manifest_sha256"))) is None
        or archive_link.get("kind") not in {"signal-family", "approved-prediction"}
        or (
            archive_link.get("model_id") is not None
            and re.fullmatch("[a-f0-9]{64}", str(archive_link["model_id"])) is None
        )
    ):
        raise ValueError("FORWARD_ARCHIVE_LINK_INVALID")
    result: list[dict[str, Any]] = []
    for day, status, raw in book.db.execute(
        "SELECT day,status,result FROM sessions ORDER BY day DESC LIMIT ?", (limit,)
    ):
        value = json.loads(raw) if raw else {}
        detail_limit = 0 if result else 50
        result.append(
            {
                "day": day,
                "receipt_state": status,
                "state": value.get("status", status),
                "target_id": value.get("target_id"),
                "signal_id": value.get("signal_id"),
                "data_version": value.get("data_version"),
                "source_revision": value.get("source_revision"),
                "observed_at": value.get("observed_at"),
                "market_evidence": value.get("market_evidence"),
                "calendar_evidence": value.get("calendar_evidence"),
                "fill_count": len(value["fills"]) if raw else None,
                "fills": [
                    {
                        k: row.get(k)
                        for k in ("symbol", "side", "quantity", "price", "fee", "timestamp")
                    }
                    for row in value.get("fills", [])[:detail_limit]
                ],
                "fills_truncated": len(value.get("fills", [])) > detail_limit,
                "holding_count": len(value["holdings"]) if raw else None,
                "holdings": [
                    {"instrument": symbol, "quantity": quantity}
                    for symbol, quantity in sorted(value.get("holdings", {}).items())[:detail_limit]
                ],
                "holdings_truncated": len(value.get("holdings", {})) > detail_limit,
                "order_count": len(value["orders"]) if raw else None,
                "orders": [
                    {k: row.get(k) for k in ("symbol", "side", "quantity", "status", "reason")}
                    for row in value.get("orders", [])[:detail_limit]
                ],
                "orders_truncated": len(value.get("orders", [])) > detail_limit,
                "blocked_count": len(value["blocked"]) if raw else None,
                "blockers": [
                    {"instrument": symbol, "reason": reason}
                    for symbol, reason in sorted(value.get("blocked", {}).items())[:detail_limit]
                ],
                "blockers_truncated": len(value.get("blocked", {})) > detail_limit,
                "blocked_reasons": dict(Counter(value.get("blocked", {}).values())),
                "cash": value.get("cash"),
                "net_asset_value": value.get("net_asset_value"),
                "forward_return": value.get("forward_return"),
                "turnover": value.get("turnover"),
                "limitations": value.get(
                    "limitations", ["Uncertain event; reconciliation required"]
                ),
            }
        )
    frozen: list[dict[str, Any]] = []
    for identity, payload, frozen_at in book.db.execute(
        "SELECT id,payload,frozen_at FROM targets ORDER BY execution_at DESC LIMIT 10"
    ):
        target = json.loads(payload)
        weight_limit = 0 if frozen else 50
        frozen.append(
            {
                "target_id": identity,
                "signal_id": target["signal_id"],
                "data_version": target["data_version"],
                "source_revision": target["source_revision"],
                "generated_at": target["generated_at"],
                "execution_at": target["execution_at"],
                "frozen_at": frozen_at,
                "weight_count": len(target["weights"]),
                "weights": [
                    {"instrument": symbol, "weight": weight}
                    for symbol, weight in sorted(target["weights"].items())[:weight_limit]
                ],
                "weights_truncated": len(target["weights"]) > weight_limit,
            }
        )
    return signed(
        {
            **AUTHORITY,
            "schema_version": "qtrade.forward-book-summary.v1",
            "strategy_id": book.strategy_id,
            "mode": book.mode.value,
            "archive": {
                k: archive_link.get(k)
                for k in ("kind", "artifact_id", "manifest_sha256", "strategy_id", "model_id")
            },
            "risk_state": book.bundle.risk.state.value,
            "running": book.bundle.runtime.status.running,
            "recovery_required": book.mode.value == "PAPER"
            and (
                not book.bundle.runtime.status.running or book.bundle.risk.state.value == "HALTED"
            ),
            "sessions": result,
            "frozen_targets": frozen,
            "target_count": book.db.execute("SELECT COUNT(*) FROM targets").fetchone()[0],
            "generated_at": datetime.now(UTC).isoformat(),
        }
    )
