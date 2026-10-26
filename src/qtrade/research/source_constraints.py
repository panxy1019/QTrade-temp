"""Freeze vendor evidence and apply additional diagnostic restrictions, never PIT authority."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.workflow import canonical, digest

POLICY = "RECONCILED_BACKFILL_ADDITIONAL_BLOCKS_V1"
PROTOCOL = "ALIGNED_SUPPORTED_UNIVERSE_SOURCE_BOUND_CONSTRAINTS_V2"


def _signed(value: dict[str, Any]) -> None:
    if signed({k: v for k, v in value.items() if k != "evidence_sha256"}) != value:
        raise ValueError("SOURCE_EVIDENCE_HASH_MISMATCH")


def _observed(value: Any, now: datetime) -> None:
    observed = datetime.fromisoformat(value)
    if observed.tzinfo is None or observed > now:
        raise ValueError("SOURCE_OBSERVATION_CLOCK_INVALID")


@dataclass(frozen=True)
class SourceSnapshot:
    coverage: dict[str, Any]
    master: dict[str, Any] | None
    statuses: dict[str, dict[str, Any]]

    @property
    def identity(self) -> str:
        # Bind actual inputs, including the exact coverage report; not just counts.
        return digest({"coverage": digest(self.coverage),
                       "master": digest(self.master) if self.master else None,
                       "statuses": {s: digest(v) for s, v in sorted(self.statuses.items())}})

    def freeze(self, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=False)
        (destination / "coverage.json").write_bytes(canonical(self.coverage))
        if self.master is not None:
            folder = destination / "master"
            folder.mkdir()
            (folder / (digest(self.master) + ".json")).write_bytes(canonical(self.master))
        folder = destination / "status"
        folder.mkdir()
        for symbol, record in self.statuses.items():
            (folder / (symbol + ".json")).write_bytes(canonical(record))
        (destination / "snapshot.json").write_bytes(canonical(signed({
            **AUTHORITY, "schema_version": "qtrade.source-snapshot.v1",
            "source_snapshot_sha256": self.identity, "historical_pit_certified": False,
        })))


def capture_source(root: Path | None, *, now: datetime | None = None) -> SourceSnapshot:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED")
    if root is None or not (root / "coverage.json").exists():
        return SourceSnapshot({"state": "NO_REPAIR_COVERAGE_AT_SUBMISSION",
                               "historical_pit_certified": False}, None, {})
    coverage: dict[str, Any] = json.loads((root / "coverage.json").read_bytes())
    # Reproduction accepts a previously frozen empty snapshot, never an uncertified claim.
    if coverage == {"state": "NO_REPAIR_COVERAGE_AT_SUBMISSION",
                    "historical_pit_certified": False}:
        empty = SourceSnapshot(coverage, None, {})
        pointer = root / "snapshot.json"
        if pointer.exists():
            frozen = json.loads(pointer.read_bytes())
            _signed(frozen)
            if frozen.get("source_snapshot_sha256") != empty.identity:
                raise ValueError("FROZEN_SOURCE_SNAPSHOT_MISMATCH")
        return empty
    _signed(coverage)
    if coverage.get("schema_version") != "qtrade.pit-repair.v1" or any(
        coverage.get(k) is not False for k in (
            "historical_pit_certified", "historical_available_at_known",
            "live_trading_enabled", "external_order_transmission",
        )
    ):
        raise ValueError("BACKFILL_AUTHORITY_INVALID")
    _observed(coverage["observed_at"], now)
    master_id = coverage["master_sha256"]
    if not isinstance(master_id, str) or re.fullmatch(r"[a-f0-9]{64}", master_id) is None:
        raise ValueError("SOURCE_MASTER_IDENTITY_INVALID")
    master: dict[str, Any] = json.loads((root / "master" / (master_id + ".json")).read_bytes())
    if digest(master) != master_id or master.get("source") != "BaoStock query_stock_basic":
        raise ValueError("SOURCE_MASTER_HASH_MISMATCH")
    _observed(master["observed_at"], now)
    if master.get("historical_available_at") is not None:
        raise ValueError("BACKFILL_MUST_NOT_INVENT_AVAILABILITY")
    checksums = coverage.get("source_checksums")
    if not isinstance(checksums, dict) or not 0 <= len(checksums) <= 1000:
        raise ValueError("SOURCE_CHECKSUM_LIST_INVALID")
    statuses = {}
    for name, identity in checksums.items():
        if re.fullmatch(r"[0-9]{6}\.json", name) is None:
            raise ValueError("SOURCE_STATUS_PATH_INVALID")
        record: dict[str, Any] = json.loads((root / "status" / name).read_bytes())
        _signed(record)
        symbol = name[:6]
        if digest(record) != identity or record.get("symbol") != symbol or (
            record.get("source") != "BaoStock raw daily status backfill"
        ):
            raise ValueError("SOURCE_STATUS_IDENTITY_MISMATCH")
        if record.get("historical_available_at") is not None or record.get(
            "certified_at_decision"
        ) is not False:
            raise ValueError("BACKFILL_MUST_NOT_INVENT_AVAILABILITY")
        _observed(record["observed_at"], now)
        previous = ""
        code = ("sh." if symbol.startswith("6") else "sz.") + symbol
        if not isinstance(record.get("rows"), list) or not record["rows"]:
            raise ValueError("SOURCE_STATUS_ROWS_INVALID")
        for row in record["rows"]:
            day = row["date"]
            if date.fromisoformat(day).isoformat() != day or day <= previous or (
                day > datetime.fromisoformat(record["observed_at"]).date().isoformat()
                or row.get("code") != code
                or row.get("tradestatus") not in {"0", "1"}
                or row.get("isST") not in {"0", "1"}
            ):
                raise ValueError("SOURCE_STATUS_ROW_IDENTITY_OR_STATE_INVALID")
            previous = day
        statuses[symbol] = record
    if len(statuses) != coverage["status_symbols"] or sum(
        len(r["rows"]) for r in statuses.values()
    ) != coverage["status_rows"] or len(master["rows"]) != coverage["master_rows"]:
        raise ValueError("SOURCE_COVERAGE_COUNT_MISMATCH")
    snapshot = SourceSnapshot(coverage, master, statuses)
    pointer = root / "snapshot.json"
    if pointer.exists():
        frozen = json.loads(pointer.read_bytes())
        _signed(frozen)
        if frozen.get("source_snapshot_sha256") != snapshot.identity:
            raise ValueError("FROZEN_SOURCE_SNAPSHOT_MISMATCH")
    return snapshot


def _number(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("SOURCE_NUMBER_INVALID") from None
    if not result.is_finite() or result < 0:
        raise ValueError("SOURCE_NUMBER_INVALID")
    return result


def restrict_rows(
    rows: list[dict[str, Any]], symbol: str, record: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Post-hoc backfills only add restrictions. They cannot establish historical tradability.

    Reconcile raw open/close within CNY 0.011, volume exactly, amount within
    max(CNY 1, 1ppm). Use no price-adjustment guesses or future-filled state.
    The ST buy exclusion is an explicit research policy, not an exchange rule.
    """
    status = {r["date"]: r for r in record["rows"]} if record else {}
    counts = {key: 0 for key in (
        "bars", "missing_state_bars", "reconciled_bars", "conflict_bars",
        "source_suspended_bars", "st_buy_exclusions", "additional_buy_blocks",
        "additional_sell_blocks",
    )}
    result, ledger = [], []
    for original in rows:
        row = dict(original)
        counts["bars"] += 1
        base = [_number(row[key]) for key in ("buy_block", "sell_block")]
        if any(value not in (0, 1) for value in base):
            raise ValueError("BINARY_BASE_CONSTRAINT_REQUIRED")
        before = [int(value) for value in base]
        source = status.get(row["date"])
        reason = "SOURCE_STATE_MISSING_PROXY_RETAINED"
        if source is None:
            counts["missing_state_bars"] += 1
        elif source["tradestatus"] == "0":
            row["buy_block"] = row["sell_block"] = 1.0
            counts["source_suspended_bars"] += 1
            counts["conflict_bars"] += 1  # Existing normalized row has nonzero volume.
            reason = "SOURCE_SUSPENDED_BUT_PRICE_ROW_EXISTS_BLOCK_BOTH"
        else:
            try:
                matches = all(abs(_number(source[field]) - _number(row[raw])) <= Decimal("0.011")
                              for field, raw in (("open", "raw_open"), ("close", "raw_close")))
                matches = matches and _number(source["volume"]) == _number(row["raw_volume"])
                amount = _number(row["amount"])
                matches = matches and abs(_number(source["amount"]) - amount) <= max(
                    Decimal(1), amount * Decimal("0.000001")
                )
            except (ValueError, KeyError):
                matches = False
            if not matches:
                row["buy_block"] = row["sell_block"] = 1.0
                counts["conflict_bars"] += 1
                reason = "RAW_SOURCE_CONFLICT_BLOCK_BOTH"
            else:
                counts["reconciled_bars"] += 1
                reason = "RECONCILED_NON_ST_PROXY_RETAINED"
                if source["isST"] == "1":
                    row["buy_block"] = 1.0
                    counts["st_buy_exclusions"] += 1
                    reason = "RECONCILED_ST_RESEARCH_BUY_EXCLUSION"
        after = [int(row[key]) for key in ("buy_block", "sell_block")]
        assert all(a >= b for a, b in zip(after, before, strict=True))
        counts["additional_buy_blocks"] += after[0] - before[0]
        counts["additional_sell_blocks"] += after[1] - before[1]
        result.append(row)
        if source is not None:
            ledger.append({"symbol": symbol, "date": row["date"], "reason": reason,
                           "base_buy_block": before[0], "base_sell_block": before[1],
                           "buy_block": after[0], "sell_block": after[1]})
    return result, {"symbol": symbol, **counts}, ledger
