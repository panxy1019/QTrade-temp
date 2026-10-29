from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any

from qtrade.research import TimeSplit
from qtrade.strategies.promotion import (
    PromotionDecision,
    PromotionEvidence,
    PromotionGate,
    PromotionOutcome,
)


class StrategyStatus(StrEnum):
    IDEA = "IDEA"
    EXPERIMENT = "EXPERIMENT"
    CANDIDATE = "CANDIDATE"
    VALIDATED = "VALIDATED"
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    APPROVED = "APPROVED"
    CHAMPION = "CHAMPION"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"


class StrategyRole(StrEnum):
    NONE = "NONE"
    CHALLENGER = "CHALLENGER"
    CHAMPION = "CHAMPION"


@dataclass(frozen=True, slots=True)
class StrategySpec:
    strategy_id: str
    name: str
    family: str
    version: str
    parent_strategy_id: str | None
    git_commit: str
    data_version: str
    universe: tuple[str, ...]
    factor_set: tuple[str, ...]
    model: str
    portfolio_rule: str
    parameters: tuple[tuple[str, str], ...]
    split: TimeSplit
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        required = (
            self.strategy_id,
            self.name,
            self.family,
            self.version,
            self.git_commit,
            self.data_version,
            self.model,
            self.portfolio_rule,
        )
        if not all(value.strip() for value in required):
            raise ValueError("strategy identity and reproducibility fields are required")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("strategy timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("strategy updated_at cannot precede created_at")
        if not self.universe or len(set(self.universe)) != len(self.universe):
            raise ValueError("strategy universe must be nonempty and unique")
        if len(set(self.factor_set)) != len(self.factor_set):
            raise ValueError("strategy factor set must be unique")
        keys = [key for key, _ in self.parameters]
        if any(not key for key in keys) or len(set(keys)) != len(keys):
            raise ValueError("strategy parameter keys must be nonempty and unique")


@dataclass(frozen=True, slots=True)
class StrategyLineage:
    child_strategy_id: str
    parent_strategy_id: str
    reason: str
    hypothesis: str
    change_summary: str
    experiment_result: str
    promotion_result: str
    created_at: datetime

    def __post_init__(self) -> None:
        values = (
            self.child_strategy_id,
            self.parent_strategy_id,
            self.reason,
            self.hypothesis,
            self.change_summary,
            self.experiment_result,
            self.promotion_result,
        )
        if not all(value.strip() for value in values):
            raise ValueError("strategy lineage fields are required")
        if self.child_strategy_id == self.parent_strategy_id:
            raise ValueError("strategy cannot descend from itself")
        if self.created_at.tzinfo is None:
            raise ValueError("lineage timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class StrategyRecord:
    spec: StrategySpec
    status: StrategyStatus
    role: StrategyRole


@dataclass(frozen=True, slots=True)
class StrategyTransition:
    strategy_id: str
    before: StrategyStatus
    after: StrategyStatus
    actor: str
    reason: str
    occurred_at: datetime


_ALLOWED_TRANSITIONS: dict[StrategyStatus, frozenset[StrategyStatus]] = {
    StrategyStatus.IDEA: frozenset(
        {StrategyStatus.EXPERIMENT, StrategyStatus.REJECTED, StrategyStatus.RETIRED}
    ),
    StrategyStatus.EXPERIMENT: frozenset(
        {StrategyStatus.CANDIDATE, StrategyStatus.REJECTED, StrategyStatus.RETIRED}
    ),
    StrategyStatus.CANDIDATE: frozenset(
        {StrategyStatus.VALIDATED, StrategyStatus.REJECTED, StrategyStatus.RETIRED}
    ),
    StrategyStatus.VALIDATED: frozenset(
        {StrategyStatus.PAPER, StrategyStatus.REJECTED, StrategyStatus.RETIRED}
    ),
    StrategyStatus.PAPER: frozenset(
        {StrategyStatus.SHADOW, StrategyStatus.REJECTED, StrategyStatus.RETIRED}
    ),
    StrategyStatus.SHADOW: frozenset(
        {StrategyStatus.APPROVED, StrategyStatus.REJECTED, StrategyStatus.RETIRED}
    ),
    StrategyStatus.APPROVED: frozenset(
        {StrategyStatus.CHAMPION, StrategyStatus.REJECTED, StrategyStatus.RETIRED}
    ),
    StrategyStatus.CHAMPION: frozenset({StrategyStatus.RETIRED}),
    StrategyStatus.RETIRED: frozenset(),
    StrategyStatus.REJECTED: frozenset(),
}


class StrategyRegistry:
    """Durable strategy identity, lineage, lifecycle and research-role registry."""

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS strategies (
                    strategy_id TEXT PRIMARY KEY,
                    family TEXT NOT NULL,
                    version TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    role TEXT NOT NULL,
                    UNIQUE(family, version)
                );
                CREATE TABLE IF NOT EXISTS strategy_lineage (
                    child_strategy_id TEXT PRIMARY KEY REFERENCES strategies(strategy_id),
                    parent_strategy_id TEXT NOT NULL REFERENCES strategies(strategy_id),
                    lineage_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_transitions (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id TEXT NOT NULL REFERENCES strategies(strategy_id),
                    before_status TEXT NOT NULL,
                    after_status TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_role_history (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id TEXT NOT NULL REFERENCES strategies(strategy_id),
                    before_role TEXT NOT NULL,
                    after_role TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS promotion_decisions (
                    evidence_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL REFERENCES strategies(strategy_id),
                    evidence_hash TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    reasons_json TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_research_contracts (
                    strategy_id TEXT PRIMARY KEY REFERENCES strategies(strategy_id),
                    contract_sha256 TEXT NOT NULL UNIQUE,
                    contract_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_research_jobs (
                    job_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL REFERENCES strategies(strategy_id),
                    actor TEXT NOT NULL,
                    registered_at TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def register(
        self,
        spec: StrategySpec,
        *,
        lineage: StrategyLineage | None = None,
    ) -> StrategyRecord:
        if (spec.parent_strategy_id is None) != (lineage is None):
            raise ValueError("parent strategy and lineage must be supplied together")
        if lineage is not None and (
            lineage.child_strategy_id != spec.strategy_id
            or lineage.parent_strategy_id != spec.parent_strategy_id
        ):
            raise ValueError("lineage identity does not match strategy specification")
        spec_json = self._encode_spec(spec)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT spec_json,status,role FROM strategies WHERE strategy_id=?",
                (spec.strategy_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] != spec_json:
                    raise ValueError("strategy_id conflicts with immutable specification")
                return StrategyRecord(spec, StrategyStatus(existing[1]), StrategyRole(existing[2]))
            if lineage is not None:
                parent = connection.execute(
                    "SELECT family FROM strategies WHERE strategy_id=?",
                    (lineage.parent_strategy_id,),
                ).fetchone()
                if parent is None:
                    raise ValueError("parent strategy is not registered")
                if parent[0] != spec.family:
                    raise ValueError("strategy lineage cannot cross families")
            try:
                connection.execute(
                    "INSERT INTO strategies VALUES(?,?,?,?,?,?)",
                    (
                        spec.strategy_id,
                        spec.family,
                        spec.version,
                        spec_json,
                        StrategyStatus.IDEA.value,
                        StrategyRole.NONE.value,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("strategy family/version already exists") from exc
            if lineage is not None:
                connection.execute(
                    "INSERT INTO strategy_lineage VALUES(?,?,?)",
                    (
                        lineage.child_strategy_id,
                        lineage.parent_strategy_id,
                        self._encode_lineage(lineage),
                    ),
                )
        return StrategyRecord(spec, StrategyStatus.IDEA, StrategyRole.NONE)

    def transition(
        self,
        strategy_id: str,
        target: StrategyStatus,
        *,
        actor: str,
        reason: str,
        human_authorized: bool = False,
        occurred_at: datetime | None = None,
    ) -> StrategyRecord:
        if not actor.strip() or not reason.strip():
            raise ValueError("strategy transition requires actor and reason")
        if target in {StrategyStatus.APPROVED, StrategyStatus.CHAMPION} and not human_authorized:
            raise PermissionError("post-SHADOW status requires explicit human authorization")
        timestamp = occurred_at or datetime.now(UTC)
        if timestamp.tzinfo is None:
            raise ValueError("strategy transition timestamp must be timezone-aware")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT spec_json,status,role FROM strategies WHERE strategy_id=?",
                (strategy_id,),
            ).fetchone()
            if row is None:
                raise KeyError(strategy_id)
            before = StrategyStatus(row[1])
            if target not in _ALLOWED_TRANSITIONS[before]:
                raise ValueError(f"illegal strategy transition {before.value}->{target.value}")
            spec = replace(self._decode_spec(row[0]), updated_at=timestamp)
            connection.execute(
                "UPDATE strategies SET spec_json=?,status=? WHERE strategy_id=?",
                (self._encode_spec(spec), target.value, strategy_id),
            )
            connection.execute(
                "INSERT INTO strategy_transitions"
                "(strategy_id,before_status,after_status,actor,reason,occurred_at) "
                "VALUES(?,?,?,?,?,?)",
                (strategy_id, before.value, target.value, actor, reason, timestamp.isoformat()),
            )
        return StrategyRecord(spec, target, StrategyRole(row[2]))

    def register_research_experiment(
        self, spec: StrategySpec, *, contract: dict[str, Any], job_id: str, actor: str
    ) -> StrategyRecord:
        """Atomically bind a Qlib contract/job at EXPERIMENT, without promotion.

        Repeated jobs preserve the original registration clocks and lifecycle;
        a distinct job can reference the same immutable strategy version.
        """
        from qtrade.research.strategy_contract import strategy_spec, validate_strategy

        if (os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false"
                or re.fullmatch("[a-f0-9]{32}", job_id) is None
                or re.fullmatch(r"[A-Za-z0-9._@-]{1,80}", actor) is None):
            raise ValueError("RESEARCH_REGISTRATION_AUTHORITY_OR_JOB_INVALID")
        contract = validate_strategy(contract)
        expected = strategy_spec(contract, universe=spec.universe, now=spec.created_at)
        if spec != expected:
            raise ValueError("RESEARCH_STRATEGY_SPEC_CONTRACT_MISMATCH")
        return self._register_validated_research_experiment(
            spec, contract=contract, job_id=job_id, actor=actor)

    def register_factor_research_experiment(
        self, spec: StrategySpec, *, contract: dict[str, Any], job_id: str, actor: str
    ) -> StrategyRecord:
        """Associate a strict factor definition; never accept Qlib/arbitrary schemas.

        This is structural registry admission, like the Qlib entry point. The
        factor_strategy_contract.register_strategy adapter additionally resolves
        the actual local factor/evaluation stores and admitted data archive.
        """
        from qtrade.research.factor_strategy_contract import (
            strategy_lineage,
            strategy_spec,
            validate_strategy,
        )

        if (os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false"
                or re.fullmatch("[a-f0-9]{32}", job_id) is None
                or re.fullmatch(r"[A-Za-z0-9._@-]{1,80}", actor) is None):
            raise ValueError("RESEARCH_REGISTRATION_AUTHORITY_OR_JOB_INVALID")
        contract = validate_strategy(contract, verify_runtime_source=True)
        if spec != strategy_spec(contract, now=spec.created_at):
            raise ValueError("RESEARCH_STRATEGY_SPEC_CONTRACT_MISMATCH")
        return self._register_validated_research_experiment(spec, contract=contract,
            job_id=job_id, actor=actor, lineage=strategy_lineage(contract, now=spec.created_at),
            parent=contract["spec"]["parent"], factor_only=True)

    def _register_validated_research_experiment(
        self, spec: StrategySpec, *, contract: dict[str, Any], job_id: str, actor: str,
        lineage: StrategyLineage | None = None, parent: dict[str, Any] | None = None,
        factor_only: bool = False,
    ) -> StrategyRecord:
        """Private shared transaction; public entry points validate fixed schemas."""
        if ((spec.parent_strategy_id is None) != (lineage is None)
                or (lineage is None) != (parent is None)
                or lineage is not None and (lineage.child_strategy_id != spec.strategy_id
                    or lineage.parent_strategy_id != spec.parent_strategy_id)):
            raise ValueError("RESEARCH_PARENT_LINEAGE_MISMATCH")
        registered_at = spec.created_at
        raw = json.dumps(contract, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._connect() as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            if parent is not None:
                registered_parent = connection.execute(
                    "SELECT s.family,s.version,c.contract_sha256,c.contract_json,s.spec_json "
                    "FROM strategies s "
                    "JOIN strategy_research_contracts c ON c.strategy_id=s.strategy_id "
                    "WHERE s.strategy_id=?", (spec.parent_strategy_id,),
                ).fetchone()
                if (registered_parent is None or registered_parent[:3] != (
                        spec.family, parent["strategy_version"], parent["contract_sha256"])):
                    raise ValueError("RESEARCH_PARENT_NOT_REGISTERED_OR_CHANGED")
                if factor_only:
                    from qtrade.research import factor_strategy_contract as factor_contract

                    parent_contract = factor_contract.validate_strategy(
                        json.loads(registered_parent[3]))
                    parent_spec = self._decode_spec(registered_parent[4])
                    expected_parent = factor_contract.strategy_spec(
                        parent_contract, now=parent_spec.created_at)
                    if (parent_contract["evidence_sha256"] != parent["contract_sha256"]
                            or parent_spec != replace(expected_parent,
                                                      updated_at=parent_spec.updated_at)):
                        raise ValueError("RESEARCH_PARENT_NOT_REGISTERED_OR_CHANGED")
            job = connection.execute("SELECT strategy_id,actor FROM strategy_research_jobs "
                                     "WHERE job_id=?", (job_id,)).fetchone()
            if job is not None and job != (spec.strategy_id, actor):
                raise ValueError("IMMUTABLE_RESEARCH_JOB_STRATEGY_CONFLICT")
            existing = connection.execute(
                "SELECT spec_json,status,role FROM strategies WHERE strategy_id=?",
                (spec.strategy_id,),
            ).fetchone()
            if existing is None:
                connection.execute("INSERT INTO strategies VALUES(?,?,?,?,?,?)", (
                    spec.strategy_id, spec.family, spec.version, self._encode_spec(spec),
                    StrategyStatus.EXPERIMENT.value, StrategyRole.NONE.value,
                ))
                connection.execute("INSERT INTO strategy_research_contracts VALUES(?,?,?)", (
                    spec.strategy_id, contract["evidence_sha256"], raw,
                ))
                if lineage is not None:
                    connection.execute("INSERT INTO strategy_lineage VALUES(?,?,?)", (
                        spec.strategy_id, spec.parent_strategy_id, self._encode_lineage(lineage),
                    ))
                connection.execute(
                    "INSERT INTO strategy_transitions(strategy_id,before_status,after_status,"
                    "actor,reason,occurred_at) VALUES(?,?,?,?,?,?)", (
                        spec.strategy_id, StrategyStatus.IDEA.value,
                        StrategyStatus.EXPERIMENT.value,
                        actor, "Accepted immutable " + ("factor" if factor_only else "Qlib")
                        + " research job " + job_id,
                        spec.created_at.isoformat(),
                    ),
                )
                status, role = StrategyStatus.EXPERIMENT, StrategyRole.NONE
            else:
                stored = self._decode_spec(existing[0])
                # Lifecycle transitions may update updated_at; no identity field may change.
                expected_spec = replace(spec, created_at=stored.created_at,
                                        updated_at=stored.updated_at)
                frozen = connection.execute(
                    "SELECT contract_sha256,contract_json FROM strategy_research_contracts "
                    "WHERE strategy_id=?", (spec.strategy_id,),
                ).fetchone()
                if stored != expected_spec or frozen != (contract["evidence_sha256"], raw):
                    raise ValueError("IMMUTABLE_RESEARCH_STRATEGY_CONFLICT")
                spec = stored
                status, role = StrategyStatus(existing[1]), StrategyRole(existing[2])
                if factor_only:
                    if status != StrategyStatus.EXPERIMENT or role != StrategyRole.NONE:
                        raise ValueError("FACTOR_RESEARCH_REGISTRATION_REQUIRES_EXPERIMENT_NONE")
                    stored_lineage = connection.execute(
                        "SELECT lineage_json FROM strategy_lineage WHERE child_strategy_id=?",
                        (spec.strategy_id,),
                    ).fetchone()
                    if lineage is None:
                        if stored_lineage is not None:
                            raise ValueError("IMMUTABLE_RESEARCH_LINEAGE_CONFLICT")
                    elif (stored_lineage is None or replace(lineage, created_at=
                            self._decode_lineage(stored_lineage[0]).created_at)
                            != self._decode_lineage(stored_lineage[0])):
                        raise ValueError("IMMUTABLE_RESEARCH_LINEAGE_CONFLICT")
                if job is None and status in {StrategyStatus.REJECTED, StrategyStatus.RETIRED}:
                    raise ValueError("RESEARCH_STRATEGY_LIFECYCLE_CLOSED")
            if job is None:
                connection.execute("INSERT INTO strategy_research_jobs VALUES(?,?,?,?)", (
                    job_id, spec.strategy_id, actor, registered_at.isoformat(),
                ))
        return StrategyRecord(spec, status, role)

    def research_contract(self, strategy_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT contract_json FROM strategy_research_contracts "
                                     "WHERE strategy_id=?", (strategy_id,)).fetchone()
        return None if row is None else dict(json.loads(row[0]))

    def research_jobs(self, strategy_id: str, *, limit: int = 100) -> tuple[dict[str, str], ...]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("BOUNDED_RESEARCH_JOB_QUERY_REQUIRED")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT job_id,actor,registered_at FROM strategy_research_jobs "
                "WHERE strategy_id=? ORDER BY registered_at DESC,job_id LIMIT ?",
                (strategy_id, limit)).fetchall()
        return tuple(dict(zip(("job_id", "actor", "registered_at"), row, strict=True))
                     for row in rows)

    def assign_research_role(
        self,
        strategy_id: str,
        role: StrategyRole,
        *,
        actor: str,
        reason: str,
        evidence_id: str,
        occurred_at: datetime | None = None,
    ) -> StrategyRecord:
        if not all(value.strip() for value in (actor, reason, evidence_id)):
            raise ValueError("role assignment requires actor, reason, and evidence")
        timestamp = occurred_at or datetime.now(UTC)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT spec_json,status,role,family FROM strategies WHERE strategy_id=?",
                (strategy_id,),
            ).fetchone()
            if row is None:
                raise KeyError(strategy_id)
            status = StrategyStatus(row[1])
            if role in {StrategyRole.CHAMPION, StrategyRole.CHALLENGER} and status not in {
                StrategyStatus.VALIDATED,
                StrategyStatus.PAPER,
                StrategyStatus.SHADOW,
                StrategyStatus.APPROVED,
                StrategyStatus.CHAMPION,
            }:
                raise ValueError("research roles require a validated strategy")
            before = StrategyRole(row[2])
            if role is StrategyRole.CHAMPION:
                decision = connection.execute(
                    "SELECT outcome FROM promotion_decisions "
                    "WHERE evidence_id=? AND strategy_id=?",
                    (evidence_id, strategy_id),
                ).fetchone()
                if decision is None or PromotionOutcome(decision[0]) is not PromotionOutcome.PASS:
                    raise ValueError(
                        "champion assignment requires a persisted passing promotion decision"
                    )
                if status not in {StrategyStatus.APPROVED, StrategyStatus.CHAMPION}:
                    raise ValueError("champion assignment requires an approved strategy")
                existing = connection.execute(
                    "SELECT strategy_id FROM strategies WHERE family=? AND role=?",
                    (row[3], StrategyRole.CHAMPION.value),
                ).fetchone()
                if existing is not None and existing[0] != strategy_id:
                    raise ValueError("strategy family already has a champion")
            connection.execute(
                "UPDATE strategies SET role=? WHERE strategy_id=?",
                (role.value, strategy_id),
            )
            connection.execute(
                "INSERT INTO strategy_role_history"
                "(strategy_id,before_role,after_role,actor,reason,evidence_id,occurred_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    strategy_id,
                    before.value,
                    role.value,
                    actor,
                    reason,
                    evidence_id,
                    timestamp.isoformat(),
                ),
            )
        return StrategyRecord(self._decode_spec(row[0]), status, role)

    def evaluate_promotion(
        self,
        strategy_id: str,
        evidence: PromotionEvidence,
        *,
        actor: str,
        occurred_at: datetime | None = None,
    ) -> PromotionDecision:
        """Evaluate with the repository policy and durably bind the immutable evidence."""
        if not actor.strip():
            raise ValueError("promotion decision requires an actor")
        timestamp = occurred_at or datetime.now(UTC)
        if timestamp.tzinfo is None:
            raise ValueError("promotion decision timestamp must be timezone-aware")
        decision = PromotionGate().evaluate(evidence)
        evidence_json = json.dumps(
            asdict(evidence), sort_keys=True, separators=(",", ":"), default=str
        )
        evidence_hash = sha256(evidence_json.encode()).hexdigest()
        reasons_json = json.dumps(decision.reasons, separators=(",", ":"))
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM strategies WHERE strategy_id=?", (strategy_id,)
            ).fetchone() is None:
                raise KeyError(strategy_id)
            existing = connection.execute(
                "SELECT strategy_id,evidence_hash,outcome,reasons_json "
                "FROM promotion_decisions "
                "WHERE evidence_id=?",
                (evidence.evidence_id,),
            ).fetchone()
            expected = (strategy_id, evidence_hash, decision.outcome.value, reasons_json)
            if existing is not None:
                if tuple(existing) != expected:
                    raise ValueError("promotion evidence identity is immutable")
                return decision
            connection.execute(
                "INSERT INTO promotion_decisions VALUES(?,?,?,?,?,?,?)",
                (
                    evidence.evidence_id,
                    strategy_id,
                    evidence_hash,
                    decision.outcome.value,
                    reasons_json,
                    actor,
                    timestamp.isoformat(),
                ),
            )
        return decision

    def get(self, strategy_id: str) -> StrategyRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT spec_json,status,role FROM strategies WHERE strategy_id=?",
                (strategy_id,),
            ).fetchone()
        if row is None:
            raise KeyError(strategy_id)
        return StrategyRecord(
            self._decode_spec(row[0]), StrategyStatus(row[1]), StrategyRole(row[2])
        )

    def list(self, *, family: str | None = None) -> tuple[StrategyRecord, ...]:
        query = "SELECT spec_json,status,role FROM strategies"
        parameters: tuple[str, ...] = ()
        if family is not None:
            query += " WHERE family=?"
            parameters = (family,)
        query += " ORDER BY family,version,strategy_id"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(
            StrategyRecord(
                self._decode_spec(row[0]), StrategyStatus(row[1]), StrategyRole(row[2])
            )
            for row in rows
        )

    def lineage(self, strategy_id: str) -> StrategyLineage | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT lineage_json FROM strategy_lineage WHERE child_strategy_id=?",
                (strategy_id,),
            ).fetchone()
        return None if row is None else self._decode_lineage(row[0])

    def transitions(self, strategy_id: str) -> tuple[StrategyTransition, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT strategy_id,before_status,after_status,actor,reason,occurred_at "
                "FROM strategy_transitions WHERE strategy_id=? ORDER BY sequence",
                (strategy_id,),
            ).fetchall()
        return tuple(
            StrategyTransition(
                row[0],
                StrategyStatus(row[1]),
                StrategyStatus(row[2]),
                row[3],
                row[4],
                datetime.fromisoformat(row[5]),
            )
            for row in rows
        )

    @staticmethod
    def _encode_spec(spec: StrategySpec) -> str:
        payload = asdict(spec)
        payload["split"] = {
            key: value.isoformat() for key, value in asdict(spec.split).items()
        }
        payload["created_at"] = spec.created_at.isoformat()
        payload["updated_at"] = spec.updated_at.isoformat()
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _decode_spec(raw: str) -> StrategySpec:
        payload = json.loads(raw)
        split = TimeSplit(
            **{
                key: date.fromisoformat(value)
                for key, value in payload["split"].items()
            }
        )
        return StrategySpec(
            strategy_id=payload["strategy_id"],
            name=payload["name"],
            family=payload["family"],
            version=payload["version"],
            parent_strategy_id=payload["parent_strategy_id"],
            git_commit=payload["git_commit"],
            data_version=payload["data_version"],
            universe=tuple(payload["universe"]),
            factor_set=tuple(payload["factor_set"]),
            model=payload["model"],
            portfolio_rule=payload["portfolio_rule"],
            parameters=tuple(tuple(item) for item in payload["parameters"]),
            split=split,
            created_at=datetime.fromisoformat(payload["created_at"]),
            updated_at=datetime.fromisoformat(payload["updated_at"]),
        )

    @staticmethod
    def _encode_lineage(lineage: StrategyLineage) -> str:
        payload = asdict(lineage)
        payload["created_at"] = lineage.created_at.isoformat()
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _decode_lineage(raw: str) -> StrategyLineage:
        payload = json.loads(raw)
        payload["created_at"] = datetime.fromisoformat(payload["created_at"])
        return StrategyLineage(**payload)
