"""Read-only proof of retired research models and closed, risk-free books."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY
from qtrade.research.model_registry import checked_document, checked_identity


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError("FORWARD_RETIREMENT_" + code)


@contextmanager
def readonly(path: Path) -> Iterator[sqlite3.Connection]:
    require(path.is_file() and not path.is_symlink(), "DATABASE_PATH_INVALID")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        yield connection


def retirement_event(database: Path, strategy: str, *, now: datetime) -> dict[str, Any] | None:
    """Resolve current registry state; an old retirement cannot retire a reactivated model."""
    if not strategy.startswith("approved_model_"):
        return None
    model = checked_identity(strategy.removeprefix("approved_model_"))
    require(now.tzinfo is not None, "AWARE_CLOCK_REQUIRED")
    with readonly(database) as connection:
        row = connection.execute(
            "SELECT state,activated_at,activation_count FROM models WHERE model_id=?", (model,)
        ).fetchone()
        active = connection.execute(
            "SELECT model_id FROM active_model WHERE singleton=1"
        ).fetchone()
        require(row is not None, "MODEL_MISSING")
        if row[0] == "ACTIVE":
            require(active is not None and active[0] == model, "ACTIVE_POINTER_CHANGED")
            return None
        require(
            row[0] == "RETIRED" and active is not None and active[0] != model,
            "MODEL_STATE_NOT_ELIGIBLE",
        )
        require(type(row[2]) is int and row[2] >= 1, "MODEL_NEVER_ACTIVATED")
        event = connection.execute(
            "SELECT sequence,at,payload FROM model_events "
            "WHERE model_id=? AND kind='RETIRED' ORDER BY sequence DESC LIMIT 1",
            (model,),
        ).fetchone()
    require(event is not None, "EVENT_MISSING")
    value: dict[str, Any] = json.loads(event[2])
    checked_document(value)
    require(all(type(value.get(k)) is type(v) for k, v in AUTHORITY.items()), "AUTHORITY_INVALID")
    activated, retired = datetime.fromisoformat(row[1]), datetime.fromisoformat(event[1])
    require(
        value["model_id"] == model
        and value["kind"] == "RETIRED"
        and value["at"] == event[1]
        and activated.tzinfo is not None
        and retired.tzinfo is not None
        and activated <= retired <= now,
        "EVENT_IDENTITY_OR_CLOCK",
    )
    checked_identity(value["details"]["replacement"])
    require(value["details"]["replacement"] != model, "INVALID_REPLACEMENT")
    return {"model_id": model, "event_sequence": event[0], "event": value}


def _rows(connection: sqlite3.Connection, table: str) -> list[Any]:
    # Callers supply constant table names only. Bound both rows and serialized payload.
    result = connection.execute("SELECT * FROM " + table + " ORDER BY rowid LIMIT 10001").fetchall()
    require(len(result) <= 10000, "ROW_BOUND")
    require(len(json.dumps(result, allow_nan=False).encode()) <= 16 * 1024 * 1024, "BYTE_BOUND")
    return result


def _hash(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _number(value: Any) -> Decimal:
    require(isinstance(value, str), "DECIMAL_STRING_REQUIRED")
    result = Decimal(value)
    require(result.is_finite() and result >= 0, "INVALID_ECONOMICS")
    return result


def closed_book_snapshot(
    root: Path,
    config: dict[str, Any],
    event: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Reconcile a stopped retired book. Holdings or uncertain work never disappear."""
    require(not root.is_symlink(), "BOOK_PATH_INVALID")
    require(config["strategy_id"] == "approved_model_" + event["model_id"], "BOOK_MODEL_CHANGED")
    retired = datetime.fromisoformat(event["event"]["at"])
    require(now.tzinfo is not None and retired <= now, "CLOCK_INVALID")
    with (
        readonly(root / "forward.sqlite") as db,
        readonly(root / "operations.sqlite") as operations,
    ):
        stored = json.loads(db.execute("SELECT payload FROM config WHERE id=1").fetchone()[0])
        require(
            stored["strategy"] == config["strategy_id"]
            and stored["mode"] == config["mode"]
            and stored["cash"] == config["cash"]
            and stored["fees"]["version"] == config["fee_version"],
            "CONFIG_CHANGED",
        )
        require(
            db.execute("SELECT 1 FROM sessions WHERE status!='COMPLETE' LIMIT 1").fetchone()
            is None,
            "PENDING_SESSION",
        )
        control = operations.execute(
            "SELECT sequence,event_type,timestamp FROM audit_events WHERE service='control' "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        require(
            control is not None and control[1] in {"SYSTEM_STOP", "KILL_SWITCH"}, "BOOK_NOT_STOPPED"
        )
        stopped = datetime.fromisoformat(control[2])
        require(stopped.tzinfo is not None and retired <= stopped <= now, "STOP_CLOCK_INVALID")
        fills = operations.execute(
            "SELECT state,trade_payload FROM fill_journal ORDER BY rowid LIMIT 10001"
        ).fetchall()
        require(len(fills) <= 10000, "FILL_BOUND")
        cash = _number(config["cash"])
        holdings: dict[str, int] = {}
        for state, raw in fills:
            require(state == "APPLIED" and isinstance(raw, str), "UNCERTAIN_FILL")
            fill = json.loads(raw)
            quantity = fill["quantity"]
            require(
                type(quantity) is int and quantity > 0 and fill["side"] in {"BUY", "SELL"},
                "FILL_INVALID",
            )
            stamp = datetime.fromisoformat(fill["timestamp"])
            require(stamp.tzinfo is not None and stamp <= retired, "EXECUTION_AFTER_RETIREMENT")
            price, fee = _number(fill["price"]), _number(fill["fee"])
            require(price > 0, "FILL_INVALID")
            direction = 1 if fill["side"] == "BUY" else -1
            symbol = fill["instrument"]
            holdings[symbol] = holdings.get(symbol, 0) + direction * quantity
            cash -= direction * price * quantity + fee
            require(cash >= 0 and holdings[symbol] >= 0, "FILL_RECONCILIATION_FAILED")
        require(not any(holdings.values()), "HELD_POSITIONS_REQUIRE_RECONCILIATION")
        if config["mode"] == "SHADOW":
            require(not fills, "SHADOW_FILL_FORBIDDEN")
        states = dict(operations.execute("SELECT state_key,payload FROM runtime_state"))
        require(not fills or {"account", "runtime"} <= set(states), "DURABLE_ACCOUNT_MISSING")
        for key, payload in states.items():
            value = json.loads(payload)
            if key == "account":
                require(
                    _number(value["cash"]) == cash
                    and all(
                        type(p["quantity"]) is int and p["quantity"] == 0
                        for p in value["positions"].values()
                    ),
                    "DURABLE_ACCOUNT_CHANGED",
                )
            elif key == "runtime":
                require(
                    not value["reservations"]
                    and all(
                        o["status"] in {"RISK_REJECTED", "FILLED", "CANCELLED", "REJECTED"}
                        for o in value["orders"]
                    )
                    and not any(p["quantity"] for p in value["sellability_lots"]),
                    "UNCERTAIN_RUNTIME_WORK",
                )
        last = db.execute("SELECT result FROM sessions ORDER BY day DESC LIMIT 1").fetchone()
        if last and config["mode"] == "PAPER":
            value = json.loads(last[0])
            require(
                value["status"] == "PAPER_OBSERVED"
                and _number(value["cash"]) == cash
                and not value["holdings"]
                and _number(value["net_asset_value"]) == cash,
                "LAST_SESSION_UNRECONCILED",
            )
        rows = {
            "forward:" + name: _hash(_rows(db, name)) for name in ("config", "targets", "sessions")
        }
        rows["operations:fill_journal"] = _hash(_rows(operations, "fill_journal"))
        rows["operations:runtime_state"] = _hash(_rows(operations, "runtime_state"))
        targets = db.execute("SELECT id,payload,frozen_at FROM targets LIMIT 10001").fetchall()
        require(len(targets) <= 10000, "TARGET_BOUND")
        for identity, raw, frozen_at in targets:
            checked_identity(identity)
            target = json.loads(raw)
            generated = datetime.fromisoformat(target["generated_at"])
            execution = datetime.fromisoformat(target["execution_at"])
            frozen = datetime.fromisoformat(frozen_at)
            require(
                _hash(target) == identity
                and target["strategy_id"] == config["strategy_id"]
                and generated.tzinfo is not None
                and execution.tzinfo is not None
                and frozen.tzinfo is not None
                and generated <= frozen < execution
                and frozen <= retired,
                "TARGET_IDENTITY_OR_CLOCK_CHANGED",
            )
            admission = root / "admissions" / (identity + ".json")
            require(
                admission.is_file()
                and not admission.is_symlink()
                and admission.stat().st_size <= 2_000_000,
                "ADMISSION_MISSING_OR_PATH",
            )
            proof = json.loads(admission.read_bytes())
            checked_document(proof)
            stamp = datetime.fromisoformat(proof["verified_at"])
            require(
                proof["target_id"] == identity
                and proof["archive"]["artifact_id"] == target["signal_id"]
                and proof["archive"]["model_id"] == event["model_id"]
                and stamp.tzinfo is not None
                and frozen <= stamp < execution
                and stamp <= retired,
                "ADMISSION_CHANGED",
            )
        pending_rows = db.execute(
            "SELECT t.id,t.execution_at FROM targets t LEFT JOIN sessions s ON s.target_id=t.id "
            "WHERE s.day IS NULL ORDER BY t.execution_at LIMIT 10001"
        ).fetchall()
        require(
            all(datetime.fromisoformat(r[1]) >= retired for r in pending_rows),
            "PRIOR_SESSION_REQUIRES_RECONCILIATION",
        )
        pending = [r[0] for r in pending_rows]
        require(len(pending) <= 10000, "TARGET_BOUND")
    admissions = sorted((root / "admissions").glob("*.json"))
    require(len(admissions) <= 10000 and not (root / "admissions").is_symlink(), "ADMISSION_BOUND")
    require({p.stem for p in admissions} == {r[0] for r in targets}, "ADMISSION_TARGET_COVERAGE")

    def digest(path: Path) -> str:
        require(
            path.is_file() and not path.is_symlink() and path.stat().st_size <= 2_000_000,
            "ARCHIVE_PATH_OR_BOUND",
        )
        raw = path.read_bytes()
        value = json.loads(raw)
        checked_document(value)
        require(
            all(type(value.get(k)) is type(v) for k, v in AUTHORITY.items()), "AUTHORITY_INVALID"
        )
        return sha256(raw).hexdigest()

    return {
        "model_id": event["model_id"],
        "model_retirement_sha256": event["event"]["evidence_sha256"],
        "retired_at": retired.isoformat(),
        "stop_event": list(control),
        "row_hashes": rows,
        "admission_hashes": {p.name: digest(p) for p in admissions},
        "archive_link_sha256": digest(root / "archive-link.json"),
        "unexecuted_targets_blocked_by_retirement": pending,
        "cash": str(cash) if config["mode"] == "PAPER" else None,
        "position_count": 0,
        "fill_count": len(fills),
        "scope": "READ_ONLY_RETIRED_BOOK_NOT_A_NEW_DAY_OBSERVATION",
    }
