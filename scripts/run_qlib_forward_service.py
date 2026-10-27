"""Supervised prospective research PAPER/SHADOW; never automatically recover a book."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import signal
import sqlite3
import sys
from collections.abc import Iterator
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event
from typing import Any
from uuid import uuid4

from qtrade.domain import RuntimeMode
from qtrade.execution import FeeSchedule
from qtrade.market_data.akshare import SHANGHAI
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_epoch import current_epoch
from qtrade.research.data_versions import content_id, file_hash
from qtrade.research.forward_paper import ForwardPaperBook, ForwardTarget
from qtrade.research.forward_publication import validate_forward_publication
from qtrade.research.model_registry import checked_document, checked_identity
from qtrade.research.prediction_registry import active_approval
from scripts.atomic_json import write_json_atomic, write_json_immutable
from scripts.qlib_forward_paper_adapter import (
    observed_session,
    prediction_target,
    public_book_summary,
    signal_targets,
)
from scripts.run_qlib_forward_paper import load_target

REPO = Path(__file__).resolve().parents[1]
LIMITATIONS = [
    "Research only; LIVE_TRADING_ENABLED=false; no external gateway.",
    "Unknown ST, suspension, limits and corporate actions block simulated orders.",
    "PAPER restarts require explicit human reconciliation; SHADOW has no fills or returns.",
    "Current universe has survivor bias; historical PIT and available_at remain uncertified.",
    "Each execution slot retains its first admitted target; later proposals are not executed.",
]
RECOVERY_SCHEMA = "qtrade.forward-recovery-request.v1"


def load(path: Path, maximum: int = 16 * 1024 * 1024) -> dict[str, Any]:
    if path.is_symlink() or path.stat().st_size > maximum:
        raise ValueError("FORWARD_SERVICE_INPUT_BOUND_EXCEEDED")
    value: dict[str, Any] = json.loads(path.read_bytes())
    checked_document(value)
    return value


def readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def validate_recovery_request(
    value: dict[str, Any], *, identity: str, instance_id: str, now: datetime
) -> None:
    checked_document(value)
    if (
        any(
            type(value.get(key)) is not type(expected) or value[key] != expected
            for key, expected in AUTHORITY.items()
        )
        or set(value)
        != set(AUTHORITY)
        | {
            "schema_version",
            "evidence_sha256",
            "request_id",
            "book_id",
            "actor",
            "instance_id",
            "issued_at",
            "expires_at",
        }
        or value["schema_version"] != RECOVERY_SCHEMA
        or re.fullmatch("[a-f0-9]{32}", identity) is None
        or value["request_id"] != identity
        or value["instance_id"] != instance_id
        or re.fullmatch("[a-f0-9]{32}", instance_id) is None
        or re.fullmatch("[A-Za-z0-9._-]{1,60}", value["actor"]) is None
    ):
        raise ValueError("FORWARD_SERVICE_RECOVERY_REQUEST_INVALID")
    checked_identity(value["book_id"])
    issued, expires = (datetime.fromisoformat(value[k]) for k in ("issued_at", "expires_at"))
    if (
        issued.tzinfo is None
        or expires.tzinfo is None
        or not issued <= now < expires
        or not 30 <= (expires - issued).total_seconds() <= 600
    ):
        raise ValueError("FORWARD_SERVICE_RECOVERY_REQUEST_EXPIRED_OR_CLOCK_INVALID")


def prepare_recovery_request(
    root: Path, *, book_id: str, actor: str, request_id: str | None = None, ttl_seconds: int = 300
) -> dict[str, Any]:
    """Explicit operator invocation only; never opens a book or recovers a runtime."""
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise ValueError("FORWARD_SERVICE_CONFIGURATION_INVALID")
    checked_identity(book_id)
    config = load(root / "roster.json")["books"].get(book_id)
    instance = load(root / "service-instance.json")
    now = datetime.now(UTC)
    heartbeat = datetime.fromisoformat(instance["heartbeat_at"])
    if (
        config is None
        or signed(config)["evidence_sha256"] != book_id
        or config["mode"] != "PAPER"
        or not (root / book_id / "forward.sqlite").is_file()
        or instance.get("schema_version") != "qtrade.forward-service-instance.v1"
        or instance.get("state") != "RUNNING"
        or any(
            type(instance.get(key)) is not type(expected) or instance[key] != expected
            for key, expected in AUTHORITY.items()
        )
        or book_id not in instance.get("paper_books", [])
        or heartbeat.tzinfo is None
        or not 0 <= (now - heartbeat).total_seconds() <= 90
        or type(ttl_seconds) is not int
        or not 30 <= ttl_seconds <= 600
    ):
        raise ValueError("FORWARD_SERVICE_RECOVERY_BOOK_OR_INSTANCE_INVALID")
    identity = request_id if request_id is not None else uuid4().hex
    value = signed(
        {
            **AUTHORITY,
            "schema_version": RECOVERY_SCHEMA,
            "request_id": identity,
            "book_id": book_id,
            "actor": actor,
            "instance_id": instance["instance_id"],
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
        }
    )
    validate_recovery_request(
        value, identity=identity, instance_id=instance["instance_id"], now=now
    )
    for directory in ("consumed", "receipts", "requests"):
        if (root / "recovery" / directory / (identity + ".json")).exists():
            raise ValueError("FORWARD_SERVICE_RECOVERY_REQUEST_ID_REUSED")
    write_json_immutable(root / "recovery/requests" / (identity + ".json"), value)
    return value


@contextlib.contextmanager
def context_guard(data: Path, models: Path) -> Iterator[None]:
    """Reserve existing ledgers in writer lock order, without changing any rows."""
    with contextlib.ExitStack() as cleanup:
        for database in dict.fromkeys((data.resolve(), models.resolve())):
            connection = cleanup.enter_context(
                contextlib.closing(
                    sqlite3.connect(
                        database.as_uri() + "?mode=rw",
                        uri=True,
                        timeout=2,
                    )
                )
            )
            connection.execute("BEGIN IMMEDIATE")
            cleanup.callback(connection.rollback)
        yield


class ForwardService:
    def __init__(
        self,
        base: Path,
        local_root: Path,
        workflow_database: Path,
        model_database: Path,
        data_database: Path,
        revision: str,
        *,
        modes: tuple[RuntimeMode, ...] = (RuntimeMode.SHADOW,),
        initialize_paper: str | None = None,
        collection_database: Path | None = None,
    ):
        if (
            os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false"
            or re.fullmatch("[a-f0-9]{40}", revision) is None
            or not modes
            or len(set(modes)) != len(modes)
            or any(m not in (RuntimeMode.PAPER, RuntimeMode.SHADOW) for m in modes)
            or (
                initialize_paper is not None
                and (
                    RuntimeMode.PAPER not in modes
                    or re.fullmatch("[A-Za-z0-9._-]{1,60}", initialize_paper) is None
                )
            )
        ):
            raise ValueError("FORWARD_SERVICE_CONFIGURATION_INVALID")
        self.base, self.root = base, local_root
        self.workflow_database, self.model_database = workflow_database, model_database
        self.data_database, self.revision = data_database, revision
        self.collection_database = collection_database
        self.instance_id = uuid4().hex
        self.modes, self.initialize_paper = modes, initialize_paper
        self.calendar = Calendar(REPO / "configs/observer_calendar_2026.json")
        self.root.mkdir(parents=True, exist_ok=True)
        self.books: dict[str, ForwardPaperBook] = {}
        self.links: dict[str, dict[str, Any]] = {}
        self.startup_errors: list[dict[str, Any]] = []
        self.discovery_errors: list[str] = []
        roster = self.root / "roster.json"
        self.roster: dict[str, Any] = load(roster)["books"] if roster.exists() else {}
        if not isinstance(self.roster, dict) or len(self.roster) > 32:
            raise ValueError("FORWARD_SERVICE_BOOK_LIMIT")
        for identity, config in self.roster.items():
            try:
                if RuntimeMode(config["mode"]) not in modes:
                    continue
                self._open(identity, config, pristine=False)
            except Exception:
                # A damaged book cannot take down independent research accounts.
                self.startup_errors.append(
                    {
                        "strategy_id": None,
                        "mode": None,
                        "code": "FORWARD_SERVICE_BOOK_RESTORE_FAILED",
                    }
                )

    def _open(self, identity: str, config: dict[str, Any], *, pristine: bool) -> None:
        checked_identity(identity)
        if signed(config)["evidence_sha256"] != identity:
            raise ValueError("FORWARD_SERVICE_BOOK_IDENTITY_CHANGED")
        book = ForwardPaperBook(
            self.root / identity,
            strategy_id=config["strategy_id"],
            mode=RuntimeMode(config["mode"]),
            opening_cash=Decimal("1000000"),
            fees=FeeSchedule(
                "forward-research-cost-v1",
                Decimal("0.0003"),
                Decimal("0.0005"),
                Decimal("5"),
                source_url="research-assumption-not-broker-fee-certification",
            ),
        )
        self.books[identity] = book
        link_file = self.root / identity / "archive-link.json"
        if link_file.exists():
            self.links[identity] = load(link_file)["archive"]
        if pristine and book.mode is RuntimeMode.PAPER and self.initialize_paper:
            # Persist intent first; an interrupted startup cannot retry recovery silently.
            write_json_immutable(
                self.root / identity / "initialization.json",
                signed(
                    {
                        **AUTHORITY,
                        "actor": self.initialize_paper,
                        "at": datetime.now(UTC).isoformat(),
                        "purpose": "INITIAL_PRISTINE_RESEARCH_PAPER_ONLY",
                    }
                ),
            )
            book.recover(actor=self.initialize_paper)

    def register(
        self,
        target: ForwardTarget,
        link: dict[str, Any],
        *,
        now: datetime,
        mode: RuntimeMode | None = None,
    ) -> None:
        self.check_context(target, link)
        if mode is not None and mode not in self.modes:
            raise ValueError("FORWARD_SERVICE_MODE_NOT_CONFIGURED")
        selected_modes = self.modes if mode is None else (mode,)
        for selected_mode in selected_modes:
            config = {
                "strategy_id": target.strategy_id,
                "mode": selected_mode.value,
                "cash": "1000000",
                "fee_version": "forward-research-cost-v1",
            }
            identity = signed(config)["evidence_sha256"]
            if identity not in self.books:
                if identity not in self.roster and len(self.roster) >= 32:
                    raise ValueError("FORWARD_SERVICE_BOOK_LIMIT")
                pristine = not (self.root / identity).exists()
                self.roster[identity] = config
                write_json_atomic(
                    self.root / "roster.json", signed({**AUTHORITY, "books": self.roster})
                )
                self._open(identity, config, pristine=pristine)
            book = self.books[identity]
            admission = self.root / identity / "admissions" / (target.identity + ".json")
            already_frozen = book.db.execute(
                "SELECT 1 FROM targets WHERE id=?", (target.identity,)
            ).fetchone()
            with context_guard(self.data_database, self.model_database):
                self.check_context(target, link)
                slot = book.db.execute(
                    "SELECT id,payload FROM targets WHERE execution_at=?",
                    (target.execution_at.isoformat(),),
                ).fetchall()
                if len(slot) > 1:
                    raise ValueError("FORWARD_SERVICE_FROZEN_SLOT_AMBIGUOUS")
                if slot and slot[0][0] != target.identity:
                    self.retain_frozen_slot(identity, target, link, slot[0], now=now)
                    continue
                if already_frozen:
                    if not admission.exists():
                        raise ValueError("FORWARD_SERVICE_ADMISSION_RECONCILIATION_REQUIRED")
                    if load(admission)["archive"] != link:
                        raise ValueError("FORWARD_SERVICE_ADMISSION_CONTEXT_CHANGED")
                else:
                    book.freeze(target, now=max(now, datetime.now(UTC)))
                    # A crash leaves an unadmitted target, which observe cannot use.
                    self.check_context(target, link)
                    write_json_immutable(
                        admission,
                        signed(
                            {
                                **AUTHORITY,
                                "target_id": target.identity,
                                "archive": link,
                                "verified_at": datetime.now(UTC).isoformat(),
                            }
                        ),
                    )
            self.links[identity] = link
            write_json_atomic(
                self.root / identity / "archive-link.json", signed({**AUTHORITY, "archive": link})
            )

    def retain_frozen_slot(
        self, identity: str, proposed: ForwardTarget, link: dict[str, Any],
        slot: tuple[str, str], *, now: datetime,
    ) -> None:
        """Keep a verified first admission; never replace it with a later proposal."""
        frozen = load_target(json.loads(slot[1]))
        if (slot[0] != frozen.identity or frozen.strategy_id != proposed.strategy_id
                or frozen.execution_at != proposed.execution_at):
            raise ValueError("FORWARD_SERVICE_FROZEN_SLOT_IDENTITY_CHANGED")
        admission = self.root / identity / "admissions" / (frozen.identity + ".json")
        if not admission.is_file():
            raise ValueError("FORWARD_SERVICE_FROZEN_ADMISSION_MISSING")
        proof = load(admission)
        if proof["target_id"] != frozen.identity:
            raise ValueError("FORWARD_SERVICE_FROZEN_ADMISSION_CHANGED")
        admitted = datetime.fromisoformat(proof["verified_at"])
        if (now.tzinfo is None or admitted.tzinfo is None
                or not frozen.generated_at <= admitted <= now < frozen.execution_at
                or not proposed.generated_at <= now):
            raise ValueError("FORWARD_SERVICE_FROZEN_SLOT_CLOCK_INVALID")
        self.check_context(frozen, proof["archive"])
        body = {
            **AUTHORITY, "kind": "FORWARD_EXECUTION_SLOT_PROPOSAL_DISPOSITION",
            "state": "RETAINED_FIRST_ADMITTED_EXECUTION_TARGET", "book_id": identity,
            "execution_at": frozen.execution_at.isoformat(),
            "frozen_target_id": frozen.identity,
            "frozen_admission_sha256": proof["evidence_sha256"],
            "proposed_target_id": proposed.identity,
            "proposed_target": json.loads(json.dumps(asdict(proposed), default=str)),
            "proposed_archive": link, "proposal_admitted": False,
        }
        path = self.root / identity / "rejected-targets" / (proposed.identity + ".json")
        if path.exists():
            prior = load(path)
            if signed({**body, "observed_at": prior["observed_at"]}) != prior:
                raise ValueError("FORWARD_SERVICE_FROZEN_PROPOSAL_RECEIPT_CHANGED")
        else:
            if len(list(path.parent.glob("*.json"))) >= 256:
                raise ValueError("FORWARD_SERVICE_FROZEN_PROPOSAL_RECEIPT_BOUND")
            write_json_immutable(path, signed({**body, "observed_at": now.isoformat()}))

    def check_context(self, target: ForwardTarget, link: dict[str, Any]) -> None:
        if current_epoch(self.data_database, target.data_version) != link["data_event_id"]:
            raise ValueError("FORWARD_SERVICE_DATA_EPOCH_CHANGED")
        if link.get("approval_sha256"):
            with contextlib.closing(readonly(self.model_database)) as c:
                approval = active_approval(c)
            if approval is None or approval["evidence_sha256"] != link["approval_sha256"]:
                raise ValueError("FORWARD_SERVICE_APPROVAL_CHANGED")

    def current_targets(self, now: datetime) -> list[tuple[ForwardTarget, dict[str, Any]]]:
        self.discovery_errors = []
        online = load(self.base / "workflow/public/online.json")
        stamp = datetime.fromisoformat(online["worker"]["heartbeat_at"])
        if stamp.tzinfo is None or not 0 <= (now - stamp).total_seconds() <= 720:
            raise ValueError("FORWARD_SERVICE_WORKER_STALE")
        version = online["data_version"]
        if version is None:
            raise ValueError("FORWARD_SERVICE_DATA_UNAVAILABLE")
        epoch = current_epoch(self.data_database, version["version_id"])
        if version.get("data_event_id") != epoch:
            raise ValueError("FORWARD_SERVICE_DATA_EPOCH_CHANGED")
        if self.calendar.expected(now, "daily").date().isoformat() != version["actual_end"]:
            raise ValueError("FORWARD_SERVICE_DATA_STALE")
        source = online["worker"]["source_revision"]
        if re.fullmatch("[a-f0-9]{40}", source) is None:
            raise ValueError("FORWARD_SERVICE_SOURCE_INVALID")
        result = []
        try:
            with contextlib.closing(readonly(self.workflow_database)) as c:
                rows = c.execute(
                    "SELECT id,request,result FROM jobs WHERE state='COMPLETE' "
                    "ORDER BY rowid DESC LIMIT 100"
                ).fetchall()
            for row in rows:
                request = json.loads(row["request"])
                if (
                    request.get("kind") != "QLIB_SIGNAL_JOB"
                    or request.get("data_version_id") != version["version_id"]
                    or request.get("data_event_id") != epoch
                ):
                    continue
                identity = checked_identity(json.loads(row["result"])["signal_family_id"])
                if re.fullmatch("[a-f0-9]{32}", row["id"]) is None:
                    raise ValueError("FORWARD_SERVICE_JOB_ID_INVALID")
                path = self.base / "workflow/jobs" / row["id"] / "output" / identity
                manifest = load(path / "manifest.json")
                if manifest["bindings"].get("data_event_id") != epoch:
                    raise ValueError("FORWARD_SERVICE_SIGNAL_EPOCH_CHANGED")
                result.extend(
                    signal_targets(path, data_version=version["version_id"], source_revision=source)
                )
                break
            if not result:
                self.discovery_errors.append("FORWARD_SERVICE_SIGNAL_UNAVAILABLE")
        except Exception:
            self.discovery_errors.append("FORWARD_SERVICE_SIGNAL_DISCOVERY_FAILED")
        with contextlib.closing(readonly(self.model_database)) as c:
            approval = active_approval(c)
        try:
            prediction = online.get("auto_predict", {}).get("current_prediction_id")
            if prediction is not None and approval is not None:
                checked_identity(prediction)
                path = (
                    self.base
                    / "online-research/control/predictions"
                    / prediction
                    / "output"
                    / prediction
                )
                manifest = load(path / "manifest.json")
                bindings = manifest["bindings"]
                if (
                    bindings["data_event_id"] != epoch
                    or bindings["approval_sha256"] != approval["evidence_sha256"]
                ):
                    raise ValueError("FORWARD_SERVICE_PREDICTION_APPROVAL_OR_EPOCH_CHANGED")
                result.append(
                    prediction_target(
                        path,
                        active_model_id=approval["model_id"],
                        data_version=version["version_id"],
                        source_revision=source,
                    )
                )
            elif approval is not None or prediction is not None:
                self.discovery_errors.append("FORWARD_SERVICE_PREDICTION_UNAVAILABLE")
        except Exception:
            self.discovery_errors.append("FORWARD_SERVICE_PREDICTION_DISCOVERY_FAILED")
        # Recheck after potentially costly byte verification, before prospective admission.
        if current_epoch(self.data_database, version["version_id"]) != epoch:
            raise ValueError("FORWARD_SERVICE_DATA_EPOCH_CHANGED")
        with contextlib.closing(readonly(self.model_database)) as c:
            if active_approval(c) != approval:
                raise ValueError("FORWARD_SERVICE_APPROVAL_CHANGED")
        return [
            (
                target,
                {
                    **link,
                    "data_event_id": epoch,
                    "approval_sha256": approval["evidence_sha256"]
                    if link["kind"] == "approved-prediction" and approval
                    else None,
                },
            )
            for target, link in result
        ]

    def collection_tasks(self, day: str, instruments: list[str]) -> list[dict[str, Any]]:
        needed = {s[2:] for s in instruments}
        if self.collection_database is not None:
            with contextlib.closing(readonly(self.collection_database)) as connection:
                rows = connection.execute(
                    "SELECT symbol,state,last_success_batch FROM "
                    "collection_tasks WHERE frequency='day' AND trading_day=? LIMIT 20001",
                    (day,),
                ).fetchall()
            if len(rows) > 20000:
                raise ValueError("FORWARD_SERVICE_COLLECTION_TASK_BOUND")
            tasks = [dict(row) for row in rows if row["symbol"] in needed]
        else:
            progress = load(self.base / "unified-data/collection-progress.json", 32 * 1024 * 1024)
            if (
                datetime.fromisoformat(progress["required_through"]["day"]).date().isoformat()
                != day
            ):
                raise ValueError("FORWARD_SERVICE_REQUIRED_MARKET_DAY_UNAVAILABLE")
            tasks = [
                t
                for t in progress["tasks"]
                if t["frequency"] == "day" and t["trading_day"] == day and t["symbol"] in needed
            ]
        unique: dict[str, dict[str, Any]] = {}
        for task in tasks:
            old = unique.get(task["symbol"])
            if old is not None and any(old[k] != task[k] for k in ("state", "last_success_batch")):
                raise ValueError("FORWARD_SERVICE_COLLECTION_TASK_AMBIGUOUS")
            unique[task["symbol"]] = task
        return list(unique.values())

    def check_observation_context(self, target: ForwardTarget, archive: dict[str, Any]) -> None:
        """Allow normal descendant data, but never risk from a rolled-back lineage."""
        with contextlib.closing(readonly(self.data_database)) as c:
            anchor = c.execute(
                "SELECT sequence FROM data_events WHERE event_id=? AND version_id=?",
                (archive["data_event_id"], target.data_version),
            ).fetchone()
            if anchor is None:
                raise ValueError("FORWARD_SERVICE_FROZEN_EPOCH_MISSING")
            events = c.execute(
                "SELECT payload FROM data_events WHERE sequence>? ORDER BY sequence", (anchor[0],)
            ).fetchall()
            head = c.execute(
                "SELECT v.version_id,v.manifest_sha256 FROM data_heads h "
                "JOIN data_versions v ON h.version_id=v.version_id "
                "WHERE h.frequency='day'"
            ).fetchone()
        for row in events:
            event = json.loads(row[0])
            checked_document(event)
            if event["kind"] != "QLIB_DATA_READY":
                raise ValueError("FORWARD_SERVICE_DATA_ROLLBACK_REQUIRES_REVIEW")
        if head is None:
            raise ValueError("FORWARD_SERVICE_CURRENT_DATA_MISSING")
        current_epoch(self.data_database, head[0])
        if head[0] != target.data_version:

            def dataset(version: str, manifest_sha: str | None = None) -> dict[str, Any]:
                root = self.base / "unified-data/qlib-versions/versions" / checked_identity(version)
                manifest = load(root / "version.json")
                if (
                    manifest["version_id"] != version
                    or content_id(manifest["files"], manifest["bindings"]) != version
                    or manifest_sha is not None
                    and manifest["evidence_sha256"] != manifest_sha
                    or file_hash(root / "dataset.json") != manifest["files"]["dataset.json"]
                ):
                    raise ValueError("FORWARD_SERVICE_DATA_LINEAGE_CHANGED")
                return load(root / "dataset.json")

            frozen = dataset(target.data_version)
            current = dataset(head[0], head[1])
            if frozen["evidence_sha256"] not in current.get("ancestor_dataset_sha256", []):
                raise ValueError("FORWARD_SERVICE_DATA_NOT_DESCENDANT")
        if archive.get("approval_sha256"):
            with contextlib.closing(readonly(self.model_database)) as c:
                approval = active_approval(c)
            if approval is None or approval["evidence_sha256"] != archive["approval_sha256"]:
                raise ValueError("FORWARD_SERVICE_APPROVAL_CHANGED")

    def observe_book(self, book: ForwardPaperBook, *, now: datetime) -> None:
        row = book.db.execute(
            "SELECT t.payload FROM targets t LEFT JOIN sessions s "
            "ON t.id=s.target_id WHERE s.day IS NULL OR s.status!='COMPLETE' "
            "ORDER BY t.execution_at LIMIT 1"
        ).fetchone()
        if row is None:
            return
        target = load_target(json.loads(row[0]))
        identity = next(k for k, v in self.books.items() if v is book)
        admission = self.root / identity / "admissions" / (target.identity + ".json")
        if not admission.exists() or load(admission)["target_id"] != target.identity:
            raise ValueError("FORWARD_SERVICE_ADMISSION_RECONCILIATION_REQUIRED")
        day = target.execution_at.astimezone(SHANGHAI).date()
        if self.calendar.expected(now, "daily").date() < day:
            return
        # Use only collector-qualified successful day batches; never substitute a later day.
        instruments = sorted(
            set(target.weights)
            | {s for s, p in book.bundle.ledger.account.positions.items() if p.quantity}
        )
        evidence_file = self.root / identity / "market-inputs" / f"{day}.json"
        previous_batches: list[str] = []
        if evidence_file.exists():
            frozen = load(evidence_file)
            batches = [receipt["batch_id"] for receipt in frozen["receipts"]]
            previous_batches = [
                receipt["batch_id"]
                for receipt in (frozen.get("previous_session_volume_evidence") or {}).get(
                    "receipts", []
                )
            ]
        else:
            tasks = self.collection_tasks(day.isoformat(), instruments)
            if set(t["symbol"] for t in tasks) != {s[2:] for s in instruments} or any(
                t["state"] not in {"SUCCEEDED", "FAILED"} for t in tasks
            ):
                raise ValueError("FORWARD_SERVICE_WAITING_MARKET_COLLECTION")
            batches = sorted({t["last_success_batch"] for t in tasks if t["state"] == "SUCCEEDED"})
            if self.collection_database is not None:
                previous_day = self.calendar.previous(day - timedelta(days=1))
                prior_tasks = self.collection_tasks(previous_day.isoformat(), instruments)
                previous_batches = sorted(
                    {t["last_success_batch"] for t in prior_tasks if t["state"] == "SUCCEEDED"}
                )
        session, evidence = observed_session(
            self.base / "unified-data/lake",
            batches,
            day=day,
            instruments=instruments,
            calendar=self.calendar,
            now=now,
            previous_batch_ids=previous_batches or None,
        )
        if evidence_file.exists():
            if load(evidence_file) != evidence:
                raise ValueError("FORWARD_SERVICE_FROZEN_MARKET_INPUT_CHANGED")
        else:
            write_json_immutable(evidence_file, evidence)
        if book.mode is RuntimeMode.PAPER:
            with context_guard(self.data_database, self.model_database):
                self.check_observation_context(target, load(admission)["archive"])
                book.observe(target, session, now=now)
        else:
            book.observe(target, session, now=now)

    def recover_requested_books(self) -> list[str]:
        """Consume explicit current-instance requests once, before human reconciliation."""
        errors = []
        for index, path in enumerate((self.root / "recovery/requests").glob("*.json")):
            if index >= 1024:
                errors.append("FORWARD_SERVICE_RECOVERY_REQUEST_BOUND")
                break
            identity = path.stem
            if re.fullmatch("[a-f0-9]{32}", identity) is None:
                errors.append("FORWARD_SERVICE_RECOVERY_REQUEST_INVALID")
                continue
            consumed = self.root / "recovery/consumed" / (identity + ".json")
            receipt = self.root / "recovery/receipts" / (identity + ".json")
            if receipt.exists():
                continue
            if consumed.exists():
                errors.append("FORWARD_SERVICE_RECOVERY_CONSUMED_REQUIRES_REVIEW")
                continue
            now = datetime.now(UTC)
            try:
                request = load(path, 16 * 1024)
                validate_recovery_request(
                    request, identity=identity, instance_id=self.instance_id, now=now
                )
                book = self.books.get(request["book_id"])
                if (
                    book is None
                    or book.mode is not RuntimeMode.PAPER
                    or signed(self.roster[request["book_id"]])["evidence_sha256"]
                    != request["book_id"]
                    or book.bundle.runtime.status.running
                ):
                    raise ValueError("FORWARD_SERVICE_RECOVERY_BOOK_NOT_HALTED_PAPER")
            except Exception:
                code = "FORWARD_SERVICE_RECOVERY_REQUEST_REJECTED"
                write_json_immutable(
                    receipt,
                    signed(
                        {
                            **AUTHORITY,
                            "request_id": identity,
                            "state": "REJECTED",
                            "at": now.isoformat(),
                            "code": code,
                        }
                    ),
                )
                errors.append(code)
                continue
            # This durable intent is irreversible even if reconciliation crashes or fails.
            write_json_immutable(
                consumed,
                signed(
                    {
                        **AUTHORITY,
                        "request_id": identity,
                        "request_sha256": request["evidence_sha256"],
                        "book_id": request["book_id"],
                        "actor": request["actor"],
                        "instance_id": self.instance_id,
                        "state": "CONSUMED",
                        "at": now.isoformat(),
                    }
                ),
            )
            state = "SUCCEEDED"
            outcome_code: str | None = None
            try:
                validate_recovery_request(
                    request, identity=identity, instance_id=self.instance_id, now=datetime.now(UTC)
                )
                book.recover(actor=request["actor"])
            except Exception:
                book.bundle.report_incident("explicit forward recovery failed")
                state = "FAILED"
                outcome_code = "FORWARD_SERVICE_RECOVERY_RECONCILIATION_FAILED"
                errors.append(outcome_code)
            write_json_immutable(
                receipt,
                signed(
                    {
                        **AUTHORITY,
                        "request_id": identity,
                        "request_sha256": request["evidence_sha256"],
                        "book_id": request["book_id"],
                        "state": state,
                        "at": datetime.now(UTC).isoformat(),
                        "code": outcome_code,
                    }
                ),
            )
        return errors

    def cycle(self, *, now: datetime | None = None, stopped: bool = False) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        errors: list[dict[str, Any]] = list(self.startup_errors)

        def error(strategy: str | None, mode: str | None, exc: Exception) -> None:
            # Exception messages may contain paths or secrets; retain only safe codes.
            message = str(exc)
            code = (
                message
                if message.startswith(("FORWARD_", "CURRENT_DATA_"))
                and re.fullmatch("[A-Z][A-Z0-9_]{1,120}", message)
                else type(exc).__name__
            )
            errors.append({"strategy_id": strategy, "mode": mode, "code": code})
            detail = signed(
                {
                    **AUTHORITY,
                    "strategy_id": strategy,
                    "mode": mode,
                    "code": code,
                    "exception_class": type(exc).__name__,
                }
            )
            path = self.root / "diagnostics" / (detail["evidence_sha256"] + ".json")
            if not path.exists():
                write_json_immutable(path, detail)

        if not stopped:
            self.discovery_errors = []
            storage_ready = shutil.disk_usage(self.base).free >= 100 * 1024**3
            if storage_ready:
                for code in self.recover_requested_books():
                    error(None, RuntimeMode.PAPER.value, ValueError(code))
            try:
                if not storage_ready:
                    raise ValueError("FORWARD_SERVICE_STORAGE_RESERVE")
                targets = self.current_targets(now)
                for target, link in targets:
                    for mode in self.modes:
                        try:
                            if now < target.execution_at:
                                self.register(target, link, now=datetime.now(UTC), mode=mode)
                        except Exception as exc:
                            error(target.strategy_id, mode.value, exc)
            except Exception as exc:
                error(None, None, exc)
            for code in self.discovery_errors:
                error(None, None, ValueError(code))
            # Previously frozen books do not depend on today's new model/signal being ready.
            if storage_ready:
                for book in self.books.values():
                    try:
                        self.observe_book(book, now=now)
                    except Exception as exc:
                        error(book.strategy_id, book.mode.value, exc)

        def publication(books: list[dict[str, Any]], state: str) -> dict[str, Any]:
            return signed(
                {
                    **AUTHORITY,
                    "schema_version": "qtrade.forward-service.v1",
                    "generated_at": now.isoformat(),
                    "heartbeat_at": now.isoformat(),
                    "source_revision": self.revision,
                    "state": state,
                    "books": books,
                    "errors": errors[:64],
                    "limitations": LIMITATIONS,
                }
            )

        summaries = []
        for identity, book in self.books.items():
            try:
                if identity in self.links:
                    summary = public_book_summary(book, archive_link=self.links[identity])
                    validate_forward_publication(publication([summary], "RUNNING"), now=now)
                    summaries.append(summary)
            except Exception as exc:
                error(book.strategy_id, book.mode.value, exc)
        recovery = any(
            b.mode is RuntimeMode.PAPER and not b.bundle.runtime.status.running
            for b in self.books.values()
        )
        state = (
            "STOPPED"
            if stopped
            else "HUMAN_RECOVERY_REQUIRED"
            if recovery
            else "DEGRADED"
            if errors
            else "RUNNING"
        )
        value = validate_forward_publication(publication(summaries, state), now=now)
        write_json_atomic(self.base / "workflow/public/forward.json", value)
        write_json_atomic(
            self.root / "service-instance.json",
            signed(
                {
                    **AUTHORITY,
                    "schema_version": "qtrade.forward-service-instance.v1",
                    "instance_id": self.instance_id,
                    "state": "STOPPED" if stopped else "RUNNING",
                    "paper_books": [
                        identity
                        for identity, book in self.books.items()
                        if book.mode is RuntimeMode.PAPER
                    ],
                    "heartbeat_at": datetime.now(UTC).isoformat(),
                }
            ),
        )
        return value

    def close(self) -> None:
        try:
            for book in self.books.values():
                book.bundle.runtime.stop(actor="forward-service-shutdown")
            self.cycle(stopped=True)
        finally:
            for book in self.books.values():
                book.db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-root", type=Path, required=True)
    for name in ("base", "workflow-database", "model-database", "data-database"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--source-revision")
    parser.add_argument("--prepare-recovery-book")
    parser.add_argument("--recovery-actor")
    parser.add_argument("--recovery-request-id")
    parser.add_argument("--recovery-ttl-seconds", type=int, default=300)
    parser.add_argument("--modes", default="SHADOW")
    parser.add_argument("--initialize-paper")
    parser.add_argument("--collection-database", type=Path)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.prepare_recovery_book:
        if not args.recovery_actor:
            parser.error("--recovery-actor is required for explicit request preparation")
        value = prepare_recovery_request(
            args.local_root,
            book_id=args.prepare_recovery_book,
            actor=args.recovery_actor,
            request_id=args.recovery_request_id,
            ttl_seconds=args.recovery_ttl_seconds,
        )
        print(json.dumps(value, sort_keys=True))
        return
    if args.recovery_actor or args.recovery_request_id or args.recovery_ttl_seconds != 300:
        parser.error("Recovery options require --prepare-recovery-book")
    if any(
        getattr(args, name) is None
        for name in (
            "base",
            "workflow_database",
            "model_database",
            "data_database",
            "source_revision",
        )
    ):
        parser.error(
            "Service mode requires base, workflow/model/data databases and source revision"
        )
    if sys.platform == "win32":
        parser.error("The supervised service requires Linux local filesystem leases")
    import fcntl

    args.local_root.mkdir(parents=True, exist_ok=True)
    with (args.local_root / "service.lock").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        service = ForwardService(
            args.base,
            args.local_root,
            args.workflow_database,
            args.model_database,
            args.data_database,
            args.source_revision,
            modes=tuple(RuntimeMode(mode) for mode in args.modes.split(",")),
            initialize_paper=args.initialize_paper,
            collection_database=args.collection_database,
        )
        stop = Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        try:
            while not stop.is_set():
                service.cycle()
                if args.once:
                    return
                stop.wait(30)
        finally:
            service.close()


if __name__ == "__main__":
    main()
