"""Prospectively frozen research targets over the existing durable PAPER engine.

This is a research simulation adapter, never an external execution gateway.
Uncertain events are retained for human reconciliation, never silently replayed.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any

from qtrade.domain import Instrument, OrderRequest, OrderStatus, RuntimeMode, Side
from qtrade.execution import FeeSchedule
from qtrade.risk import KillState, RiskContext, RiskLimits
from qtrade.runtime.paper import build_paper_runtime


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)


def _time(value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError("timezone-aware timestamps required")


@dataclass(frozen=True)
class ForwardTarget:
    strategy_id: str
    signal_id: str
    data_version: str
    source_revision: str
    generated_at: datetime
    execution_at: datetime
    weights: dict[str, Decimal]
    limitations: tuple[str, ...]

    def validate(self) -> None:
        _time(self.generated_at)
        _time(self.execution_at)
        if not all((self.strategy_id, self.signal_id, self.data_version, self.source_revision)):
            raise ValueError("target provenance is required")
        if self.generated_at >= self.execution_at:
            raise ValueError("targets must predate execution")
        if not self.limitations:
            raise ValueError("research limitations are required")
        if len(self.weights) > 6000:
            raise ValueError("target instrument limit exceeded")
        if any(not k or not v.is_finite() or v < 0 or v > 1 for k, v in self.weights.items()):
            raise ValueError("invalid long-only weights")
        if sum(self.weights.values(), Decimal(0)) > Decimal("0.95"):
            raise ValueError("at least five percent cash reserve required")

    @property
    def identity(self) -> str:
        self.validate()
        return sha256(_json(asdict(self)).encode()).hexdigest()


@dataclass(frozen=True)
class ObservedSecurity:
    symbol: str
    exchange: str
    raw_open: Decimal
    raw_close: Decimal
    volume: int
    lot_size: int
    price_tick: Decimal
    suspended: bool | None
    st: bool | None
    upper_limit: Decimal | None
    lower_limit: Decimal | None
    corporate_action_clear: bool | None
    evidence_id: str
    previous_session_volume: int | None = None

    def block_reason(self) -> str | None:
        if (
            any(
                value is not None and type(value) is not bool
                for value in (self.suspended, self.st, self.corporate_action_clear)
            )
            or type(self.volume) is not int
            or type(self.lot_size) is not int
            or self.previous_session_volume is not None
            and type(self.previous_session_volume) is not int
            or not self.symbol
            or self.exchange not in {"SSE", "SZSE"}
        ):
            return "INVALID_TRADING_CONSTRAINTS"
        if (
            self.suspended is None
            or self.st is None
            or self.upper_limit is None
            or self.lower_limit is None
            or self.corporate_action_clear is None
            or not self.evidence_id
        ):
            return "UNKNOWN_TRADING_CONSTRAINTS"
        if not self.corporate_action_clear:
            return "CORPORATE_ACTION_REQUIRES_RECONCILIATION"
        if self.suspended:
            return "SUSPENDED"
        if self.st:
            return "ST_EXCLUDED"
        if self.previous_session_volume is None:
            return "UNKNOWN_PREVIOUS_SESSION_VOLUME"
        if (
            self.volume < 0
            or self.previous_session_volume <= 0
            or self.lot_size <= 0
            or not self.price_tick.is_finite()
            or self.price_tick <= 0
            or any(
                not v.is_finite() or v <= 0
                for v in (self.raw_open, self.raw_close, self.upper_limit, self.lower_limit)
            )
            or self.lower_limit >= self.upper_limit
            or not self.lower_limit <= self.raw_open <= self.upper_limit
            or not self.lower_limit <= self.raw_close <= self.upper_limit
        ):
            return "INVALID_MARKET_VALUES"
        return None


@dataclass(frozen=True)
class ObservedSession:
    trading_day: date
    previous_session: date
    next_session: date
    open_at: datetime
    close_at: datetime
    observed_at: datetime
    calendar_evidence: str
    market_evidence: str
    securities: dict[str, ObservedSecurity]

    def validate(self, now: datetime) -> None:
        for stamp in (self.open_at, self.close_at, self.observed_at, now):
            _time(stamp)
        if not (self.open_at < self.close_at <= self.observed_at <= now):
            raise ValueError("completed observed session required")
        if (
            self.open_at.date() != self.trading_day
            or self.close_at.date() != self.trading_day
            or not self.previous_session < self.trading_day < self.next_session
            or not self.calendar_evidence
            or not self.market_evidence
        ):
            raise ValueError("verified chronological calendar and evidence required")
        if any(key != value.symbol for key, value in self.securities.items()):
            raise ValueError("market symbol mismatch")


class ForwardPaperBook:
    """One strategy/mode/account, local SQLite, separate immutable stages.

    Daily open fills are explicitly end-of-day simulated fills. A target must
    already be persisted before that open; SHADOW never invokes the order path.
    """

    def __init__(
        self,
        root: Path,
        *,
        strategy_id: str,
        mode: RuntimeMode,
        opening_cash: Decimal,
        fees: FeeSchedule,
    ) -> None:
        if mode not in (RuntimeMode.PAPER, RuntimeMode.SHADOW):
            raise ValueError("only PAPER or SHADOW allowed")
        if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
            raise ValueError("LIVE_TRADING_ENABLED must remain false")
        if not strategy_id or not opening_cash.is_finite() or opening_cash <= 0:
            raise ValueError("strategy and positive opening cash required")
        root.mkdir(parents=True, exist_ok=True)
        self.strategy_id, self.mode, self.fees = strategy_id, mode, fees
        self.opening_cash = opening_cash
        self._lock = RLock()
        self.db = sqlite3.connect(root / "forward.sqlite", isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS targets
              (id TEXT PRIMARY KEY, execution_at TEXT UNIQUE NOT NULL, payload TEXT NOT NULL,
               frozen_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions
              (day TEXT PRIMARY KEY, target_id TEXT NOT NULL, input_hash TEXT NOT NULL,
               status TEXT NOT NULL, result TEXT, input_payload TEXT NOT NULL);
        """)
        config = _json(
            {
                "strategy": strategy_id,
                "mode": mode.value,
                "cash": opening_cash,
                "fees": asdict(fees),
                "schema": 1,
            }
        )
        self.db.execute("INSERT OR IGNORE INTO config VALUES (1, ?)", (config,))
        if self.db.execute("SELECT payload FROM config WHERE id=1").fetchone()[0] != config:
            raise ValueError("immutable account configuration mismatch")
        self.bundle = build_paper_runtime(
            mode=mode,
            opening_cash=opening_cash,
            fees=fees,
            limits=RiskLimits(
                opening_cash,
                100_000_000,
                opening_cash * 10,
                market_data_max_age=timedelta(days=1),
                estimated_buy_fee_rate=fees.commission_rate
                + fees.transfer_rate
                + fees.exchange_handling_rate
                + fees.regulatory_rate,
                minimum_buy_fee=fees.minimum_commission,
            ),
            journal_path=root / "operations.sqlite",
        )

    def recover(self, *, actor: str) -> None:
        """Explicit operator action only. Uncertain research events cannot resume."""
        if not actor.strip():
            raise ValueError("human actor required")
        if self.db.execute("SELECT 1 FROM sessions WHERE status='PENDING'").fetchone():
            raise ValueError("uncertain forward event requires manual reconciliation")
        self.bundle.reconcile_and_recover(actor=actor)
        self.bundle.runtime.start(actor=actor)

    def freeze(self, target: ForwardTarget, *, now: datetime | None = None) -> str:
        stamp = now or datetime.now(UTC)
        _time(stamp)
        identity = target.identity
        if target.strategy_id != self.strategy_id:
            raise ValueError("strategy mismatch")
        with self._lock:
            existing = self.db.execute(
                "SELECT payload FROM targets WHERE id=?", (identity,)
            ).fetchone()
            if existing:
                return identity
            if not target.generated_at <= stamp < target.execution_at:
                raise ValueError("target must be frozen prospectively before execution")
            self.db.execute(
                "INSERT INTO targets VALUES (?, ?, ?, ?)",
                (
                    identity,
                    target.execution_at.isoformat(),
                    _json(asdict(target)),
                    stamp.isoformat(),
                ),
            )
        return identity

    def observe(
        self, target: ForwardTarget, session: ObservedSession, *, now: datetime | None = None
    ) -> dict[str, Any]:
        stamp = now or datetime.now(UTC)
        session.validate(stamp)
        identity = target.identity
        if target.strategy_id != self.strategy_id or target.execution_at != session.open_at:
            raise ValueError("target execution session mismatch")
        fingerprint = sha256(_json(asdict(session)).encode()).hexdigest()
        with self._lock:
            frozen = self.db.execute(
                "SELECT payload FROM targets WHERE id=?", (identity,)
            ).fetchone()
            if frozen is None or frozen[0] != _json(asdict(target)):
                raise ValueError("target was not frozen before execution")
            old = self.db.execute(
                "SELECT target_id,input_hash,status,result FROM sessions WHERE day=?",
                (session.trading_day.isoformat(),),
            ).fetchone()
            if old:
                if old[:2] != (identity, fingerprint):
                    raise ValueError("immutable session conflict")
                if old[2] != "COMPLETE":
                    self.bundle.report_incident("uncertain forward session")
                    raise ValueError("uncertain forward event requires manual reconciliation")
                return json.loads(old[3])  # type: ignore[no-any-return]
            previous = self.db.execute(
                "SELECT day,result,status FROM sessions ORDER BY day DESC LIMIT 1"
            ).fetchone()
            if previous and previous[2] != "COMPLETE":
                self.bundle.report_incident("previous forward event remains uncertain")
                raise ValueError("uncertain previous event requires manual reconciliation")
            if previous and previous[0] != session.previous_session.isoformat():
                raise ValueError("missing or out-of-order observed session")
            if (
                previous
                and self.mode is RuntimeMode.PAPER
                and json.loads(previous[1])["net_asset_value"] is None
            ):
                raise ValueError("unvalued previous session requires manual reconciliation")
            if self.mode is RuntimeMode.PAPER:
                # A healthy running process may renew its short-lived readiness
                # attestation. This never changes HALTED/PAUSED state or starts it.
                if (
                    self.bundle.runtime.status.running
                    and self.bundle.risk.state is KillState.NORMAL
                ):
                    self.bundle.reconcile_and_recover(actor="forward-health-check")
                self.bundle.runtime.ensure_submission_allowed()
            self.db.execute(
                "INSERT INTO sessions VALUES (?,?,?,'PENDING',NULL,?)",
                (session.trading_day.isoformat(), identity, fingerprint, _json(asdict(session))),
            )
            try:
                result = self._execute(target, session, previous)
                encoded = _json(result)
                self.db.execute(
                    "UPDATE sessions SET status='COMPLETE',result=? WHERE day=?",
                    (encoded, session.trading_day.isoformat()),
                )
                return json.loads(encoded)  # type: ignore[no-any-return]
            except BaseException:
                self.bundle.report_incident(
                    "forward event incomplete; human reconciliation required"
                )
                raise

    def _execute(
        self, target: ForwardTarget, session: ObservedSession, previous: tuple[Any, ...] | None
    ) -> dict[str, Any]:
        account = self.bundle.ledger.account
        holdings = {s: p.quantity for s, p in account.positions.items() if p.quantity}
        symbols = set(holdings) | set(target.weights)
        blocked = {
            s: (
                session.securities[s].block_reason()
                if s in session.securities
                else "MISSING_MARKET"
            )
            for s in sorted(symbols)
        }
        blocked = {s: reason for s, reason in blocked.items() if reason}
        # Unknown/corporate-action held marks prohibit valuation and all new risk.
        invalid_held = any(
            s not in session.securities
            or session.securities[s].corporate_action_clear is not True
            or not session.securities[s].raw_open.is_finite()
            or session.securities[s].raw_open <= 0
            or not session.securities[s].raw_close.is_finite()
            or session.securities[s].raw_close <= 0
            or session.securities[s].block_reason() == "INVALID_TRADING_CONSTRAINTS"
            for s in holdings
        )
        result: dict[str, Any] = {
            "schema_version": 1,
            "mode": self.mode.value,
            "strategy_id": self.strategy_id,
            "target_id": target.identity,
            "signal_id": target.signal_id,
            "data_version": target.data_version,
            "source_revision": target.source_revision,
            "session": session.trading_day.isoformat(),
            "observed_at": session.observed_at,
            "market_evidence": session.market_evidence,
            "calendar_evidence": session.calendar_evidence,
            "target_weights": target.weights,
            "target_quantities": {},
            "orders": [],
            "fills": [],
            "holdings": holdings,
            "cash": account.cash,
            "net_asset_value": None,
            "forward_return": None,
            "turnover": None,
            "blocked": blocked,
            "LIVE_TRADING_ENABLED": False,
            "execution_kind": "SIMULATED_END_OF_DAY_OPEN_PRICE",
            "limitations": list(target.limitations)
            + [
                "Daily open simulation has no auction queue, slippage or liquidity fill proof.",
                "No certified historical PIT or historical available_at is implied.",
                "Corporate actions require reconciliation; adjusted-price fills are prohibited.",
            ],
        }
        if self.mode is RuntimeMode.SHADOW:
            result.update(
                status="SHADOW_OBSERVED",
                execution_kind="NO_EXECUTION",
                cash=None,
                holdings={},
                limitations=list(result["limitations"])
                + ["SHADOW has no fills, holdings, portfolio return or execution admission."],
            )
            return result
        if invalid_held:
            self.bundle.report_incident("held corporate action or mark requires reconciliation")
            result["status"] = "HALTED_UNVALUED"
            return result
        equity = account.cash + sum(
            (session.securities[s].raw_open * q for s, q in holdings.items()), Decimal(0)
        )
        targets = dict(holdings)
        for s in sorted(symbols - set(blocked)):
            security = session.securities[s]
            targets[s] = (
                int(
                    (
                        equity
                        * target.weights.get(s, Decimal(0))
                        / (security.raw_open * security.lot_size)
                    ).to_integral_value(rounding=ROUND_DOWN)
                )
                * security.lot_size
            )
        result["target_quantities"] = targets
        turnover = Decimal(0)
        # Sell before buy. Existing service enforces cash, lot, T+1 and gross limits.
        for s in sorted(targets, key=lambda name: (targets[name] - holdings.get(name, 0), name)):
            delta = targets[s] - holdings.get(s, 0)
            if not delta or s in blocked:
                continue
            security = session.securities[s]
            side = Side.BUY if delta > 0 else Side.SELL
            assert security.upper_limit is not None and security.lower_limit is not None
            if (side is Side.BUY and security.raw_open >= security.upper_limit) or (
                side is Side.SELL and security.raw_open <= security.lower_limit
            ):
                blocked[s] = "LIMIT_UP_BUY_BLOCKED" if delta > 0 else "LIMIT_DOWN_SELL_BLOCKED"
                continue
            quantity = abs(delta)
            assert security.previous_session_volume is not None
            if quantity > security.previous_session_volume // 100:
                blocked[s] = "ONE_PERCENT_PREVIOUS_SESSION_VOLUME_LIMIT"
                continue
            request = OrderRequest(
                f"FORWARD:{target.identity}:{s}",
                self.strategy_id,
                self.strategy_id,
                s,
                side,
                security.raw_open,
                quantity,
                session.observed_at,
                session.trading_day,
                session.next_session if side is Side.BUY else None,
            )
            instruments = {
                name: Instrument(name, row.exchange, "STOCK", row.lot_size, row.price_tick)
                for name, row in session.securities.items()
                if not row.block_reason()
            }
            prices = {name: row.raw_open for name, row in session.securities.items()}
            context = RiskContext(
                account,
                instruments,
                prices,
                session.open_at,
                True,
                self.bundle.metrics.reconciliation_status == "OK",
                session.observed_at,
                sellable_quantities={
                    s: self.bundle.sellability.sellable_quantity(s, session.trading_day)
                },
            )
            order, _ = self.bundle.service.submit(request, context, now=session.observed_at)
            result["orders"].append(
                {
                    "symbol": s,
                    "side": side.value,
                    "quantity": quantity,
                    "status": order.status.value,
                    "reason": order.rejection_reason,
                    "order_id": order.internal_order_id,
                }
            )
            if order.status is OrderStatus.ACCEPTED:
                trade, _ = self.bundle.gateway.fill(
                    order,
                    fill_key=f"FORWARD-FILL:{target.identity}:{s}",
                    price=security.raw_open,
                    quantity=quantity,
                    now=session.observed_at,
                )
                result["fills"].append(asdict(trade))
                result["orders"][-1]["status"] = order.status.value
                turnover += trade.price * trade.quantity
        nav = account.cash + sum(
            (
                session.securities[s].raw_close * p.quantity
                for s, p in account.positions.items()
                if p.quantity
            ),
            Decimal(0),
        )
        prior_nav = (
            self.opening_cash
            if previous is None
            else Decimal(str(json.loads(previous[1])["net_asset_value"]))
        )
        result.update(
            status="PAPER_OBSERVED",
            holdings={s: p.quantity for s, p in account.positions.items() if p.quantity},
            cash=account.cash,
            net_asset_value=nav,
            forward_return=nav / prior_nav - 1,
            turnover=turnover / equity,
        )
        return result
