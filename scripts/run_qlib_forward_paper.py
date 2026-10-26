"""Operator CLI for prospective PAPER/SHADOW admission and observed-day processing."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from qtrade.domain import RuntimeMode
from qtrade.execution import FeeSchedule
from qtrade.research.forward_paper import (
    ForwardPaperBook,
    ForwardTarget,
    ObservedSecurity,
    ObservedSession,
)


def load_target(payload: dict[str, Any]) -> ForwardTarget:
    return ForwardTarget(
        strategy_id=payload["strategy_id"],
        signal_id=payload["signal_id"],
        data_version=payload["data_version"],
        source_revision=payload["source_revision"],
        generated_at=datetime.fromisoformat(payload["generated_at"]),
        execution_at=datetime.fromisoformat(payload["execution_at"]),
        weights={symbol: Decimal(str(value)) for symbol, value in payload["weights"].items()},
        limitations=tuple(payload["limitations"]),
    )


def load_session(payload: dict[str, Any]) -> ObservedSession:
    securities = {}
    for symbol, raw in payload["securities"].items():
        fields = dict(raw)
        for name in ("raw_open", "raw_close", "price_tick", "upper_limit", "lower_limit"):
            fields[name] = None if fields[name] is None else Decimal(str(fields[name]))
        for name in ("suspended", "st", "corporate_action_clear"):
            if fields[name] is not None and type(fields[name]) is not bool:
                raise ValueError("constraint flags require explicit booleans or null")
        for name in ("volume", "lot_size"):
            if type(fields[name]) is not int:
                raise ValueError("volume and lot size require integers")
        if fields.get("previous_session_volume") is not None and type(
            fields["previous_session_volume"]
        ) is not int:
            raise ValueError("previous session volume requires integer or null")
        securities[symbol] = ObservedSecurity(**fields)
    return ObservedSession(
        trading_day=date.fromisoformat(payload["trading_day"]),
        previous_session=date.fromisoformat(payload["previous_session"]),
        next_session=date.fromisoformat(payload["next_session"]),
        open_at=datetime.fromisoformat(payload["open_at"]),
        close_at=datetime.fromisoformat(payload["close_at"]),
        observed_at=datetime.fromisoformat(payload["observed_at"]),
        calendar_evidence=payload["calendar_evidence"],
        market_evidence=payload["market_evidence"],
        securities=securities,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "observe", "status"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--session", type=Path)
    parser.add_argument("--recover-actor", help="Explicit human recovery, never set by a scheduler")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    fees = config["fees"]
    book = ForwardPaperBook(
        args.root,
        strategy_id=config["strategy_id"],
        mode=RuntimeMode(config["mode"]),
        opening_cash=Decimal(config["opening_cash"]),
        fees=FeeSchedule(
            fees["version"],
            Decimal(fees["commission_rate"]),
            Decimal(fees["sell_tax_rate"]),
            Decimal(fees["minimum_commission"]),
            source_url=fees["source_url"],
        ),
    )
    if args.recover_actor:
        book.recover(actor=args.recover_actor)
    if args.action == "status":
        result = {
            "mode": book.mode.value,
            "risk_state": book.bundle.risk.state.value,
            "running": book.bundle.runtime.status.running,
            "targets": book.db.execute("SELECT COUNT(*) FROM targets").fetchone()[0],
            "sessions": [
                dict(zip(("day", "status"), row, strict=True))
                for row in book.db.execute(
                    "SELECT day,status FROM sessions ORDER BY day DESC LIMIT 30"
                )
            ],
            "LIVE_TRADING_ENABLED": False,
        }
    else:
        if args.target is None:
            parser.error("--target is required")
        target = load_target(json.loads(args.target.read_text(encoding="utf-8")))
        if args.action == "freeze":
            result = {"target_id": book.freeze(target), "status": "FROZEN_BEFORE_EXECUTION"}
        else:
            if args.session is None:
                parser.error("--session is required")
            session = load_session(json.loads(args.session.read_text(encoding="utf-8")))
            result = book.observe(target, session)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False))


if __name__ == "__main__":
    main()
