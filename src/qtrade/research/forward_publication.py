"""Strict bounded read-only publication contract for forward research books."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from qtrade.domain import OrderStatus
from qtrade.monitoring.data_freshness import AUTHORITY
from qtrade.research.model_registry import checked_document, checked_identity


def _exact(value: dict[str, Any], keys: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("FORWARD_PUBLIC_FIELDS_INVALID")


def _clock(value: Any, now: datetime, *, future: bool = False) -> datetime:
    if not isinstance(value, str):
        raise ValueError("FORWARD_PUBLIC_CLOCK_INVALID")
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or (not future and stamp > now + timedelta(seconds=60)):
        raise ValueError("FORWARD_PUBLIC_CLOCK_INVALID")
    return stamp


def _code(value: Any, *, length: int = 128) -> None:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9_.:-]{1," + str(length) + "}", value) is None
    ):
        raise ValueError("FORWARD_PUBLIC_CODE_INVALID")


def _count(value: Any, *, maximum: int = 6000) -> None:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("FORWARD_PUBLIC_COUNT_INVALID")


def _number(value: Any, *, minimum: Decimal | None = None) -> None:
    if value is None:
        return
    try:
        if not isinstance(value, str) or len(value) > 100:
            raise ValueError("FORWARD_PUBLIC_NUMBER_INVALID")
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("FORWARD_PUBLIC_NUMBER_INVALID") from error
    if not number.is_finite() or minimum is not None and number < minimum:
        raise ValueError("FORWARD_PUBLIC_NUMBER_INVALID")


def _strings(values: Any) -> None:
    if (
        not isinstance(values, list)
        or not 1 <= len(values) <= 32
        or any(not isinstance(v, str) or not 1 <= len(v) <= 2000 for v in values)
    ):
        raise ValueError("FORWARD_PUBLIC_LIMITATIONS_INVALID")


def _signed(value: dict[str, Any]) -> None:
    if not isinstance(value, dict):
        raise ValueError("FORWARD_PUBLIC_DOCUMENT_INVALID")
    checked_document(value)
    if any(type(value[k]) is not type(v) for k, v in AUTHORITY.items()):
        raise ValueError("FORWARD_PUBLIC_AUTHORITY_INVALID")


def _details(row: dict[str, Any], key: str, count_key: str, *, pending: bool) -> None:
    values, total, truncated = row[key], row[count_key], row[key + "_truncated"]
    if not isinstance(values, list) or len(values) > 50 or type(truncated) is not bool:
        raise ValueError("FORWARD_PUBLIC_DETAILS_BOUND_INVALID")
    if pending:
        if values or total is not None or truncated:
            raise ValueError("FORWARD_PUBLIC_PENDING_MUST_REMAIN_UNKNOWN")
    else:
        _count(total)
        if len(values) > total or truncated != (len(values) < total):
            raise ValueError("FORWARD_PUBLIC_DETAILS_COUNT_MISMATCH")


def _book(book: dict[str, Any], now: datetime) -> None:
    _signed(book)
    _exact(
        book,
        set(AUTHORITY)
        | {
            "evidence_sha256",
            "schema_version",
            "strategy_id",
            "mode",
            "archive",
            "risk_state",
            "running",
            "recovery_required",
            "sessions",
            "frozen_targets",
            "target_count",
            "generated_at",
        },
    )
    if book["schema_version"] != "qtrade.forward-book-summary.v1" or book["mode"] not in {
        "PAPER",
        "SHADOW",
    }:
        raise ValueError("FORWARD_PUBLIC_BOOK_SCHEMA_INVALID")
    _code(book["strategy_id"])
    _clock(book["generated_at"], now)
    _count(book["target_count"], maximum=1_000_000)
    if (
        book["risk_state"] not in {"NORMAL", "PAUSED", "REDUCE_ONLY", "HALTED"}
        or type(book["running"]) is not bool
        or type(book["recovery_required"]) is not bool
        or book["recovery_required"]
        != (book["mode"] == "PAPER" and (not book["running"] or book["risk_state"] == "HALTED"))
    ):
        raise ValueError("FORWARD_PUBLIC_RECOVERY_STATE_INVALID")
    archive = book["archive"]
    _exact(archive, {"kind", "artifact_id", "manifest_sha256", "strategy_id", "model_id"})
    checked_identity(archive["artifact_id"])
    checked_identity(archive["manifest_sha256"])
    if archive["strategy_id"] != book["strategy_id"]:
        raise ValueError("FORWARD_PUBLIC_ARCHIVE_STRATEGY_MISMATCH")
    if archive["kind"] == "approved-prediction":
        checked_identity(archive["model_id"])
        if book["strategy_id"] != "approved_model_" + archive["model_id"]:
            raise ValueError("FORWARD_PUBLIC_MODEL_ACCOUNT_MISMATCH")
    elif archive["kind"] != "signal-family" or archive["model_id"] is not None:
        raise ValueError("FORWARD_PUBLIC_ARCHIVE_KIND_INVALID")
    targets = book["frozen_targets"]
    if not isinstance(targets, list) or len(targets) > min(10, book["target_count"]):
        raise ValueError("FORWARD_PUBLIC_TARGET_BOUND_INVALID")
    seen = set()
    for target in targets:
        _exact(
            target,
            {
                "target_id",
                "signal_id",
                "data_version",
                "source_revision",
                "generated_at",
                "execution_at",
                "frozen_at",
                "weight_count",
                "weights",
                "weights_truncated",
            },
        )
        for key in ("target_id", "signal_id", "data_version"):
            checked_identity(target[key])
        if target["target_id"] in seen:
            raise ValueError("FORWARD_PUBLIC_DUPLICATE_TARGET")
        seen.add(target["target_id"])
        _revision(target["source_revision"])
        if not (
            _clock(target["generated_at"], now)
            <= _clock(target["frozen_at"], now)
            < _clock(target["execution_at"], now, future=True)
        ):
            raise ValueError("FORWARD_PUBLIC_TARGET_NOT_PROSPECTIVE")
        _details(target, "weights", "weight_count", pending=False)
        instruments = set()
        for weight in target["weights"]:
            _exact(weight, {"instrument", "weight"})
            _code(weight["instrument"])
            _number(weight["weight"], minimum=Decimal(0))
            if (
                weight["weight"] is None
                or Decimal(weight["weight"]) > 1
                or weight["instrument"] in instruments
            ):
                raise ValueError("FORWARD_PUBLIC_WEIGHT_INVALID")
            instruments.add(weight["instrument"])
    sessions = book["sessions"]
    if not isinstance(sessions, list) or len(sessions) > 30:
        raise ValueError("FORWARD_PUBLIC_SESSION_BOUND_INVALID")
    previous_day = "9999-12-31"
    for row in sessions:
        _session(row, book["mode"], now)
        if row["day"] >= previous_day:
            raise ValueError("FORWARD_PUBLIC_SESSION_ORDER_INVALID")
        previous_day = row["day"]


def _revision(value: Any) -> None:
    if not isinstance(value, str) or re.fullmatch("[a-f0-9]{40}", value) is None:
        raise ValueError("FORWARD_PUBLIC_REVISION_INVALID")


def _session(row: dict[str, Any], mode: str, now: datetime) -> None:
    _exact(
        row,
        {
            "day",
            "receipt_state",
            "state",
            "target_id",
            "signal_id",
            "data_version",
            "source_revision",
            "observed_at",
            "market_evidence",
            "calendar_evidence",
            "fill_count",
            "fills",
            "fills_truncated",
            "holding_count",
            "holdings",
            "holdings_truncated",
            "order_count",
            "orders",
            "orders_truncated",
            "blocked_count",
            "blockers",
            "blockers_truncated",
            "blocked_reasons",
            "cash",
            "net_asset_value",
            "forward_return",
            "turnover",
            "limitations",
        },
    )
    if date.fromisoformat(row["day"]).isoformat() != row["day"]:
        raise ValueError("FORWARD_PUBLIC_DAY_INVALID")
    pending = row["receipt_state"] == "PENDING"
    expected_states = (
        {"PENDING"}
        if pending
        else ({"SHADOW_OBSERVED"} if mode == "SHADOW" else {"PAPER_OBSERVED", "HALTED_UNVALUED"})
    )
    if row["receipt_state"] not in {"PENDING", "COMPLETE"} or row["state"] not in expected_states:
        raise ValueError("FORWARD_PUBLIC_SESSION_STATE_INVALID")
    for key in ("target_id", "signal_id", "data_version", "market_evidence", "calendar_evidence"):
        if pending:
            if row[key] is not None:
                raise ValueError("FORWARD_PUBLIC_PENDING_IDENTITY_INVALID")
        else:
            checked_identity(row[key])
    if pending:
        if row["source_revision"] is not None or row["observed_at"] is not None:
            raise ValueError("FORWARD_PUBLIC_PENDING_CLOCK_INVALID")
    else:
        _revision(row["source_revision"])
        _clock(row["observed_at"], now)
    for key, count_key in (
        ("fills", "fill_count"),
        ("holdings", "holding_count"),
        ("orders", "order_count"),
        ("blockers", "blocked_count"),
    ):
        _details(row, key, count_key, pending=pending)
    for holding in row["holdings"]:
        _exact(holding, {"instrument", "quantity"})
        _code(holding["instrument"])
        _count(holding["quantity"], maximum=100_000_000)
        if holding["quantity"] == 0:
            raise ValueError("FORWARD_PUBLIC_ZERO_HOLDING_INVALID")
    if len({holding["instrument"] for holding in row["holdings"]}) != len(row["holdings"]):
        raise ValueError("FORWARD_PUBLIC_DUPLICATE_HOLDING")
    for fill in row["fills"]:
        _exact(fill, {"symbol", "side", "quantity", "price", "fee", "timestamp"})
        _code(fill["symbol"])
        _count(fill["quantity"], maximum=100_000_000)
        if fill["side"] not in {"BUY", "SELL"} or fill["quantity"] == 0:
            raise ValueError("FORWARD_PUBLIC_FILL_INVALID")
        for key in ("price", "fee"):
            _number(fill[key], minimum=Decimal(0))
            if fill[key] is None:
                raise ValueError("FORWARD_PUBLIC_FILL_VALUE_REQUIRED")
        if Decimal(fill["price"]) == 0:
            raise ValueError("FORWARD_PUBLIC_FILL_PRICE_INVALID")
        _clock(fill["timestamp"], now)
    for order in row["orders"]:
        _exact(order, {"symbol", "side", "quantity", "status", "reason"})
        _code(order["symbol"])
        _count(order["quantity"], maximum=100_000_000)
        _code(order["status"], length=40)
        if (
            order["side"] not in {"BUY", "SELL"}
            or order["status"] not in set(OrderStatus)
            or order["quantity"] == 0
            or order["reason"] is not None
            and (not isinstance(order["reason"], str) or len(order["reason"]) > 500)
        ):
            raise ValueError("FORWARD_PUBLIC_ORDER_INVALID")
    reasons = row["blocked_reasons"]
    if not isinstance(reasons, dict) or len(reasons) > 32:
        raise ValueError("FORWARD_PUBLIC_BLOCK_REASONS_INVALID")
    for reason, count in reasons.items():
        _code(reason)
        _count(count)
        if count == 0:
            raise ValueError("FORWARD_PUBLIC_BLOCK_REASON_COUNT_INVALID")
    if sum(reasons.values()) != (row["blocked_count"] or 0):
        raise ValueError("FORWARD_PUBLIC_BLOCK_REASON_COUNT_MISMATCH")
    for blocker in row["blockers"]:
        _exact(blocker, {"instrument", "reason"})
        _code(blocker["instrument"])
        if blocker["reason"] not in reasons:
            raise ValueError("FORWARD_PUBLIC_BLOCKER_REASON_INVALID")
    if len({blocker["instrument"] for blocker in row["blockers"]}) != len(row["blockers"]):
        raise ValueError("FORWARD_PUBLIC_DUPLICATE_BLOCKER")
    for key in ("cash", "net_asset_value", "turnover"):
        _number(row[key], minimum=Decimal(0))
    _number(row["forward_return"], minimum=Decimal(-1))
    unknown_metrics = pending or mode == "SHADOW" or row["state"] == "HALTED_UNVALUED"
    if unknown_metrics and any(
        row[k] is not None for k in ("net_asset_value", "forward_return", "turnover")
    ):
        raise ValueError("FORWARD_PUBLIC_UNOBSERVED_METRIC")
    if pending and row["cash"] is not None:
        raise ValueError("FORWARD_PUBLIC_PENDING_CASH_UNKNOWN")
    if row["state"] == "PAPER_OBSERVED" and any(
        row[k] is None for k in ("cash", "net_asset_value", "forward_return", "turnover")
    ):
        raise ValueError("FORWARD_PUBLIC_OBSERVED_METRICS_REQUIRED")
    if mode == "SHADOW" and (
        any(row[k] not in (None, 0) for k in ("fill_count", "holding_count", "order_count"))
        or row["cash"] is not None
    ):
        raise ValueError("FORWARD_PUBLIC_SHADOW_CANNOT_EXECUTE")
    _strings(row["limitations"])


def validate_forward_publication(
    value: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    stamp = now or datetime.now(UTC)
    if len(json.dumps(value, allow_nan=False).encode()) > 2_000_000:
        raise ValueError("FORWARD_PUBLIC_SIZE_LIMIT")
    _signed(value)
    _exact(
        value,
        set(AUTHORITY)
        | {
            "evidence_sha256",
            "schema_version",
            "generated_at",
            "heartbeat_at",
            "source_revision",
            "state",
            "books",
            "errors",
            "limitations",
        },
    )
    if value["schema_version"] != "qtrade.forward-service.v1" or value["state"] not in {
        "RUNNING",
        "DEGRADED",
        "HUMAN_RECOVERY_REQUIRED",
        "STOPPED",
    }:
        raise ValueError("FORWARD_PUBLIC_SCHEMA_INVALID")
    _revision(value["source_revision"])
    _clock(value["generated_at"], stamp)
    _clock(value["heartbeat_at"], stamp)
    books, errors = value["books"], value["errors"]
    if (
        not isinstance(books, list)
        or len(books) > 32
        or not isinstance(errors, list)
        or len(errors) > 64
    ):
        raise ValueError("FORWARD_PUBLIC_LIST_BOUND_INVALID")
    identities = set()
    for book in books:
        _book(book, stamp)
        key = (book["strategy_id"], book["mode"])
        if key in identities:
            raise ValueError("FORWARD_PUBLIC_DUPLICATE_BOOK")
        identities.add(key)
    for error in errors:
        _exact(error, {"strategy_id", "mode", "code"})
        if error["strategy_id"] is not None:
            _code(error["strategy_id"])
        if error["mode"] not in {"PAPER", "SHADOW", None}:
            raise ValueError("FORWARD_PUBLIC_ERROR_MODE_INVALID")
        _code(error["code"])
    _strings(value["limitations"])
    return value
