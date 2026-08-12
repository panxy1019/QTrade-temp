from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

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
