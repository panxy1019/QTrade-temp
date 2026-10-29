"""Small nonproduction registry for existing, trusted daily price factors.

Reuse the existing canonical identity, PriceFactorSpec/average_ranks and the
StrategyRegistry/metadata catalog immutable SQLite transaction pattern. There is
no expression parser, dynamic import, strategy lifecycle or execution authority.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from qtrade.monitoring.data_freshness import AUTHORITY
from qtrade.research.factors import PriceFactorSpec
from qtrade.research.signal_baselines import average_ranks
from qtrade.research.workflow import canonical, digest

FactorRef = dict[str, str]
FactorDefinition = dict[str, Any]
SCHEMA = "qtrade.factor-definition.v1"
PRICE_BASES = (
    "SINA_NATIVE_ADJUSTMENT_UNCERTIFIED",
    "QLIB_NORMALIZED_ADJUSTMENT_UNCERTIFIED",
    "SYNTHETIC_UNADJUSTED",
)
_SOURCE_FILES = (
    "src/qtrade/monitoring/data_freshness.py",
    "src/qtrade/research/factor_registry.py",
    "src/qtrade/research/factors.py",
    "src/qtrade/research/signal_baselines.py",
    "src/qtrade/research/workflow.py",
)
_BUILTINS = {
    "momentum20": ("qtrade.price.momentum20", "momentum", 20,
                   "close[t]/close[t-20]-1"),
    "reversal5": ("qtrade.price.reversal5", "reversal", 5,
                  "-(close[t]/close[t-5]-1)"),
    "lowvol20": ("qtrade.price.lowvol20", "low_volatility", 20,
                 "-population_std(simple_close_returns[t-19:t+1],ddof=0)"),
}
_COMPOSITE = "multifactor_equal_rank"


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError("FACTOR_" + code)


def _disabled() -> None:
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("LIVE_TRADING_MUST_REMAIN_DISABLED")


def _identity(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _database_path(database: Path) -> None:
    path = database.absolute()
    _require(not any(item.is_symlink() for item in (path, *path.parents)),
             "DATABASE_SYMLINK_FORBIDDEN")
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(str(path) + suffix)
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue  # SQLite can remove its own transient sidecars between connections.
        _require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1,
                 "DATABASE_OR_SIDECAR_LINK_FORBIDDEN")


def _source_hashes(source_root: Path | None = None) -> dict[str, str]:
    root = source_root if source_root is not None else Path(__file__).resolve().parents[3]
    return {name: sha256((root / name).read_bytes()).hexdigest() for name in _SOURCE_FILES}


def _seal(body: FactorDefinition) -> FactorDefinition:
    versioned = {**body, "factor_version": digest(body)}
    return {**versioned, "definition_sha256": digest(versioned)}


def _definitions(revision: str, basis: str,
                 sources: dict[str, str]) -> tuple[FactorDefinition, ...]:
    common = {
        **AUTHORITY, "schema_version": SCHEMA, "frequency": "day", "price_basis": basis,
        "input_fields": ["close"], "security_types": ["STOCK", "ETF"],
        "missing_policy": "REJECT_MISSING_NONFINITE_OR_NONPOSITIVE_CLOSES",
        "session_policy": "EXACT_EXCHANGE_SESSION_GRID_REQUIRED_BY_EVALUATOR",
        "numeric_policy": {
            "decimal_precision": 28, "rounding": "ROUND_HALF_EVEN", "emin": -999999,
            "emax": 999999, "capitals": 1, "clamp": 0,
            "traps": ["DivisionByZero", "InvalidOperation", "Overflow"],
            "volatility": "EXISTING_FLOAT_POPULATION_STD_RUNTIME_VERSION_IN_EVALUATION",
        },
        "time_policy": {
            "available_at": "ALL_REQUIRED_INPUTS_NO_LATER_THAN_DECISION",
            "observed_at": "ALL_REQUIRED_INPUTS_NO_LATER_THAN_DECISION",
            "unknown_clock": "RETROSPECTIVE_ONLY_WITH_EXPLICIT_DISCLOSURE",
            "computed_at": "ACTUAL_COMPUTATION_TIME_NEVER_BACKDATED",
        },
        "source": {"revision": revision, "files": sources,
                   "binding": "EXACT_FILE_BYTES_REVISION_LABEL_NOT_GIT_CERTIFICATION"},
    }
    definitions = []
    for alias, (identity, name, lookback, formula) in _BUILTINS.items():
        definitions.append(_seal({
            **common, "factor_id": identity, "alias": alias, "formula": formula,
            "engine": "QTRADE_PRICE_FACTOR_SPEC", "required_closes": lookback + 1,
            "parameters": {"name": name, "lookback": lookback, "skip": 0},
            "dependencies": [],
        }))
    definitions.append(_seal({
        **common, "factor_id": "qtrade.composite.multifactor_equal_rank", "alias": _COMPOSITE,
        "formula": "mean(cross_sectional_average_percentile_rank(each_dependency))",
        "engine": "QTRADE_EQUAL_RANK", "required_closes": 21,
        "parameters": {"weights": ["1/3", "1/3", "1/3"],
                       "tie_policy": "AVERAGE_RANK", "singleton_rank": "0.5",
                       "universe_policy": "SAME_ELIGIBLE_INSTRUMENTS_FOR_ALL_DEPENDENCIES"},
        "dependencies": [factor_ref(value) for value in definitions],
    }))
    return tuple(definitions)


def builtin_definitions(*, source_revision: str, price_basis: str,
                        source_root: Path | None = None) -> tuple[FactorDefinition, ...]:
    """Describe four built-ins; source_root only reads the fixed source-file list."""
    _require(isinstance(source_revision, str)
             and re.fullmatch(r"[a-f0-9]{40}", source_revision) is not None,
             "SOURCE_REVISION_INVALID")
    _require(price_basis in PRICE_BASES, "PRICE_BASIS_UNSUPPORTED")
    return _definitions(source_revision, price_basis, _source_hashes(source_root))


def factor_ref(definition: FactorDefinition) -> FactorRef:
    """Return only an exact version reference, never resolve latest by alias."""
    return {key: definition[key] for key in ("factor_id", "factor_version", "definition_sha256")}


def _checked_ref(value: Any) -> FactorRef:
    _require(isinstance(value, dict) and set(value) == {
        "factor_id", "factor_version", "definition_sha256"}, "REFERENCE_INVALID")
    _require(isinstance(value["factor_id"], str)
        and value["factor_id"] in {row[0] for row in _BUILTINS.values()} | {
        "qtrade.composite.multifactor_equal_rank"}
        and _identity(value["factor_version"]) and _identity(value["definition_sha256"]),
        "REFERENCE_INVALID")
    return dict(value)


def validate_definition(definition: Any, *, verify_runtime_source: bool = True) -> FactorDefinition:
    """Only the exact trusted built-in contracts are executable, even after rehashing."""
    _require(isinstance(definition, dict), "DEFINITION_REQUIRED")
    try:
        encoded = canonical(definition)
        value = cast(FactorDefinition, json.loads(encoded))
        source = value["source"]
        revision, sources = source["revision"], source["files"]
        _require(isinstance(revision, str)
                 and re.fullmatch(r"[a-f0-9]{40}", revision) is not None,
                 "SOURCE_REVISION_INVALID")
        _require(isinstance(sources, dict) and set(sources) == set(_SOURCE_FILES)
                 and all(_identity(item) for item in sources.values()), "SOURCE_FILES_INVALID")
        _require(value["price_basis"] in PRICE_BASES, "PRICE_BASIS_UNSUPPORTED")
        expected = next((row for row in _definitions(revision, value["price_basis"], sources)
                         if row["alias"] == value["alias"]), None)
        _require(expected is not None and canonical(expected) == encoded,
                 "DEFINITION_NOT_TRUSTED_OR_IDENTITY_INVALID")
        if verify_runtime_source:
            _require(sources == _source_hashes(), "RUNTIME_SOURCE_MISMATCH")
    except (KeyError, TypeError, OverflowError, RecursionError) as error:
        raise ValueError("FACTOR_DEFINITION_INVALID") from error
    return value


def evaluate_builtin(definition: FactorDefinition,
                     histories: dict[str, tuple[Decimal, ...]]) -> dict[str, Decimal]:
    """Score already admitted histories; the evaluator owns calendars and clocks."""
    _disabled()
    value = validate_definition(definition)
    _require(isinstance(histories, dict) and 1 <= len(histories) <= 6000, "HISTORIES_INVALID")
    for symbol, closes in histories.items():
        _require(isinstance(symbol, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,40}", symbol)
                 is not None and isinstance(closes, tuple)
                 and value["required_closes"] <= len(closes) <= 4096,
                 "HISTORY_IDENTITY_OR_WINDOW_INVALID")
        _require(all(isinstance(item, Decimal) and item.is_finite() and item > 0
                     for item in closes),
                 "CLOSE_VALUE_INVALID")
    context = Context(prec=28, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999,
                      capitals=1, clamp=0, flags=[],
                      traps=[DivisionByZero, InvalidOperation, Overflow])
    with localcontext(context):
        aliases = tuple(_BUILTINS) if value["alias"] == _COMPOSITE else (value["alias"],)
        scores = {}
        for alias in aliases:
            _, name, lookback, _ = _BUILTINS[alias]
            spec = PriceFactorSpec(name, lookback)
            scores[alias] = {symbol: spec.value(histories[symbol][-spec.required_closes:])
                             for symbol in sorted(histories)}
        if value["alias"] != _COMPOSITE:
            return scores[value["alias"]]
        ranks = [average_ranks(scores[alias]) for alias in aliases]
        return {symbol: sum((rank[symbol] for rank in ranks), Decimal(0)) / len(ranks)
                for symbol in sorted(histories)}


class FactorRegistry:
    """Immutable version records in an explicit isolated local SQLite database."""

    def __init__(self, database: Path, *, read_only: bool = False):
        _disabled()
        _database_path(database)
        self.database = database.resolve()
        self.read_only = read_only
        if read_only:
            _require(self.database.is_file(), "READ_ONLY_DATABASE_MISSING")
            return
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS factor_definitions (
                factor_id TEXT NOT NULL, factor_version TEXT NOT NULL,
                definition_sha256 TEXT NOT NULL UNIQUE, definition_json TEXT NOT NULL,
                registered_at TEXT NOT NULL, PRIMARY KEY(factor_id,factor_version))""")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        _database_path(self.database)
        connection = sqlite3.connect(
            self.database.as_uri() + "?mode=ro" if self.read_only else str(self.database),
            timeout=10, uri=self.read_only,
        )
        connection.row_factory = sqlite3.Row
        try:
            if self.read_only:
                connection.execute("PRAGMA query_only=ON")
                connection.execute("BEGIN")
            else:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _register(self, definitions: tuple[FactorDefinition, ...], *,
                  now: datetime) -> tuple[FactorRef, ...]:
        _disabled()
        _require(not self.read_only, "REGISTRY_READ_ONLY")
        _require(isinstance(now, datetime) and now.tzinfo is not None
                 and now.utcoffset() is not None, "REGISTRATION_TIMEZONE_REQUIRED")
        checked = tuple(validate_definition(value) for value in definitions)
        with self.connect() as connection:
            for value in checked:
                raw = canonical(value).decode()
                reference = factor_ref(value)
                prior = connection.execute(
                    "SELECT definition_json FROM factor_definitions "
                    "WHERE factor_id=? AND factor_version=?",
                    (reference["factor_id"], reference["factor_version"]),
                ).fetchone()
                for dependency in value["dependencies"]:
                    stored = connection.execute(
                        "SELECT definition_json FROM factor_definitions "
                        "WHERE factor_id=? AND factor_version=? AND definition_sha256=?",
                        tuple(dependency[key] for key in (
                            "factor_id", "factor_version", "definition_sha256")),
                    ).fetchone()
                    _require(stored is not None, "DEPENDENCY_NOT_REGISTERED")
                    dependency_value = validate_definition(json.loads(stored["definition_json"]))
                    _require(factor_ref(dependency_value) == dependency,
                             "DEPENDENCY_IDENTITY_MISMATCH")
                if prior is not None:
                    _require(prior["definition_json"] == raw, "IMMUTABLE_VERSION_CONFLICT")
                    continue
                connection.execute("INSERT INTO factor_definitions VALUES(?,?,?,?,?)", (
                    reference["factor_id"], reference["factor_version"],
                    reference["definition_sha256"], raw, now.isoformat(),
                ))
        return tuple(factor_ref(value) for value in checked)

    def register(self, definition: FactorDefinition, *, now: datetime) -> FactorRef:
        return self._register((definition,), now=now)[0]

    def register_builtins(self, *, source_revision: str, price_basis: str,
                          now: datetime) -> tuple[FactorRef, ...]:
        return self._register(builtin_definitions(source_revision=source_revision,
                                                 price_basis=price_basis), now=now)

    def get(self, reference: FactorRef) -> FactorDefinition:
        checked = _checked_ref(reference)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT definition_json FROM factor_definitions "
                "WHERE factor_id=? AND factor_version=?",
                (checked["factor_id"], checked["factor_version"]),
            ).fetchone()
            _require(row is not None, "VERSION_NOT_REGISTERED")
            value = validate_definition(json.loads(row["definition_json"]),
                                        verify_runtime_source=False)
            _require(factor_ref(value) == checked, "REFERENCE_IDENTITY_MISMATCH")
            for dependency in value["dependencies"]:
                dependency_row = connection.execute(
                    "SELECT definition_json FROM factor_definitions "
                    "WHERE factor_id=? AND factor_version=? AND definition_sha256=?",
                    tuple(dependency[key] for key in (
                        "factor_id", "factor_version", "definition_sha256")),
                ).fetchone()
                _require(dependency_row is not None, "DEPENDENCY_NOT_REGISTERED")
                dependency_value = validate_definition(
                    json.loads(dependency_row["definition_json"]), verify_runtime_source=False,
                )
                _require(factor_ref(dependency_value) == dependency, "DEPENDENCY_IDENTITY_MISMATCH")
        return value
