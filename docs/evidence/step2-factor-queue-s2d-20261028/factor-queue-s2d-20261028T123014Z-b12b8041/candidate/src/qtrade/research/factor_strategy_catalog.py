"""Bounded published factor descriptors; structural preview is not local admission."""
from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.factor_evaluation import LIMITATIONS
from qtrade.research.factor_strategy_contract import (
    _SOURCE_FILES,
    _SPEC_FIELDS,
    FAMILY,
    _number,
    _segments,
    preview_strategy,
    validate_strategy,
)
from qtrade.research.strategy_contract import validate_data_binding
from qtrade.research.workflow import canonical, digest

SCHEMA = "qtrade.factor-strategy-catalog.v1"
KIND = "FACTOR_STRATEGY_REGISTER_V1"
REQUEST_SCHEMA = "qtrade.factor-strategy-register-request.v1"
MAX_CATALOG_BYTES = 900_000
MAX_WIRE_BYTES = 1_000_000
MAX_REQUEST_BYTES = 16_000


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError("FACTOR_CATALOG_" + code)


def _identity(value: Any, length: int = 64) -> bool:
    return type(value) is str and re.fullmatch(r"[a-f0-9]{" + str(length) + "}", value) is not None


def _clone(value: Any) -> Any:
    cells = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal cells
        cells += 1
        require(cells <= 120_000 and depth <= 25, "TREE_BOUND")
        if type(item) is dict:
            require(all(type(key) is str for key in item), "JSON_KEYS")
            for child in item.values():
                visit(child, depth + 1)
        elif type(item) is list:
            for child in item:
                visit(child, depth + 1)
        else:
            require(item is None or type(item) in (bool, int, str), "JSON_TYPE")

    visit(value, 0)
    raw = canonical(value)
    require(len(raw) <= MAX_CATALOG_BYTES, "BYTE_BOUND")
    require(len(json.dumps(value, ensure_ascii=False, sort_keys=True,
                           indent=2, allow_nan=False).encode()) + 1 <= MAX_WIRE_BYTES,
            "WIRE_BYTE_BOUND")
    return json.loads(raw)


def catalog_identity(value: dict[str, Any]) -> str:
    """Heartbeat changes freshness, never the identity of the available inputs."""
    body = {key: item for key, item in value.items()
            if key not in {"evidence_sha256", "catalog_sha256"}}
    body["worker"] = {key: item for key, item in body["worker"].items()
                      if key != "heartbeat_at"}
    return digest(body)


def validate_catalog(value: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Validate content and aware clocks. Stale archives remain readable.

    Submission adds its existing freshness/worker-source/capacity gates. Neither
    a checksum nor this public descriptor substitutes for local worker evidence.
    """
    value = _clone(value)
    require(type(value) is dict and set(value) == {*AUTHORITY, "schema_version", "worker",
        "datasets", "templates", "parents", "limitations", "catalog_sha256", "evidence_sha256"},
        "FIELDS")
    require(value["schema_version"] == SCHEMA and all(type(value.get(key)) is type(expected)
        and value[key] == expected for key, expected in AUTHORITY.items()), "AUTHORITY")
    require(signed({key: item for key, item in value.items() if key != "evidence_sha256"})
            == value and value["catalog_sha256"] == catalog_identity(value), "HASH")
    clock = now or datetime.now(UTC)
    require(clock.tzinfo is not None and clock.utcoffset() is not None, "NOW_AWARE")
    worker = value["worker"]
    require(type(worker) is dict and set(worker) == {"heartbeat_at", "source_revision",
        "implementation_sources", "supported_kinds"} and _identity(worker["source_revision"], 40),
        "WORKER")
    require(type(worker["heartbeat_at"]) is str, "HEARTBEAT")
    heartbeat = datetime.fromisoformat(worker["heartbeat_at"])
    require(heartbeat.tzinfo is not None and heartbeat.utcoffset() is not None
            and heartbeat <= clock + timedelta(seconds=60), "HEARTBEAT")
    require(worker["supported_kinds"] in ([], [KIND]), "SUPPORTED_KINDS")
    files = worker["implementation_sources"]
    require(type(files) is dict and set(files) == set(_SOURCE_FILES)
            and all(_identity(item) for item in files.values()), "SOURCE_FILES")
    require(value["limitations"] == LIMITATIONS, "LIMITATIONS")
    datasets, templates, parents = value["datasets"], value["templates"], value["parents"]
    require(type(datasets) is list and 1 <= len(datasets) <= 4
            and type(templates) is list and 1 <= len(templates) <= 16
            and type(parents) is list and len(parents) <= 100, "SCOPE_BOUND")
    data = {}
    for dataset in datasets:
        require(type(dataset) is dict and set(dataset) == {"data_binding", "calendar",
                                                          "calendar_sha256"}, "DATASET")
        binding = validate_data_binding(dataset["data_binding"])
        calendar = dataset["calendar"]
        require(type(calendar) is list and 1 <= len(calendar) <= 4096 and all(type(day) is str
            and date.fromisoformat(day).isoformat() == day for day in calendar)
            and calendar == sorted(set(calendar)) and calendar[0] == binding["start"]
            and calendar[-1] == binding["actual_end"]
            and dataset["calendar_sha256"] == digest(calendar), "CALENDAR")
        require(binding["data_version_id"] not in data, "DUPLICATE_DATASET")
        data[binding["data_version_id"]] = dataset
    choices = set()
    for template in templates:
        template = validate_strategy(template)
        require(template["source"]["revision"] == worker["source_revision"]
                and template["source"]["files"] == files, "TEMPLATE_SOURCE")
        binding = template["evaluation_context"]["data_binding"]
        require(binding["data_version_id"] in data and data[binding["data_version_id"]][
            "data_binding"] == binding and template["spec"]["parent"] is None, "TEMPLATE_DATA")
        key = (digest(template["spec"]["factor_ref"]), digest(template["spec"]["evaluation_ref"]))
        require(key not in choices, "AMBIGUOUS_TEMPLATE")
        choices.add(key)
        require(preview_strategy(template, template["spec"],
            calendar=data[binding["data_version_id"]]["calendar"]) == template, "TEMPLATE_CALENDAR")
    identities = set()
    for parent in parents:
        require(type(parent) is dict and set(parent) == {"strategy_id", "strategy_version",
            "contract_sha256", "family", "status", "role"} and parent["family"] == FAMILY
            and parent["status"] == "EXPERIMENT" and parent["role"] == "NONE"
            and _identity(parent["strategy_version"]) and _identity(parent["contract_sha256"])
            and parent["strategy_id"] == "factor-" + parent["strategy_version"], "PARENT")
        require(parent["strategy_id"] not in identities, "DUPLICATE_PARENT")
        identities.add(parent["strategy_id"])
    return cast(dict[str, Any], value)


def preview_from_catalog(value: Any, spec: dict[str, Any], *,
                         now: datetime | None = None) -> dict[str, Any]:
    value = validate_catalog(value, now=now)
    require(type(spec) is dict, "SPEC")
    choices = [row for row in value["templates"] if row["spec"]["factor_ref"] == spec.get(
        "factor_ref") and row["spec"]["evaluation_ref"] == spec.get("evaluation_ref")]
    require(len(choices) == 1, "EXACT_REFERENCE_REQUIRED")
    template = choices[0]
    parent = spec.get("parent")
    if parent is not None:
        require(type(parent) is dict and any(all(parent.get(key) == row[key]
            for key in ("strategy_id", "strategy_version", "contract_sha256"))
            for row in value["parents"]), "PARENT_NOT_PUBLISHED")
    binding = template["evaluation_context"]["data_binding"]
    calendar = next(row["calendar"] for row in value["datasets"]
                    if row["data_binding"] == binding)
    return preview_strategy(template, spec, calendar=calendar)


def validate_registration_request(value: Any) -> dict[str, Any]:
    value = _clone(value)
    require(type(value) is dict and set(value) == {"schema_version", "kind", "job_id", "actor",
        "factor_spec", "factor_spec_sha256", "expected_strategy_version", "expected_catalog_sha256",
        "source_revision", "data_version_id", "live_trading_enabled"}, "REQUEST_FIELDS")
    require(len(canonical(value)) <= MAX_REQUEST_BYTES and value["schema_version"] == REQUEST_SCHEMA
        and value["kind"] == KIND and value["live_trading_enabled"] is False
        and _identity(value["job_id"], 32) and type(value["actor"]) is str
        and re.fullmatch(r"[A-Za-z0-9._@-]{1,80}", value["actor"]) is not None
        and _identity(value["source_revision"], 40), "REQUEST_AUTHORITY")
    for key in ("factor_spec_sha256", "expected_strategy_version", "expected_catalog_sha256",
                "data_version_id"):
        require(_identity(value[key]), "REQUEST_HASH")
    require(type(value["factor_spec"]) is dict and digest(value["factor_spec"])
            == value["factor_spec_sha256"], "REQUEST_SPEC_HASH")
    spec = value["factor_spec"]
    require(set(spec) == _SPEC_FIELDS and type(spec["name"]) is str and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,59}", spec["name"]) is not None, "SPEC_FIELDS")
    ref = spec["factor_ref"]
    require(type(ref) is dict and set(ref) == {"factor_id", "factor_version", "definition_sha256"}
        and ref["factor_id"] in {"qtrade.price.momentum20", "qtrade.price.reversal5",
        "qtrade.price.lowvol20", "qtrade.composite.multifactor_equal_rank"}
        and all(_identity(ref[key]) for key in ("factor_version", "definition_sha256")),
        "FACTOR_REF")
    ref = spec["evaluation_ref"]
    require(type(ref) is dict and set(ref) == {"evaluation_id", "specification_sha256",
        "report_manifest_sha256"} and type(ref["evaluation_id"]) is str and re.fullmatch(
        r"factor-eval-[a-f0-9]{32}", ref["evaluation_id"]) is not None and all(_identity(ref[key])
        for key in ("specification_sha256", "report_manifest_sha256")), "EVALUATION_REF")
    require(spec["signal_kind"] in {"FACTOR_SINGLE", "FACTOR_COMPOSITE"}, "SIGNAL_KIND")
    # Cheap queue admission uses the existing scalar validators. Actual calendar,
    # universe, exact versions and parent lifecycle are re-resolved by the child.
    _segments(spec["segments"], {"start": "1900-01-01", "actual_end": "2200-01-01"})
    portfolio = spec["portfolio"]
    require(type(portfolio) is dict and set(portfolio) == {"method", "topk", "n_drop",
        "hold_thresh", "risk_degree"} and portfolio["method"] == "TopkDropout", "PORTFOLIO")
    for key, low, high in (("topk", 1, 100), ("n_drop", 1, 20), ("hold_thresh", 2, 20)):
        require(type(portfolio[key]) is int and low <= portfolio[key] <= high, "PORTFOLIO_RANGE")
    require(portfolio["n_drop"] <= portfolio["topk"], "PORTFOLIO_DROP")
    _number(portfolio["risk_degree"], "0.1", "0.95")
    execution = spec["execution"]
    require(type(execution) is dict and set(execution) == {"buy_cost_bps", "sell_cost_bps",
        "min_cost", "initial_cash", "slippage_bps"}, "EXECUTION")
    for key, cost_low, cost_high in (("buy_cost_bps", "1", "100"), ("sell_cost_bps", "1", "100"),
        ("min_cost", "0", "100"), ("initial_cash", "100000", "100000000"),
        ("slippage_bps", "0", "0")):
        _number(execution[key], cost_low, cost_high)
    parent = spec["parent"]
    if parent is not None:
        require(type(parent) is dict and set(parent) == {"strategy_id", "strategy_version",
            "contract_sha256", "reason", "hypothesis", "change_summary"}
            and _identity(parent["strategy_version"]) and _identity(parent["contract_sha256"])
            and parent["strategy_id"] == "factor-" + parent["strategy_version"], "SPEC_PARENT")
        require(all(type(parent[key]) is str and 1 <= len(parent[key].strip())
            <= len(parent[key]) <= 500 for key in ("reason", "hypothesis", "change_summary")),
            "SPEC_LINEAGE")
    return cast(dict[str, Any], value)
