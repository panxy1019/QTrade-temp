"""Read-only acceptance of prospective books on a genuinely completed trading day."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, date, datetime, time
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from qtrade.domain import RuntimeMode
from qtrade.market_data.akshare import SHANGHAI
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.forward_publication import validate_forward_publication
from qtrade.research.model_registry import checked_document, checked_identity
from qtrade.research.signal_baselines import STRATEGIES
from scripts.atomic_json import write_json_immutable
from scripts.qlib_forward_paper_adapter import observed_session, public_book_summary
from scripts.run_qlib_forward_paper import load_session, load_target

REPO = Path(__file__).resolve().parents[1]
STOCK_STRATEGIES = tuple(name for name in STRATEGIES if name != "etf_positive_trend20")


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)


def document(path: Path, maximum: int = 2_000_000) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise ValueError("FORWARD_AUDIT_FILE_BOUND_OR_PATH_INVALID")
    value: dict[str, Any] = json.loads(path.read_bytes())
    checked_document(value)
    if any(type(value.get(k)) is not type(v) for k, v in AUTHORITY.items()):
        raise ValueError("FORWARD_AUDIT_AUTHORITY_TYPE_INVALID")
    return value


def readonly(path: Path) -> sqlite3.Connection:
    if path.is_symlink():
        raise ValueError("FORWARD_AUDIT_SQLITE_PATH_INVALID")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    return connection


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError("FORWARD_AUDIT_" + code)


def decimal(value: Any) -> Decimal:
    require(isinstance(value, str), "DECIMAL_STRING_REQUIRED")
    result = Decimal(value)
    require(result.is_finite(), "FINITE_DECIMAL_REQUIRED")
    return result


def check_accounting(
    result: dict[str, Any],
    previous: dict[str, Any] | None,
    config: dict[str, Any],
    market: Any,
    operations: sqlite3.Connection,
) -> None:
    """Reconcile receipt economics and durable fill effects without executing orders."""
    if result["mode"] == "SHADOW":
        require(
            not any(result[k] for k in ("orders", "fills", "holdings", "target_quantities"))
            and all(
                result[k] is None for k in ("cash", "net_asset_value", "forward_return", "turnover")
            )
            and result["execution_kind"] == "NO_EXECUTION",
            "SHADOW_EXECUTION_FORBIDDEN",
        )
        require(
            operations.execute("SELECT 1 FROM fill_journal LIMIT 1").fetchone() is None,
            "SHADOW_DURABLE_FILL_FORBIDDEN",
        )
        return
    require(
        operations.execute("SELECT 1 FROM fill_journal WHERE state!='APPLIED' LIMIT 1").fetchone()
        is None,
        "UNCERTAIN_OPERATIONAL_FILL",
    )
    require(result["status"] == "PAPER_OBSERVED", "PAPER_SESSION_UNVALUED")
    cash = decimal(previous["cash"] if previous else config["cash"])
    holdings = dict(previous["holdings"]) if previous else {}
    opening_equity = cash + sum(
        (market.securities[s].raw_open * q for s, q in holdings.items()), Decimal(0)
    )
    turnover = Decimal(0)
    for row in result["orders"] + result["fills"]:
        security = market.securities.get(row["symbol"])
        require(
            security is not None and security.block_reason() is None,
            "EXECUTION_WITH_UNKNOWN_OR_BLOCKED_CONSTRAINTS",
        )
    fills = operations.execute(
        "SELECT fill_key,state,trade_payload FROM fill_journal WHERE fill_key LIKE ?",
        ("FORWARD-FILL:" + result["target_id"] + ":%",),
    ).fetchall()
    require(len(fills) == len(result["fills"]), "DURABLE_FILL_COUNT_MISMATCH")
    durable = {row["fill_key"]: row for row in fills}
    for fill in result["fills"]:
        symbol, quantity = fill["symbol"], fill["quantity"]
        security = market.securities[symbol]
        require(
            type(quantity) is int and quantity > 0 and quantity % security.lot_size == 0,
            "FILL_QUANTITY_INVALID",
        )
        require(
            security.previous_session_volume is not None
            and quantity <= security.previous_session_volume // 100,
            "FILL_LIQUIDITY_INVALID",
        )
        require(
            decimal(fill["price"]) == security.raw_open
            and datetime.fromisoformat(fill["timestamp"]) == market.observed_at,
            "FILL_PRICE_OR_CLOCK_MISMATCH",
        )
        stored = durable.get("FORWARD-FILL:" + result["target_id"] + ":" + symbol)
        expected = {
            "trade_id": fill["trade_id"],
            "order_id": fill["internal_order_id"],
            "instrument": symbol,
            "side": fill["side"],
            "price": fill["price"],
            "quantity": quantity,
            "fee": fill["fee"],
            "timestamp": datetime.fromisoformat(fill["timestamp"]).isoformat(),
        }
        require(
            stored is not None
            and stored["state"] == "APPLIED"
            and json.loads(stored["trade_payload"]) == expected,
            "DURABLE_FILL_MISMATCH",
        )
        amount, fee = decimal(fill["price"]) * quantity, decimal(fill["fee"])
        require(fee >= 0 and fill["side"] in {"BUY", "SELL"}, "FILL_SIDE_OR_FEE_INVALID")
        direction = 1 if fill["side"] == "BUY" else -1
        holdings[symbol] = holdings.get(symbol, 0) + direction * quantity
        cash -= direction * amount + fee
        turnover += amount
    holdings = {s: q for s, q in holdings.items() if q}
    require(
        cash >= 0
        and all(type(q) is int and q > 0 for q in holdings.values())
        and result["holdings"] == holdings
        and decimal(result["cash"]) == cash,
        "CASH_OR_HOLDINGS_RECONCILIATION_FAILED",
    )
    nav = cash + sum((market.securities[s].raw_close * q for s, q in holdings.items()), Decimal(0))
    prior_nav = decimal(previous["net_asset_value"] if previous else config["cash"])
    require(
        decimal(result["net_asset_value"]) == nav
        and decimal(result["forward_return"]) == nav / prior_nav - 1
        and decimal(result["turnover"]) == turnover / opening_equity,
        "VALUATION_OR_RETURN_RECONCILIATION_FAILED",
    )


def audit_book(
    root: Path,
    lake: Path,
    identity: str,
    config: dict[str, Any],
    public: dict[str, Any] | None,
    day: date,
    calendar: Calendar,
    now: datetime,
    replay_cache: dict[str, Any],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "book_id": identity,
        "strategy_id": config["strategy_id"],
        "mode": config["mode"],
        "target_id": None,
        "session_sha256": None,
        "market_evidence": None,
        "failures": [],
        "accepted": False,
    }
    failures = row["failures"]
    try:
        checked_identity(identity)
        require(signed(config)["evidence_sha256"] == identity, "BOOK_IDENTITY_MISMATCH")
        if public is None:
            failures.append("FORWARD_AUDIT_PUBLISHED_BOOK_MISSING")
        elif config["mode"] == "PAPER" and (
            public["risk_state"] != "NORMAL"
            or public["running"] is not True
            or public["recovery_required"]
        ):
            failures.append("FORWARD_AUDIT_PAPER_RUNTIME_NOT_READY")
        if (
            public is not None
            and not 0
            <= (now - datetime.fromisoformat(public["generated_at"])).total_seconds()
            <= 720
        ):
            failures.append("FORWARD_AUDIT_PUBLIC_BOOK_STALE")
        book_root = root / identity
        require(not book_root.is_symlink(), "BOOK_DIRECTORY_SYMLINK")
        with (
            closing(readonly(book_root / "forward.sqlite")) as db,
            closing(readonly(book_root / "operations.sqlite")) as operations,
        ):
            stored_config = db.execute("SELECT payload FROM config WHERE id=1").fetchone()
            require(stored_config is not None, "ACCOUNT_CONFIGURATION_MISSING")
            account_config = json.loads(stored_config[0])
            require(
                account_config["strategy"] == config["strategy_id"]
                and account_config["mode"] == config["mode"]
                and account_config["cash"] == config["cash"]
                and account_config["fees"]["version"] == config["fee_version"],
                "ACCOUNT_CONFIGURATION_MISMATCH",
            )
            if config["mode"] == "PAPER":
                control = operations.execute(
                    "SELECT event_type,timestamp FROM audit_events "
                    "WHERE service='control' ORDER BY sequence DESC LIMIT 1"
                ).fetchone()
                started = operations.execute(
                    "SELECT event_type FROM audit_events "
                    "WHERE service='control' AND event_type IN "
                    "('SYSTEM_START','SYSTEM_STOP','KILL_SWITCH') ORDER BY sequence DESC LIMIT 1"
                ).fetchone()
                if (
                    control is None
                    or control[0] not in {"SYSTEM_START", "SYSTEM_RESUME"}
                    or started is None
                    or started[0] != "SYSTEM_START"
                ):
                    failures.append("FORWARD_AUDIT_LOCAL_PAPER_NOT_RUNNING")
                else:
                    require(datetime.fromisoformat(control[1]) <= now, "FUTURE_CONTROL_EVENT")
            session = db.execute(
                "SELECT * FROM sessions WHERE day=?", (day.isoformat(),)
            ).fetchone()
            require(session is not None, "OBSERVED_SESSION_MISSING")
            require(
                session["status"] == "COMPLETE" and session["result"] is not None,
                "SESSION_PENDING_OR_INCOMPLETE",
            )
            target_row = db.execute(
                "SELECT * FROM targets WHERE id=?", (session["target_id"],)
            ).fetchone()
            require(target_row is not None, "FROZEN_TARGET_MISSING")
            target = load_target(json.loads(target_row["payload"]))
            open_at = datetime.combine(day, time(9, 30), SHANGHAI)
            frozen = datetime.fromisoformat(target_row["frozen_at"])
            require(
                target.identity == target_row["id"] == session["target_id"]
                and target.strategy_id == config["strategy_id"]
                and target.execution_at == open_at
                and datetime.fromisoformat(target_row["execution_at"]) == open_at
                and frozen.tzinfo is not None
                and target.generated_at <= frozen < open_at,
                "TARGET_NOT_PROSPECTIVE_OR_IDENTITY_CHANGED",
            )
            row["target_id"] = target.identity
            admission = document(book_root / "admissions" / (target.identity + ".json"))
            verified = datetime.fromisoformat(admission["verified_at"])
            archive = admission["archive"]
            require(
                admission["target_id"] == target.identity
                and verified.tzinfo is not None
                and frozen <= verified < open_at
                and archive["artifact_id"] == target.signal_id
                and archive["strategy_id"] == target.strategy_id,
                "ADMISSION_CONTEXT_MISMATCH",
            )
            for key in ("manifest_sha256", "data_event_id"):
                checked_identity(archive[key])
            if target.strategy_id.startswith("approved_model_"):
                checked_identity(archive["approval_sha256"])
                require(
                    archive["kind"] == "approved-prediction"
                    and target.strategy_id == "approved_model_" + archive["model_id"],
                    "APPROVED_MODEL_ADMISSION_MISMATCH",
                )
            else:
                require(
                    archive["kind"] == "signal-family" and archive["model_id"] is None,
                    "SIGNAL_ADMISSION_MISMATCH",
                )
            evidence = document(book_root / "market-inputs" / (day.isoformat() + ".json"))
            prior = db.execute(
                "SELECT day,status,result FROM sessions WHERE day<? ORDER BY day DESC LIMIT 1",
                (day.isoformat(),),
            ).fetchone()
            previous = None
            if prior is not None:
                require(
                    prior["status"] == "COMPLETE" and prior["result"] is not None,
                    "PREVIOUS_SESSION_INCOMPLETE",
                )
                previous = json.loads(prior["result"])
            expected_instruments = sorted(
                set(target.weights) | set(previous["holdings"] if previous else {})
            )
            require(
                evidence["day"] == day.isoformat()
                and evidence["requested"] == expected_instruments,
                "MARKET_INPUT_SCOPE_MISMATCH",
            )
            fingerprint = evidence["evidence_sha256"]
            if fingerprint not in replay_cache:
                previous_evidence = evidence.get("previous_session_volume_evidence")
                replay_cache[fingerprint] = observed_session(
                    lake,
                    [r["batch_id"] for r in evidence["receipts"]],
                    day=day,
                    instruments=evidence["requested"],
                    calendar=calendar,
                    now=now,
                    previous_batch_ids=[r["batch_id"] for r in previous_evidence["receipts"]]
                    if previous_evidence
                    else None,
                )
            market, replayed = replay_cache[fingerprint]
            require(replayed == evidence, "RAW_MARKET_REPLAY_MISMATCH")
            stored_market = load_session(json.loads(session["input_payload"]))
            require(
                canonical(asdict(market)) == canonical(asdict(stored_market))
                and sha256(canonical(asdict(market)).encode()).hexdigest() == session["input_hash"],
                "SESSION_INPUT_HASH_OR_RAW_REPLAY_MISMATCH",
            )
            require(
                prior is None or prior["day"] == market.previous_session.isoformat(),
                "MISSING_PREVIOUS_TRADING_SESSION",
            )
            result = json.loads(session["result"])
            require(
                all(
                    result[k] == v
                    for k, v in {
                        "mode": config["mode"],
                        "strategy_id": target.strategy_id,
                        "target_id": target.identity,
                        "signal_id": target.signal_id,
                        "data_version": target.data_version,
                        "source_revision": target.source_revision,
                        "session": day.isoformat(),
                        "market_evidence": market.market_evidence,
                        "calendar_evidence": market.calendar_evidence,
                    }.items()
                )
                and result["LIVE_TRADING_ENABLED"] is False
                and datetime.fromisoformat(result["observed_at"]) == market.observed_at
                and result["target_weights"] == {s: str(w) for s, w in target.weights.items()},
                "RECEIPT_CONTEXT_MISMATCH",
            )
            for symbol in expected_instruments:
                security = market.securities.get(symbol)
                blocker = security.block_reason() if security else "MISSING_MARKET"
                require(
                    blocker is None or result["blocked"].get(symbol) == blocker,
                    "UNKNOWN_CONSTRAINT_BLOCKER_MISSING",
                )
            require(
                result["status"]
                == ("SHADOW_OBSERVED" if config["mode"] == "SHADOW" else "PAPER_OBSERVED"),
                "OBSERVED_MODE_STATE_INVALID",
            )
            check_accounting(result, previous, config, market, operations)
            row["session_sha256"] = sha256(canonical(result).encode()).hexdigest()
            row["market_evidence"] = fingerprint
            row["observed_security_count"] = len(evidence["observed"])
            row["missing_security_count"] = len(evidence["missing"])
            row["blocked_count"] = len(result["blocked"])
            row["fill_count"] = len(result["fills"])
            if public is not None:
                # The projection uses no runtime constructor or journal writer. Volatile
                # runtime state is checked independently above, not inferred by reopening.
                view: Any = SimpleNamespace(
                    db=db,
                    strategy_id=config["strategy_id"],
                    mode=RuntimeMode(config["mode"]),
                    bundle=SimpleNamespace(
                        runtime=SimpleNamespace(status=SimpleNamespace(running=public["running"])),
                        risk=SimpleNamespace(state=SimpleNamespace(value=public["risk_state"])),
                    ),
                )
                latest_link = document(book_root / "archive-link.json")["archive"]
                projected = public_book_summary(view, archive_link=latest_link)
                require(public["archive"] == projected["archive"], "PUBLIC_ARCHIVE_MISMATCH")
                published_session = next(
                    (s for s in public["sessions"] if s["day"] == day.isoformat()), None
                )
                expected_session = next(
                    (s for s in projected["sessions"] if s["day"] == day.isoformat()), None
                )
                published_target = next(
                    (t for t in public["frozen_targets"] if t["target_id"] == target.identity), None
                )
                expected_target = next(
                    (t for t in projected["frozen_targets"] if t["target_id"] == target.identity),
                    None,
                )
                require(
                    published_session is not None and published_session == expected_session,
                    "PUBLIC_SESSION_MISSING_OR_CHANGED",
                )
                require(
                    published_target is not None and published_target == expected_target,
                    "PUBLIC_TARGET_MISSING_OR_CHANGED",
                )
    except Exception as exc:
        message = str(exc)
        failures.append(
            message
            if message.startswith("FORWARD_AUDIT_")
            else "FORWARD_AUDIT_BOOK_EVIDENCE_INVALID"
        )
    row["accepted"] = not failures
    return row


def audit_transition(
    base: Path,
    local_root: Path,
    publication: dict[str, Any],
    day: date,
    approved_model_id: str,
    calendar: Calendar,
    *,
    now: datetime,
) -> dict[str, Any]:
    checked_identity(approved_model_id)
    require(now.tzinfo is not None, "AWARE_CLOCK_REQUIRED")
    require(
        os.environ.get("LIVE_TRADING_ENABLED", "false").lower() == "false", "LIVE_DISABLED_REQUIRED"
    )
    expected = {
        (strategy, mode)
        for strategy in (*STOCK_STRATEGIES, "approved_model_" + approved_model_id)
        for mode in ("PAPER", "SHADOW")
    }
    failures: list[str] = []
    close_at = datetime.combine(day, time(15), SHANGHAI)
    if not calendar.is_open(day) or now < close_at:
        failures.append("FORWARD_AUDIT_REAL_SESSION_NOT_COMPLETE")
    public = {}
    try:
        validate_forward_publication(publication, now=now)
        for key in ("heartbeat_at", "generated_at"):
            age = (now - datetime.fromisoformat(publication[key])).total_seconds()
            require(0 <= age <= 720, "PUBLICATION_NOT_FRESH")
        require(
            publication["state"] == "RUNNING" and not publication["errors"],
            "PUBLIC_SERVICE_DEGRADED_OR_STOPPED",
        )
    except Exception:
        failures.append("FORWARD_AUDIT_PUBLICATION_INVALID_STALE_OR_DEGRADED")
    # Even degraded valid documents can reveal which specific PAPER book is paused.
    try:
        validate_forward_publication(publication, now=now)
        public = {(b["strategy_id"], b["mode"]): b for b in publication["books"]}
    except Exception:
        pass
    if set(public) != expected:
        failures.append("FORWARD_AUDIT_PUBLIC_BOOK_COVERAGE_MISMATCH")
    local = {}
    try:
        roster = document(local_root / "roster.json")["books"]
        require(isinstance(roster, dict) and len(roster) <= 32, "ROSTER_BOUND_INVALID")
        local = {(c["strategy_id"], c["mode"]): (identity, c) for identity, c in roster.items()}
        if set(local) != expected or len(local) != len(roster):
            failures.append("FORWARD_AUDIT_LOCAL_BOOK_COVERAGE_MISMATCH")
    except Exception:
        failures.append("FORWARD_AUDIT_LOCAL_ROSTER_INVALID")
    books = []
    cache: dict[str, Any] = {}
    for strategy, mode in sorted(expected):
        if (strategy, mode) not in local:
            books.append(
                {
                    "book_id": None,
                    "strategy_id": strategy,
                    "mode": mode,
                    "accepted": False,
                    "failures": ["FORWARD_AUDIT_LOCAL_BOOK_MISSING"],
                }
            )
            continue
        identity, config = local[(strategy, mode)]
        books.append(
            audit_book(
                local_root,
                base / "unified-data/lake",
                identity,
                config,
                public.get((strategy, mode)),
                day,
                calendar,
                now,
                cache,
            )
        )
    accepted = not failures and all(b["accepted"] for b in books)
    return signed(
        {
            **AUTHORITY,
            "schema_version": "qtrade.forward-transition-audit.v1",
            "audited_at": now.isoformat(),
            "day": day.isoformat(),
            "state": "PASSED" if accepted else "BLOCKED",
            "forward_book_transition_accepted": accepted,
            "expected_book_count": len(expected),
            "publication_sha256": publication.get("evidence_sha256"),
            "failures": failures,
            "books": books,
            "whole_step1_accepted": False,
            "historical_pit_certified": False,
            "browser_verified": False,
            "limitations": [
                "Only the specified day, local books and supplied publication are audited.",
                "Source/model/version generation acceptance remains a separate immutable audit.",
                "Unknown constraints must block PAPER orders; zero orders is a valid observation.",
                "SHADOW has no fills or portfolio returns; ETFs are outside this stock adapter.",
                "Hashes prove integrity, not external timestamps or independent PIT certification.",
            ],
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "local-root", "published-forward", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--approved-model-id", required=True)
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    # No --now option and no book constructors: production acceptance uses wall time.
    result = audit_transition(
        args.base,
        args.local_root,
        document(args.published_forward),
        args.day,
        args.approved_model_id,
        Calendar(REPO / "configs/observer_calendar_2026.json"),
        now=datetime.now(UTC),
    )
    require(
        not args.output.resolve().is_relative_to(args.local_root.resolve())
        and not args.output.resolve().is_relative_to((args.base / "unified-data/lake").resolve()),
        "OUTPUT_MUST_BE_SEPARATE",
    )
    write_json_immutable(args.output, result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
