"""Frozen nonproduction factor strategies, with independently resolved evidence.

Reuse audit: FactorRegistry owns the four trusted definitions; the evaluator
owns saved diagnostics; ready_dataset owns archive admission; StrategyRegistry
owns identity/lifecycle transactions. vn.py order/gateway components do not
establish any of those research identities and are absent from this boundary.
No signal execution, fitting, queue submission or lifecycle promotion occurs.
"""
from __future__ import annotations

import json
import os
import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.experiment import TimeSplit
from qtrade.research.factor_evaluation import (
    LIMITATIONS,
    FactorEvaluationStore,
    _cost_policy,
    _label_policy,
    _split_policy,
    validate_report,
)
from qtrade.research.factor_registry import FactorRegistry, factor_ref, validate_definition
from qtrade.research.strategy_contract import (
    ready_dataset,
    supported_universe,
    validate_data_binding,
)
from qtrade.research.workflow import canonical, digest
from qtrade.strategies.registry import (
    StrategyLineage,
    StrategyRecord,
    StrategyRegistry,
    StrategySpec,
)

SCHEMA = "qtrade.factor-strategy-definition.v1"
FAMILY = "QTRADE_EXISTING_FACTOR"
MAX_BYTES = 256_000
_SOURCE_FILES = (
    "src/qtrade/research/factor_strategy_contract.py",
    "src/qtrade/research/factor_registry.py",
    "src/qtrade/research/factor_evaluation.py",
    "src/qtrade/research/factors.py",
    "src/qtrade/research/signal_baselines.py",
    "src/qtrade/research/metrics.py",
    "src/qtrade/research/strategy_contract.py",
    "src/qtrade/research/data_versions.py",
    "src/qtrade/research/model_registry.py",
    "src/qtrade/research/experiment.py",
    "src/qtrade/research/workflow.py",
    "src/qtrade/strategies/registry.py",
    "src/qtrade/monitoring/data_freshness.py",
)
_SPEC_FIELDS = {"name", "factor_ref", "evaluation_ref", "signal_kind", "segments",
                "portfolio", "execution", "parent"}
_CONTEXT_FIELDS = {"data_binding", "ordered_factor_refs", "instruments", "decision_scope",
    "label", "diagnostic_cost_policy", "diagnostic_split_policy", "knowledge_mode",
    "calendar_sha256", "eligibility_ledger_sha256", "factor_definitions_sha256",
    "selected_factor_summary_sha256", "selected_valid_ic_days", "evaluation_state",
    "computed_at", "limitations"}


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError("FACTOR_STRATEGY_" + code)


def _disabled() -> None:
    _require(os.environ.get("LIVE_TRADING_ENABLED", "false").lower() == "false",
             "LIVE_DISABLED_REQUIRED")


def _copy(value: Any) -> Any:
    """Bound the actual JSON tree; reject bool/numeric and key coercion aliases."""
    cells = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal cells
        cells += 1
        _require(cells <= 30_000 and depth <= 20, "DOCUMENT_BOUND_EXCEEDED")
        if type(item) is dict:
            _require(all(type(key) is str for key in item), "JSON_KEYS_REQUIRED")
            for child in item.values():
                visit(child, depth + 1)
        elif type(item) is list:
            for child in item:
                visit(child, depth + 1)
        else:
            _require(item is None or type(item) in (str, int, bool), "JSON_TYPE_INVALID")

    visit(value, 0)
    raw = canonical(value)
    _require(len(raw) <= MAX_BYTES, "DOCUMENT_BOUND_EXCEEDED")
    return json.loads(raw)


def _hash(value: Any) -> bool:
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _instant(value: Any) -> datetime:
    _require(type(value) is str, "CLOCK_INVALID")
    result = datetime.fromisoformat(value)
    _require(result.tzinfo is not None and result.utcoffset() is not None
             and result <= datetime.now(UTC), "CLOCK_INVALID")
    return result


def _sources() -> dict[str, str]:
    root = Path(__file__).resolve().parents[3]
    return {name: sha256((root / name).read_bytes()).hexdigest() for name in _SOURCE_FILES}


def _evaluation_ref(report: dict[str, Any]) -> dict[str, str]:
    return {key: report[key] for key in (
        "evaluation_id", "specification_sha256", "report_manifest_sha256")}


def _context(report: dict[str, Any], reference: dict[str, str]) -> dict[str, Any]:
    docs = report["documents"]
    specification = docs["specification"]
    request = specification["request"]
    _require(reference in request["ordered_factor_refs"], "FACTOR_NOT_EVALUATED")
    index = request["ordered_factor_refs"].index(reference)
    decisions = request["decisions"]
    summary = docs["summary"]["factors"][str(index)]
    return {"data_binding": specification["data_binding"],
        "ordered_factor_refs": request["ordered_factor_refs"],
        "instruments": request["instruments"], "decision_scope": {
            "first_session": decisions[0]["session"], "last_session": decisions[-1]["session"],
            "count": len(decisions), "decisions_sha256": digest(decisions)},
        "label": specification["label"], "diagnostic_cost_policy": specification["cost_policy"],
        "diagnostic_split_policy": specification["split_policy"],
        "knowledge_mode": request["knowledge_mode"],
        "calendar_sha256": specification["calendar_sha256"],
        "eligibility_ledger_sha256": report["report_manifest"]["eligibility_ledger"],
        "factor_definitions_sha256": report["report_manifest"]["factor_definitions"],
        "selected_factor_summary_sha256": digest(summary),
        "selected_valid_ic_days": summary["ic"]["valid_days"],
        "evaluation_state": report["state"], "computed_at": report["computed_at"],
        "limitations": docs["summary"]["limitations"]}


def _number(value: Any, low: str, high: str) -> None:
    _require(type(value) is str and re.fullmatch(r"(?:0|[1-9]\d{0,9})(?:\.\d{1,8})?",
                                               value) is not None, "DECIMAL_STRING_REQUIRED")
    try:
        number = Decimal(value)
        _require(Decimal(low) <= number <= Decimal(high), "NUMBER_OUT_OF_RANGE")
    except InvalidOperation as error:
        raise ValueError("FACTOR_STRATEGY_NUMBER_INVALID") from error


def _segments(value: Any, binding: dict[str, Any]) -> TimeSplit:
    _require(type(value) is dict and set(value) == {"train", "valid", "test"},
             "SEGMENTS_INVALID")
    for pair in value.values():
        _require(type(pair) is list and len(pair) == 2 and all(type(day) is str
            and date.fromisoformat(day).isoformat() == day for day in pair), "SEGMENTS_INVALID")
    result = TimeSplit(*(date.fromisoformat(day) for name in ("train", "valid", "test")
                         for day in value[name]))
    _require(binding["start"] <= value["train"][0]
             and value["test"][1] <= binding["actual_end"], "SPLIT_OUTSIDE_READY_DATA")
    return result


def _compose(spec: dict[str, Any], definition: dict[str, Any], context: dict[str, Any],
             source: dict[str, Any]) -> dict[str, Any]:
    _require(type(spec) is dict and set(spec) == _SPEC_FIELDS, "SPEC_FIELDS_INVALID")
    _require(type(spec["name"]) is str and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,59}", spec["name"]) is not None, "NAME_INVALID")
    definition = validate_definition(definition, verify_runtime_source=False)
    reference = factor_ref(definition)
    _require(spec["factor_ref"] == reference, "FACTOR_REFERENCE_MISMATCH")
    kind = "FACTOR_COMPOSITE" if definition["dependencies"] else "FACTOR_SINGLE"
    _require(spec["signal_kind"] == kind, "SIGNAL_KIND_MISMATCH")
    ref = spec["evaluation_ref"]
    _require(type(ref) is dict and set(ref) == {"evaluation_id", "specification_sha256",
             "report_manifest_sha256"} and type(ref["evaluation_id"]) is str
             and re.fullmatch(r"factor-eval-[a-f0-9]{32}", ref["evaluation_id"]) is not None
             and all(_hash(ref[k]) for k in ("specification_sha256", "report_manifest_sha256")),
             "EVALUATION_REFERENCE_INVALID")
    _require(type(context) is dict and set(context) == _CONTEXT_FIELDS, "EVIDENCE_FIELDS_INVALID")
    binding = validate_data_binding(context["data_binding"])
    refs, instruments = context["ordered_factor_refs"], context["instruments"]
    _require(type(refs) is list and 1 <= len(refs) <= 4 and reference in refs
             and len({digest(ref) for ref in refs}) == len(refs), "EVALUATED_FACTORS_INVALID")
    for item in refs:
        _require(type(item) is dict and set(item) == set(reference)
                 and type(item["factor_id"]) is str
                 and item["factor_id"] in {"qtrade.price.momentum20", "qtrade.price.reversal5",
                     "qtrade.price.lowvol20", "qtrade.composite.multifactor_equal_rank"}
                 and _hash(item["factor_version"]) and _hash(item["definition_sha256"]),
                 "EVALUATED_FACTORS_INVALID")
    _require(type(instruments) is list and 1 <= len(instruments) <= binding["supported_symbols"]
             and all(type(s) is str and re.fullmatch(r"(?:SH|SZ)\d{6}", s) for s in instruments)
             and instruments == sorted(set(instruments)), "INSTRUMENT_SCOPE_INVALID")
    for key in ("calendar_sha256", "eligibility_ledger_sha256", "factor_definitions_sha256",
                "selected_factor_summary_sha256"):
        _require(_hash(context[key]), "EVIDENCE_HASH_INVALID")
    _require(context["knowledge_mode"] in {"OBSERVED_SYSTEM", "RETROSPECTIVE_RECONSTRUCTION"}
             and context["evaluation_state"] == "DIAGNOSTICS_COMPUTED"
             and type(context["selected_valid_ic_days"]) is int
             and 1 <= context["selected_valid_ic_days"] <= 2520
             and context["limitations"] == LIMITATIONS, "DIAGNOSTIC_EVIDENCE_INVALID")
    computed = _instant(context["computed_at"])
    label = context["label"]
    _require(type(label) is dict and type(label.get("horizon_sessions")) is int
             and 1 <= label["horizon_sessions"] <= 20
             and label == _label_policy(label["horizon_sessions"], label["maturity_barrier"])
             and _instant(label["maturity_barrier"]) <= computed
             and context["diagnostic_cost_policy"] == _cost_policy()
             and context["diagnostic_split_policy"] == _split_policy(), "DIAGNOSTIC_POLICY_INVALID")
    _segments(spec["segments"], binding)
    scope = context["decision_scope"]
    _require(type(scope) is dict and set(scope) == {"first_session", "last_session", "count",
             "decisions_sha256"} and type(scope["count"]) is int and 1 <= scope["count"] <= 2520
             and _hash(scope["decisions_sha256"]), "DECISION_SCOPE_INVALID")
    for key in ("first_session", "last_session"):
        _require(type(scope[key]) is str and date.fromisoformat(scope[key]).isoformat()
                 == scope[key], "DECISION_SCOPE_INVALID")
    _require(spec["segments"]["test"][0] <= scope["first_session"] <= scope["last_session"]
             <= spec["segments"]["test"][1], "DIAGNOSTIC_OUTSIDE_DECLARED_TEST")
    portfolio = spec["portfolio"]
    _require(type(portfolio) is dict and set(portfolio) == {
        "method", "topk", "n_drop", "hold_thresh", "risk_degree"}
        and portfolio["method"] == "TopkDropout", "PORTFOLIO_INVALID")
    for key, low, high in (("topk", 1, min(100, len(instruments))),
                           ("n_drop", 1, 20), ("hold_thresh", 2, 20)):
        _require(type(portfolio[key]) is int and low <= portfolio[key] <= high,
                 "PORTFOLIO_RANGE_INVALID")
    _require(portfolio["n_drop"] <= portfolio["topk"], "DROP_EXCEEDS_TOPK")
    _number(portfolio["risk_degree"], "0.1", "0.95")
    execution = spec["execution"]
    _require(type(execution) is dict and set(execution) == {
        "buy_cost_bps", "sell_cost_bps", "min_cost", "initial_cash", "slippage_bps"},
        "EXECUTION_FIELDS_INVALID")
    for key, cost_low, cost_high in (("buy_cost_bps", "1", "100"), ("sell_cost_bps", "1", "100"),
            ("min_cost", "0", "100"), ("initial_cash", "100000", "100000000"),
            ("slippage_bps", "0", "0")):
        _number(execution[key], cost_low, cost_high)
    parent = spec["parent"]
    if parent is not None:
        _require(type(parent) is dict and set(parent) == {"strategy_id", "strategy_version",
            "contract_sha256", "reason", "hypothesis", "change_summary"}
            and _hash(parent["strategy_version"]) and _hash(parent["contract_sha256"])
            and parent["strategy_id"] == "factor-" + parent["strategy_version"],
            "PARENT_REFERENCE_INVALID")
        for key in ("reason", "hypothesis", "change_summary"):
            _require(type(parent[key]) is str and 1 <= len(parent[key].strip()) <= 500
                     and len(parent[key]) <= 500, "PARENT_LINEAGE_INVALID")
    _require(type(source) is dict and set(source) == {"revision", "files", "binding"}
        and type(source["revision"]) is str
        and re.fullmatch(r"[a-f0-9]{40}", source["revision"]) is not None
        and source["binding"] == "EXACT_FILE_BYTES_REVISION_LABEL_NOT_GIT_CERTIFICATION"
        and type(source["files"]) is dict and set(source["files"]) == set(_SOURCE_FILES)
        and all(_hash(value) for value in source["files"].values()), "SOURCE_BINDING_INVALID")
    body = {**AUTHORITY, "schema_version": SCHEMA, "spec": spec,
        "factor_definition": definition, "evaluation_context": context, "source": source,
        "signal": {"kind": kind, "direction": "HIGH_SCORE_LONG_ONLY_NO_SIGN_SELECTION",
            "missing_policy": "EXCLUDE_UNAVAILABLE_NO_FILL",
            "dependency_refs": definition["dependencies"],
            "knowledge_mode": context["knowledge_mode"], "adapter_implemented": False},
        "split_policy": {"kind": "DECLARED_RESEARCH_SEGMENTS", "fitting": False,
            "unseen_test_claim": False, "diagnostic_reference_is_split_performance": False,
            "label_purge": "EXACT_CALENDAR_HORIZON_BEFORE_NEXT_SEGMENT"},
        "portfolio": {**portfolio, "rebalance": "EVERY_EXCHANGE_SESSION",
            "hold_thresh_semantics": "MINIMUM_HOLDING_BEFORE_DROPOUT_NOT_FIXED_EXIT"},
        "execution": {**execution, "frequency": "day", "deal_price": "open",
            "timing": "PREVIOUS_SIGNAL_NEXT_SESSION_OPEN", "trade_unit": 100,
            "limit_threshold": ["$buy_block", "$sell_block"],
            "volume_threshold": ["cum", "$capacity"], "impact_cost": "0",
            "rules_certified": False, "costs_applied_to_diagnostic": False,
            "backtest_implemented": False},
        "research_only": True, "paper_admission": False, "model_activation": False}
    identity = digest(body)
    return cast(dict[str, Any], _copy(signed({**body, "strategy_version": identity,
                                             "strategy_id": "factor-" + identity})))


def _check_calendar(definition: dict[str, Any], report: dict[str, Any]) -> None:
    segments = definition["spec"]["segments"]
    calendar = report["documents"]["specification"]["calendar"]
    horizon = definition["evaluation_context"]["label"]["horizon_sessions"]
    _require(all(day in calendar for pair in segments.values() for day in pair),
             "SPLIT_BOUNDARY_NOT_EXCHANGE_SESSION")
    for left, right in (("train", "valid"), ("valid", "test")):
        _require(calendar.index(segments[left][1]) + horizon < calendar.index(segments[right][0]),
                 "LABEL_PURGE_GAP_REQUIRED")
    last = definition["evaluation_context"]["decision_scope"]["last_session"]
    _require(calendar.index(last) + horizon <= calendar.index(segments["test"][1]),
             "DIAGNOSTIC_LABEL_OUTSIDE_TEST")


def freeze_strategy(spec: dict[str, Any], *, factors: FactorRegistry,
                    evaluation: dict[str, Any], source_revision: str) -> dict[str, Any]:
    """Build from a real report; self-contained validation alone is not admission."""
    _disabled()
    spec = _copy(spec)
    _require(type(spec) is dict and set(spec) == _SPEC_FIELDS, "SPEC_FIELDS_INVALID")
    evaluation = validate_report(evaluation)
    _require(spec["evaluation_ref"] == _evaluation_ref(evaluation), "EVALUATION_REFERENCE_MISMATCH")
    factor = factors.get(spec["factor_ref"])
    report_factors = evaluation["documents"]["factor_definitions"]
    _require(any(value == factor for value in report_factors), "EVALUATED_DEFINITION_MISMATCH")
    source = {"revision": source_revision, "files": _sources(),
              "binding": "EXACT_FILE_BYTES_REVISION_LABEL_NOT_GIT_CERTIFICATION"}
    definition = _compose(spec, factor, _context(evaluation, spec["factor_ref"]), source)
    _check_calendar(definition, evaluation)
    return definition


def validate_strategy(value: Any, *, verify_runtime_source: bool = False) -> dict[str, Any]:
    """Validate an archived definition; registration also resolves actual evidence."""
    value = _copy(value)
    _require(type(value) is dict, "DOCUMENT_REQUIRED")
    try:
        expected = _compose(value["spec"], value["factor_definition"],
                            value["evaluation_context"], value["source"])
        _require(canonical(value) == canonical(expected), "DEFINITION_CHANGED")
        if verify_runtime_source:
            _require(value["source"]["files"] == _sources(), "RUNTIME_SOURCE_MISMATCH")
    except (KeyError, TypeError, OverflowError, RecursionError) as error:
        raise ValueError("FACTOR_STRATEGY_DOCUMENT_INVALID") from error
    return cast(dict[str, Any], value)


def verify_bound_strategy(value: Any, *, factors: FactorRegistry,
                          evaluations: FactorEvaluationStore, version_root: Path,
                          data_database: Path) -> dict[str, Any]:
    """Resolve exact versions and actual admitted bytes, never mutable latest heads."""
    value = validate_strategy(value, verify_runtime_source=True)
    spec = value["spec"]
    report = evaluations.get(spec["evaluation_ref"]["evaluation_id"])
    rebuilt = freeze_strategy(spec, factors=factors, evaluation=report,
                              source_revision=value["source"]["revision"])
    _require(value == rebuilt, "LOCAL_EVIDENCE_MISMATCH")
    _require(value["evaluation_context"]["data_binding"] == ready_dataset(
        version_root, data_database), "LOCAL_READY_DATASET_MISMATCH")
    _require(set(value["evaluation_context"]["instruments"])
             <= set(supported_universe(version_root)),
             "LOCAL_UNIVERSE_MISMATCH")
    validate_definition(value["factor_definition"], verify_runtime_source=True)
    return cast(dict[str, Any], value)


def strategy_spec(value: Any, *, now: datetime) -> StrategySpec:
    value = validate_strategy(value)
    _instant(now.isoformat())
    spec, context = value["spec"], value["evaluation_context"]
    _require(now >= _instant(context["computed_at"]), "REGISTRATION_PRECEDES_EVALUATION")
    parent = spec["parent"]
    reference = spec["factor_ref"]
    return StrategySpec(strategy_id=value["strategy_id"], name=spec["name"], family=FAMILY,
        version=value["strategy_version"], parent_strategy_id=None if parent is None else
        parent["strategy_id"], git_commit=value["source"]["revision"],
        data_version=context["data_binding"]["data_version_id"],
        universe=tuple(context["instruments"]),
        factor_set=(reference["factor_id"] + "@" + reference["factor_version"],),
        model="NONE_FIXED_FACTOR_SCORE", portfolio_rule="TopkDropout",
        parameters=(("contract_sha256", value["evidence_sha256"]),),
        split=_segments(spec["segments"], context["data_binding"]), created_at=now, updated_at=now)


def strategy_lineage(value: Any, *, now: datetime) -> StrategyLineage | None:
    value = validate_strategy(value)
    parent = value["spec"]["parent"]
    if parent is None:
        return None
    return StrategyLineage(child_strategy_id=value["strategy_id"],
        parent_strategy_id=parent["strategy_id"], reason=parent["reason"],
        hypothesis=parent["hypothesis"], change_summary=parent["change_summary"],
        experiment_result="PENDING_RESEARCH_NO_EXECUTED_PORTFOLIO_RESULT",
        promotion_result="NOT_REQUESTED", created_at=now)


def register_strategy(registry: StrategyRegistry, definition: Any, *, factors: FactorRegistry,
                      evaluations: FactorEvaluationStore, version_root: Path, data_database: Path,
                      job_id: str, actor: str, now: datetime) -> StrategyRecord:
    """Verify local evidence and atomically associate EXPERIMENT/NONE only."""
    _disabled()
    definition = verify_bound_strategy(definition, factors=factors, evaluations=evaluations,
                                       version_root=version_root, data_database=data_database)
    return registry.register_factor_research_experiment(strategy_spec(definition, now=now),
        contract=definition, job_id=job_id, actor=actor)
