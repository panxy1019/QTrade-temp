"""Independent frozen execution-day reader; current readiness has a separate scope.

The original strict auditor is unchanged. This fork reuses its raw market replay,
economics and public session/target projection, while checking PAPER control
history for the execution day rather than requiring it to be running now.
No book/runtime/registry constructors, inference, training or execution calls.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, date, datetime, time
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from qtrade.domain import RuntimeMode
from qtrade.market_data.akshare import SHANGHAI
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.forward_publication import validate_forward_publication
from qtrade.research.forward_retirement import retirement_event
from qtrade.research.model_registry import checked_document, checked_identity
from qtrade.research.prediction_registry import active_approval
from scripts.atomic_json import write_json_immutable
from scripts.audit_qlib_forward_transition import (
    STOCK_STRATEGIES,
    audit_retired_book,
    canonical,
    check_accounting,
    document,
    readonly,
    require,
)
from scripts.qlib_forward_paper_adapter import (
    observed_session,
    prediction_target,
    public_book_summary,
    signal_targets,
)
from scripts.run_qlib_forward_paper import load_session, load_target

REPO = Path(__file__).resolve().parents[1]
SCHEMA = "qtrade.frozen-execution-day-audit.v1"


def bounded_rows(db, sql, values=()):
    result = db.execute(sql + " LIMIT 10001", values).fetchall()
    require(len(result) <= 10000, "SCOPE_ROW_BOUND")
    return [dict(row) for row in result]


def clock(value, now):
    stamp = datetime.fromisoformat(value)
    require(stamp.tzinfo is not None and stamp <= now, "SCOPE_FUTURE_OR_NAIVE_CLOCK")
    return stamp


def checked(value):
    checked_document(value)
    require(
        all(type(value.get(k)) is type(v) for k, v in AUTHORITY.items()), "SCOPE_AUTHORITY_TYPE"
    )


def historical_controls(operations, config, day, now):
    if config["mode"] != "PAPER":
        return
    opening = datetime.combine(day, time(9, 30), SHANGHAI)
    closing_at = datetime.combine(day, time(15), SHANGHAI)
    events = bounded_rows(
        operations,
        "SELECT sequence,event_type,timestamp FROM audit_events "
        "WHERE service='control' ORDER BY sequence",
    )
    times = [clock(r["timestamp"], now) for r in events]
    require(times == sorted(times), "EXECUTION_DAY_CONTROL_CLOCK_ORDER")
    before_open = [r for r in events if clock(r["timestamp"], now) <= opening]
    require(
        before_open and before_open[-1]["event_type"] in {"SYSTEM_START", "SYSTEM_RESUME"},
        "EXECUTION_DAY_PAPER_NOT_STARTED_BEFORE_OPEN",
    )
    lifecycle = [
        r for r in before_open if r["event_type"] in {"SYSTEM_START", "SYSTEM_STOP", "KILL_SWITCH"}
    ]
    require(
        lifecycle and lifecycle[-1]["event_type"] == "SYSTEM_START",
        "EXECUTION_DAY_PAPER_START_CHAIN_INVALID",
    )
    require(
        not any(
            opening < clock(r["timestamp"], now) <= closing_at
            and r["event_type"] in {"SYSTEM_STOP", "KILL_SWITCH", "SYSTEM_HALT"}
            for r in events
        ),
        "EXECUTION_DAY_PAPER_STOPPED_DURING_SESSION",
    )


def approval_scope(
    model_database, day, frozen_model, frozen_approval, current_model, current_approval, now
):
    opening = datetime.combine(day, time(9, 30), SHANGHAI)
    closing_at = datetime.combine(day, time(15), SHANGHAI)
    with closing(readonly(model_database)) as db:
        current = active_approval(db)
        events = bounded_rows(
            db,
            "SELECT sequence,model_id,kind,at,payload FROM model_events "
            "WHERE kind='CONTROL_APPLIED' ORDER BY sequence",
        )
        models = bounded_rows(
            db,
            "SELECT * FROM models WHERE model_id IN (?,?) ORDER BY model_id",
            (frozen_model, current_model),
        )
    require(
        current is not None
        and current["model_id"] == current_model
        and current["evidence_sha256"] == current_approval,
        "EXPLICIT_CURRENT_APPROVAL_CHANGED",
    )
    verified = []
    for row in events:
        payload = json.loads(row["payload"])
        checked(payload)
        receipt = payload["details"]
        checked(receipt)
        require(
            payload["kind"] == row["kind"] == "CONTROL_APPLIED"
            and payload["model_id"] == row["model_id"] == receipt["model_id"]
            and payload["at"] == row["at"] == receipt["at"]
            and receipt["state"] == "APPLIED"
            and receipt["active_model"] == receipt["model_id"],
            "CONTROL_RECEIPT_BINDING",
        )
        verified.append((clock(receipt["at"], now), receipt))
    require([x[0] for x in verified] == sorted(x[0] for x in verified), "CONTROL_CLOCK_ORDER")
    before = [receipt for at, receipt in verified if at < opening]
    require(
        before
        and before[-1]["model_id"] == frozen_model
        and before[-1]["evidence_sha256"] == frozen_approval,
        "EXECUTION_DAY_APPROVAL_CHANGED",
    )
    require(not any(opening <= at <= closing_at for at, _ in verified), "INTRADAY_APPROVAL_CHANGE")
    if frozen_model != current_model:
        require(clock(current["at"], now) > closing_at, "NEW_MODEL_NOT_POST_CLOSE")
    return {
        "current_approval": current,
        "control_events": events,
        "model_rows": models,
        "execution_approval": before[-1],
    }


def verify_frozen_epoch(event, epoch_id, version_id, generated_at, now, commands=()):
    checked(event)
    require(
        event["evidence_sha256"] == epoch_id
        and event["kind"] in {"QLIB_DATA_READY", "QLIB_DATA_ROLLBACK"}
        and event["version_id"] == version_id
        and clock(
            event["admitted_at"] if event["kind"] == "QLIB_DATA_READY" else event["created_at"], now
        )
        <= generated_at,
        "FROZEN_DATA_EPOCH_CHANGED",
    )
    if event["kind"] == "QLIB_DATA_ROLLBACK":
        require(
            type(event.get("request_id")) is str and len(commands) == 1,
            "FROZEN_ROLLBACK_COMMAND_REQUIRED",
        )
        command = json.loads(commands[0]["command"])
        receipt = json.loads(commands[0]["receipt"])
        checked(receipt)
        require(
            receipt["state"] == "APPLIED"
            and receipt["event_id"] == epoch_id
            and receipt["version_id"] == version_id
            and receipt["request_id"] == event["request_id"]
            and receipt["command_sha256"] == signed(command)["evidence_sha256"],
            "FROZEN_ROLLBACK_RECEIPT_CHANGED",
        )


def verify_admitted_artifact(base, workflow_database, data_database, row, root, day, now):
    archive = row["admitted_archive"]
    identity = row["target_id"]
    with closing(readonly(root / row["book_id"] / "forward.sqlite")) as db:
        target_row = db.execute("SELECT payload FROM targets WHERE id=?", (identity,)).fetchone()
    target = load_target(json.loads(target_row[0]))
    artifact = checked_identity(archive["artifact_id"])
    if archive["kind"] == "approved-prediction":
        path = base / "online-research/control/predictions" / artifact / "output" / artifact
        require(not any(p.is_symlink() for p in (path, *path.parents)), "FROZEN_ARCHIVE_PATH_ALIAS")
        reproduced, link = prediction_target(
            path,
            active_model_id=archive["model_id"],
            data_version=target.data_version,
            source_revision=target.source_revision,
        )
    else:
        with closing(readonly(workflow_database)) as db:
            jobs = bounded_rows(
                db,
                "SELECT id,state,result FROM jobs WHERE "
                "json_extract(request,'$.kind')='QLIB_SIGNAL_JOB' AND "
                "json_extract(result,'$.signal_family_id')=?",
                (artifact,),
            )
        require(
            len(jobs) == 1 and jobs[0]["state"] == "COMPLETE",
            "FROZEN_SIGNAL_JOB_NOT_UNIQUE_COMPLETE",
        )
        job = jobs[0]
        require(
            len(job["id"]) == 32 and all(c in "0123456789abcdef" for c in job["id"]),
            "FROZEN_SIGNAL_JOB_ID",
        )
        path = base / "workflow/jobs" / job["id"] / "output" / artifact
        require(not any(p.is_symlink() for p in (path, *path.parents)), "FROZEN_ARCHIVE_PATH_ALIAS")
        matches = [
            (t, link)
            for t, link in signal_targets(
                path, data_version=target.data_version, source_revision=target.source_revision
            )
            if t.strategy_id == target.strategy_id
        ]
        require(len(matches) == 1, "FROZEN_SIGNAL_TARGET_NOT_UNIQUE")
        reproduced, link = matches[0]
    require(not any(p.is_symlink() for p in (path, *path.parents)), "FROZEN_ARCHIVE_PATH_ALIAS")
    require(
        reproduced.identity == identity and all(archive[k] == v for k, v in link.items()),
        "FROZEN_ARCHIVE_TARGET_CHANGED",
    )
    with closing(readonly(data_database)) as db:
        epochs = bounded_rows(
            db, "SELECT * FROM data_events WHERE event_id=?", (archive["data_event_id"],)
        )
    require(len(epochs) == 1, "FROZEN_DATA_EPOCH_MISSING")
    event = json.loads(epochs[0]["payload"])
    commands = []
    if event["kind"] == "QLIB_DATA_ROLLBACK":
        require(type(event.get("request_id")) is str, "FROZEN_ROLLBACK_COMMAND_REQUIRED")
        with closing(readonly(data_database)) as db:
            commands = bounded_rows(
                db,
                "SELECT * FROM data_rollback_commands WHERE request_id=?",
                (event["request_id"],),
            )
    verify_frozen_epoch(
        event, archive["data_event_id"], target.data_version, target.generated_at, now, commands
    )
    row["frozen_artifact_bytes_verified"] = True


def audit_execution_book(
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
            historical_controls(operations, config, day, now)
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
            # Compare aware instants, not only text: a changed SQLite schema or
            # alternate ISO spelling must not let a second slot replace the first.
            slots = bounded_rows(db, "SELECT id,execution_at FROM targets ORDER BY rowid")
            parsed = [(r["id"], datetime.fromisoformat(r["execution_at"])) for r in slots]
            require(all(at.tzinfo is not None for _, at in parsed), "NAIVE_EXECUTION_SLOT")
            matches = [identity for identity, at in parsed if at == open_at]
            require(matches == [target.identity], "FIRST_ADMITTED_EXECUTION_SLOT_NOT_UNIQUE")
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
            row["admission_sha256"] = admission["evidence_sha256"]
            row["admitted_archive"] = archive
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
                projected = public_book_summary(view, archive_link=archive)
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


def book_snapshot(root, roster):
    result = {}
    for identity in sorted(roster):
        checked_identity(identity)
        with closing(readonly(root / identity / "forward.sqlite")) as db:
            forward = {
                table: bounded_rows(db, "SELECT * FROM " + table + " ORDER BY rowid")
                for table in ("config", "targets", "sessions")
            }
        with closing(readonly(root / identity / "operations.sqlite")) as db:
            operations = {
                table: bounded_rows(db, "SELECT * FROM " + table + " ORDER BY rowid")
                for table in ("audit_events", "fill_journal")
            }
        result[identity] = {"forward": forward, "operations": operations}
    return result


def audit(
    base,
    root,
    model_database,
    workflow_database,
    data_database,
    publication,
    day,
    frozen_model,
    frozen_approval,
    current_model,
    current_approval,
    *,
    now=None,
    calendar=None,
):
    stamp = now or datetime.now(UTC)
    checks = {}
    errors = []
    books = []
    closures = []
    readiness_errors = []
    projection_errors = []
    report = {
        "frozen_execution_ledger_accepted": False,
        "frozen_execution_public_projection_accepted": False,
        "current_readiness_accepted": False,
    }
    try:
        require(stamp.tzinfo is not None, "AWARE_CLOCK_REQUIRED")
        require(
            os.environ.get("LIVE_TRADING_ENABLED", "false").lower() == "false",
            "LIVE_DISABLED_REQUIRED",
        )
        for identity in (frozen_model, frozen_approval, current_model, current_approval):
            checked_identity(identity)
        calendar = calendar or Calendar(REPO / "configs/observer_calendar_2026.json")
        require(
            calendar.is_open(day) and stamp >= datetime.combine(day, time(15), SHANGHAI),
            "REAL_EXECUTION_DAY_NOT_COMPLETE",
        )
        context = approval_scope(
            model_database,
            day,
            frozen_model,
            frozen_approval,
            current_model,
            current_approval,
            stamp,
        )
        roster_document = document(root / "roster.json")
        roster = roster_document["books"]
        require(type(roster) is dict and len(roster) <= 32, "ROSTER_BOUND_INVALID")
        before = book_snapshot(root, roster)
        local = {(c["strategy_id"], c["mode"]): (identity, c) for identity, c in roster.items()}
        require(len(local) == len(roster), "DUPLICATE_BOOK_CONFIG")
        required = {
            (strategy, mode)
            for strategy in (*STOCK_STRATEGIES, "approved_model_" + frozen_model)
            for mode in ("PAPER", "SHADOW")
        }
        require(required <= set(local), "FROZEN_EXECUTION_BOOK_MISSING")
        public = {}
        try:
            validate_forward_publication(publication, now=stamp)
            public = {(b["strategy_id"], b["mode"]): b for b in publication["books"]}
            require(len(public) == len(publication["books"]), "DUPLICATE_PUBLIC_BOOK")
            require(set(public) == set(local), "FULL_PUBLIC_ROSTER_COVERAGE_CHANGED")
            require(
                all(
                    0 <= (stamp - clock(publication[k], stamp)).total_seconds() <= 720
                    for k in ("heartbeat_at", "generated_at")
                ),
                "CURRENT_PUBLICATION_NOT_READY",
            )
        except (ValueError, KeyError, TypeError):
            projection_errors.append("FROZEN_SCOPE_PUBLIC_PROJECTION_INVALID_STALE_OR_INCOMPLETE")
            readiness_errors.append("FROZEN_SCOPE_CURRENT_PUBLICATION_INVALID_STALE_OR_DEGRADED")
        if publication.get("state") != "RUNNING" or publication.get("errors"):
            readiness_errors.append("CURRENT_SCOPE_PUBLIC_SERVICE_DEGRADED_OR_STOPPED")
        cache = {}
        for strategy, mode in sorted(required):
            identity, config = local[(strategy, mode)]
            # Private evidence is independent of today's volatile public runtime.
            row = audit_execution_book(
                root,
                base / "unified-data/lake",
                identity,
                config,
                None,
                day,
                calendar,
                stamp,
                cache,
            )
            if row["accepted"]:
                try:
                    if strategy == "approved_model_" + frozen_model:
                        require(
                            row["admitted_archive"]["approval_sha256"] == frozen_approval,
                            "FROZEN_MODEL_APPROVAL_BINDING_CHANGED",
                        )
                    verify_admitted_artifact(
                        base, workflow_database, data_database, row, root, day, stamp
                    )
                except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
                    row["accepted"] = False
                    row["failures"].append("FROZEN_SCOPE_ARTIFACT_REPLAY_FAILED")
            delivered = audit_execution_book(
                root,
                base / "unified-data/lake",
                identity,
                config,
                public.get((strategy, mode)),
                day,
                calendar,
                stamp,
                cache,
            )
            row["public_day_projection_matches"] = (
                bool(public.get((strategy, mode))) and delivered["accepted"]
            )
            books.append(row)
        checks["execution_day_frozen_approval"] = True
        # Account for all retained books. A post-close retired frozen model still
        # needs its observation above; closure never substitutes for that session.
        for (strategy, mode), (identity, config) in sorted(local.items()):
            if (strategy, mode) not in required and strategy != "approved_model_" + current_model:
                require(
                    strategy.startswith("approved_model_") and mode in {"PAPER", "SHADOW"},
                    "UNCLASSIFIED_RETAINED_BOOK",
                )
            event = (
                retirement_event(model_database, strategy, now=stamp)
                if strategy.startswith("approved_model_")
                else None
            )
            if event is not None:
                closures.append(
                    audit_retired_book(
                        root, identity, config, public.get((strategy, mode)), event, now=stamp
                    )
                )
            elif (strategy, mode) not in required:
                require(strategy == "approved_model_" + current_model, "UNCLASSIFIED_ACTIVE_BOOK")
        current_required = {
            (strategy, mode)
            for strategy in (*STOCK_STRATEGIES, "approved_model_" + current_model)
            for mode in ("PAPER", "SHADOW")
        }
        for pair in sorted(current_required):
            value = public.get(pair)
            if pair not in local or value is None:
                readiness_errors.append("CURRENT_SCOPE_BOOK_MISSING:" + pair[0] + ":" + pair[1])
                continue
            if pair[1] == "PAPER" and not (
                value["running"] is True
                and value["risk_state"] == "NORMAL"
                and value["recovery_required"] is False
            ):
                readiness_errors.append("CURRENT_SCOPE_PAPER_NOT_READY:" + pair[0])
            if pair[1] == "PAPER":
                controls = [
                    r
                    for r in before[local[pair][0]]["operations"]["audit_events"]
                    if r["service"] == "control"
                ]
                lifecycle = [
                    r
                    for r in controls
                    if r["event_type"] in {"SYSTEM_START", "SYSTEM_STOP", "KILL_SWITCH"}
                ]
                if not (
                    controls
                    and controls[-1]["event_type"] in {"SYSTEM_START", "SYSTEM_RESUME"}
                    and lifecycle
                    and lifecycle[-1]["event_type"] == "SYSTEM_START"
                ):
                    readiness_errors.append("CURRENT_SCOPE_LOCAL_PAPER_NOT_RUNNING:" + pair[0])
            if not 0 <= (stamp - clock(value["generated_at"], stamp)).total_seconds() <= 720:
                readiness_errors.append("CURRENT_SCOPE_BOOK_STALE:" + pair[0] + ":" + pair[1])
        require(
            document(root / "roster.json") == roster_document
            and book_snapshot(root, roster) == before
            and approval_scope(
                model_database,
                day,
                frozen_model,
                frozen_approval,
                current_model,
                current_approval,
                stamp,
            )
            == context,
            "SCOPE_CHANGED_DURING_REPLAY",
        )
        require(
            all(
                document(root / r["book_id"] / "admissions" / (r["target_id"] + ".json"))[
                    "evidence_sha256"
                ]
                == r["admission_sha256"]
                and document(root / r["book_id"] / "market-inputs" / (day.isoformat() + ".json"))[
                    "evidence_sha256"
                ]
                == r["market_evidence"]
                for r in books
                if r["accepted"]
            ),
            "FROZEN_ADMISSION_OR_MARKET_DOCUMENT_CHANGED",
        )
        checks["readonly_before_after_unchanged"] = True
        report["frozen_execution_ledger_accepted"] = all(row["accepted"] for row in books)
        report["frozen_execution_public_projection_accepted"] = not projection_errors and all(
            row["public_day_projection_matches"] for row in books
        )
        report["current_readiness_accepted"] = not readiness_errors and all(
            row["accepted"] for row in closures
        )
        checks["scope_snapshot_sha256"] = signed({"context": context, "books": before})[
            "evidence_sha256"
        ]
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        text = str(error)
        errors.append(
            text if text.startswith("FORWARD_AUDIT_") else "FROZEN_SCOPE_INPUT_OR_REPLAY_INVALID"
        )
    passed = not errors and all(report.values())
    return signed(
        {
            **AUTHORITY,
            "schema_version": SCHEMA,
            "state": "SCOPED_EXECUTION_AND_CURRENT_READINESS_VERIFIED" if passed else "BLOCKED",
            "clock_source": "SYSTEM_UTC" if now is None else "CONTROLLED_TEST_ONLY",
            "checked_at": stamp.isoformat(),
            "execution_day": day.isoformat(),
            "execution_contract": "EXECUTION_DAY_FROZEN_ADMISSION_V1",
            "current_contract": "CURRENT_READINESS_V1",
            "frozen_model_id": frozen_model,
            "frozen_approval_sha256": frozen_approval,
            "expected_current_model": current_model,
            "expected_current_approval_sha256": current_approval,
            **report,
            "checks": checks,
            "errors": errors,
            "public_projection_errors": projection_errors,
            "current_readiness_errors": readiness_errors,
            "books": books,
            "closures": closures,
            "read_only": True,
            "whole_step1_accepted": False,
            "browser_verified": False,
            "model_activated_by_auditor": False,
            "paper_recovered_by_auditor": False,
            "auditor_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "historical_pit_certified": False,
            "limitations": [
                "CONTENT_CHECKSUMS_AND_RECORDED_LEDGER_CLOCKS_NOT_EXTERNAL_TIMESTAMP_OR_HMAC_AUTHENTICATION",
                "EXECUTION_DAY_OBSERVATION_AND_POST_CLOSE_CURRENT_READINESS_ARE_SEPARATE_RESULTS",
                "NO_TRAINING_INFERENCE_EXECUTION_RECOVERY_OR_RECONSTRUCTION_OF_MISSING_SESSIONS",
                "PUBLIC_INPUT_MUST_BE_CAPTURED_SEPARATELY_WITH_REAL_HTTP_AND_DEPLOYMENT_BINDING",
            ],
        }
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "base",
        "local-root",
        "model-database",
        "workflow-database",
        "data-database",
        "published-forward",
        "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in (
        "frozen-model",
        "frozen-approval",
        "expected-current-model",
        "expected-current-approval",
    ):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    args = parser.parse_args(argv)
    require(
        not os.path.lexists(args.output)
        and not any(
            args.output.resolve().is_relative_to(p.resolve())
            for p in (
                args.base,
                args.local_root,
            )
        )
        and not any(p.is_symlink() for p in args.output.parents),
        "OUTPUT_MUST_BE_NEW_AND_INDEPENDENT",
    )
    value = audit(
        args.base,
        args.local_root,
        args.model_database,
        args.workflow_database,
        args.data_database,
        document(args.published_forward),
        args.day,
        args.frozen_model,
        args.frozen_approval,
        args.expected_current_model,
        args.expected_current_approval,
    )
    write_json_immutable(args.output, value)
    print(json.dumps(value, sort_keys=True, allow_nan=False))
    return 0 if value["state"] == "SCOPED_EXECUTION_AND_CURRENT_READINESS_VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
