"""Freeze supported Qlib research inputs and adapt them to the existing registry.

Reuse audit: workflow owns the model/portfolio allowlist; verify_version owns
Provider integrity; StrategyRegistry owns lifecycle. vn.py gateway components do
not establish research identity and are deliberately absent from this boundary.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.data_versions import verify_version
from qtrade.research.experiment import TimeSplit
from qtrade.research.model_registry import checked_document, checked_identity
from qtrade.strategies.registry import StrategyRecord, StrategyRegistry, StrategySpec

FEATURE_SET_VERSION = "qlib-0.9.7-Alpha158-v1"
DATA_SCHEMA = "qtrade.qlib-research-data-binding.v1"
STRATEGY_SCHEMA = "qtrade.qlib-strategy-definition.v1"
LABEL = "Ref($open,-2)/Ref($open,-1)-1"


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError("RESEARCH_CONTRACT_" + code)


def clone(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))


def checked(value: Any) -> dict[str, Any]:
    require(isinstance(value, dict), "DOCUMENT_REQUIRED")
    checked_document(value)
    require(all(type(value.get(key)) is type(expected) for key, expected in AUTHORITY.items()),
            "AUTHORITY_TYPE_INVALID")
    return dict(value)


def validate_data_binding(value: Any) -> dict[str, Any]:
    value = checked(value)
    require(set(value) == {*AUTHORITY, "evidence_sha256", "schema_version", "dataset_id",
        "data_version_id", "manifest_sha256", "dataset_sha256", "source_snapshot_sha256",
        "ready_event_id", "actual_end", "start", "supported_universe_sha256", "supported_symbols"},
        "DATA_BINDING_FIELDS_INVALID")
    require(value["schema_version"] == DATA_SCHEMA, "DATA_SCHEMA_INVALID")
    for key in ("data_version_id", "manifest_sha256", "dataset_sha256", "source_snapshot_sha256",
                "ready_event_id", "supported_universe_sha256"):
        checked_identity(value[key])
    require(value["dataset_id"] == "qlib-version-" + value["data_version_id"],
            "DATASET_ID_INVALID")
    for key in ("start", "actual_end"):
        require(isinstance(value[key], str) and date.fromisoformat(value[key]).isoformat()
                == value[key], "DATA_DATE_INVALID")
    require(value["start"] <= value["actual_end"]
            and type(value["supported_symbols"]) is int
            and 1 <= value["supported_symbols"] <= 6000, "DATA_SCOPE_INVALID")
    return dict(clone(value))


def supported_universe(root: Path) -> tuple[str, ...]:
    """Read the admitted Qlib instrument intervals, not today's mutable target pool."""
    path = root / "provider/instruments/workflow_supported.txt"
    require(not path.is_symlink() and path.stat().st_size <= 2_000_000,
            "UNIVERSE_FILE_BOUND_OR_PATH_INVALID")
    codes = set()
    lines = path.read_text().splitlines()
    require(1 <= len(lines) <= 50_000, "UNIVERSE_ROW_BOUND_INVALID")
    for line in lines:
        fields = line.split("\t")
        require(len(fields) == 3, "UNIVERSE_INTERVAL_INVALID")
        code, start, end = fields
        require(re.fullmatch(r"(?:SH|SZ)\d{6}", code) is not None
                and not code.startswith("SH688")
                and date.fromisoformat(start).isoformat() == start
                and date.fromisoformat(end).isoformat() == end and start <= end,
                "UNSUPPORTED_UNIVERSE_INTERVAL")
        codes.add(code)
    require(len(codes) <= 6000, "UNIVERSE_BOUND_EXCEEDED")
    return tuple(sorted(codes))


def _admission(database: Path, identity: str) -> dict[str, Any]:
    require(database.is_file() and not database.is_symlink(), "DATABASE_PATH_INVALID")
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True,
                                timeout=3)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        versions = connection.execute("SELECT * FROM data_versions WHERE version_id=? LIMIT 2",
                                      (identity,)).fetchall()
        events = connection.execute("SELECT event_id,payload FROM data_events WHERE version_id=? "
            "AND kind='QLIB_DATA_READY' ORDER BY sequence LIMIT 2", (identity,)).fetchall()
        require(len(versions) == len(events) == 1, "UNIQUE_READY_ADMISSION_REQUIRED")
        return {"version": dict(versions[0]), "event": dict(events[0])}


def ready_dataset(root: Path, data_database: Path) -> dict[str, Any]:
    """Full immutable archive verification plus two short RO admission snapshots.

    A historical admitted version need not still be the current head. This is
    dataset selection for research, never current serving/execution admission.
    """
    require(not root.is_symlink() and not (root / "version.json").is_symlink(),
            "VERSION_PATH_INVALID")
    identity = checked_identity(root.name)
    before = _admission(data_database, identity)
    version = verify_version(root)
    checked(version)
    require(version["version_id"] == identity, "VERSION_DIRECTORY_IDENTITY_MISMATCH")
    event = checked(json.loads(before["event"]["payload"]))
    row = before["version"]
    require(event["evidence_sha256"] == before["event"]["event_id"]
            and event["kind"] == "QLIB_DATA_READY" and event["version_id"] == identity
            and row["manifest_sha256"] == event["manifest_sha256"] == version["evidence_sha256"]
            and row["actual_end"] == event["actual_end"] == version["actual_end"]
            and row["admitted_at"] == event["admitted_at"], "READY_ADMISSION_BINDING_MISMATCH")
    admitted = datetime.fromisoformat(event["admitted_at"])
    require(admitted.tzinfo is not None
            and datetime.fromisoformat(version["created_at"]) <= admitted <= datetime.now(UTC),
            "READY_ADMISSION_CLOCK_INVALID")
    codes = supported_universe(root)
    require(all(code[2:] in version["historical_symbols"] for code in codes),
            "UNIVERSE_OUTSIDE_ADMITTED_DATA")
    calendar = (root / "provider/calendars/day.txt").read_text().splitlines()
    require(bool(calendar) and calendar[0] <= version["actual_end"], "CALENDAR_RANGE_INVALID")
    require(_admission(data_database, identity) == before, "ADMISSION_CHANGED_DURING_VERIFY")
    return validate_data_binding(signed({**AUTHORITY, "schema_version": DATA_SCHEMA,
        "dataset_id": "qlib-version-" + identity, "data_version_id": identity,
        "manifest_sha256": version["evidence_sha256"],
        "dataset_sha256": version["files"]["dataset.json"],
        "source_snapshot_sha256": version["bindings"]["source_snapshot_sha256"],
        "ready_event_id": event["evidence_sha256"], "actual_end": version["actual_end"],
        "start": calendar[0], "supported_symbols": len(codes),
        "supported_universe_sha256": signed({"instruments": list(codes)})["evidence_sha256"]}))


def verify_bound_spec(spec: dict[str, Any], root: Path,
                      data_database: Path) -> dict[str, Any]:
    """Reject self-signed client metadata unless it matches actual admitted bytes."""
    from qtrade.research.workflow import validate_spec

    spec = validate_spec(spec)
    require(spec.get("data_binding") == ready_dataset(root, data_database),
            "LOCAL_READY_DATASET_MISMATCH")
    return spec


def bound_default_spec(binding: dict[str, Any], model: str = "lightgbm") -> dict[str, Any]:
    """Build a new explicit selection; never rewrite an existing experiment."""
    from qtrade.research.workflow import default_spec, validate_spec

    binding = validate_data_binding(binding)
    spec = default_spec(model)
    spec.update(dataset_id=binding["dataset_id"], data_binding=binding,
                source_snapshot_sha256=binding["source_snapshot_sha256"],
                feature_set_version=FEATURE_SET_VERSION)
    spec["segments"]["test"][1] = binding["actual_end"]
    return validate_spec(spec)


def strategy_choices(binding: dict[str, Any], source_revision: str) -> list[dict[str, Any]]:
    """Full selectable definitions, not an enum masquerading as a frozen strategy."""
    from qtrade.research.workflow import MODELS

    return [freeze_strategy(bound_default_spec(binding, model), source_revision)
            for model in MODELS]


def freeze_strategy(spec: dict[str, Any], source_revision: str) -> dict[str, Any]:
    """Pure deterministic freeze. Workers must independently recheck ready_dataset."""
    from qtrade.research.workflow import build_config, validate_spec

    spec = validate_spec(spec)
    require("data_binding" in spec and re.fullmatch("[a-f0-9]{40}", source_revision) is not None,
            "VERSION_BOUND_SPEC_AND_REVISION_REQUIRED")
    baseline = json.loads((Path(__file__).resolve().parents[3]
                          / "configs/qlib_daily_baseline.json").read_bytes())
    config = build_config(spec, baseline)
    config["infer_processors"].append({"class": "Float64Features",
                                      "module_path": "qtrade.research.qlib_processors"})
    config["online_feature_precision"] = "FLOAT64_AFTER_FROZEN_NORMALIZATION_V1"
    body = {**AUTHORITY, "schema_version": STRATEGY_SCHEMA, "spec": spec,
        "source_revision": source_revision,
        "training_config": config, "restore_contract": "FITTED_DATASETH_RMDLOADER_V1",
        "signal": {"kind": "QLIB_MODEL_SCORE", "model": spec["model"], "label": LABEL,
                   "model_artifact_id": None, "fitted_model_created_by_this_contract": False},
        "feature_set": {"id": "Alpha158", "version": FEATURE_SET_VERSION, "feature_count": 158,
                        "handler": "qlib.contrib.data.handler.Alpha158"},
        "portfolio": {"method": "TopkDropout", **config["portfolio"]},
        "execution": {"frequency": "day", "deal_price": "open", "trade_unit": 100,
            "limit_threshold": ["$buy_block", "$sell_block"],
            "volume_threshold": ["cum", "$capacity"], "impact_cost": 0.0,
            "buy_cost_bps": spec["buy_cost_bps"], "sell_cost_bps": spec["sell_cost_bps"],
            "min_cost": spec["min_cost"], "timing": "PREVIOUS_SIGNAL_NEXT_SESSION_OPEN",
            "rules_certified": False},
        "research_only": True, "paper_admission": False, "model_activation": False}
    identity = signed(body)["evidence_sha256"]
    return signed({**body, "strategy_version": identity, "strategy_id": "qlib-" + identity})


def validate_strategy(value: dict[str, Any]) -> dict[str, Any]:
    value = checked(value)
    require(freeze_strategy(value["spec"], value["source_revision"]) == value,
            "STRATEGY_DEFINITION_CHANGED")
    return dict(clone(value))


def strategy_spec(definition: dict[str, Any], *, universe: tuple[str, ...],
                  now: datetime) -> StrategySpec:
    """Translate the complete frozen definition to the existing lifecycle identity."""
    definition = validate_strategy(definition)
    binding = definition["spec"]["data_binding"]
    require(universe == tuple(sorted(set(universe)))
            and len(universe) == binding["supported_symbols"]
            and signed({"instruments": list(universe)})["evidence_sha256"]
            == binding["supported_universe_sha256"], "REGISTERED_UNIVERSE_MISMATCH")
    spec = definition["spec"]
    segments = spec["segments"]
    split = TimeSplit(*(date.fromisoformat(day) for name in ("train", "valid", "test")
                        for day in segments[name]))
    return StrategySpec(strategy_id=definition["strategy_id"], name=spec["name"],
        family="QLIB_ALPHA158", version=definition["strategy_version"], parent_strategy_id=None,
        git_commit=definition["source_revision"], data_version=binding["data_version_id"],
        universe=universe, factor_set=(FEATURE_SET_VERSION,), model=spec["model"],
        portfolio_rule="TopkDropout",
        parameters=(("contract_sha256", definition["evidence_sha256"]),),
        split=split, created_at=now, updated_at=now)


def register_strategy(registry: StrategyRegistry, definition: dict[str, Any], *,
                      job_id: str, actor: str, universe: tuple[str, ...],
                      now: datetime) -> StrategyRecord:
    """Atomically record an accepted research job; never approve/activate/recover."""
    require(os.environ.get("LIVE_TRADING_ENABLED", "false").lower() == "false",
            "LIVE_DISABLED_REQUIRED")
    record = strategy_spec(definition, universe=universe, now=now)
    return registry.register_research_experiment(record, contract=definition,
                                                 job_id=job_id, actor=actor)
