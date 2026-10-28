"""Independent, version-bound daily factor diagnostics; no execution authority.

Reuse audit: ready_dataset/verify_version own admission and byte identity,
FactorRegistry owns reviewed formulas, and metrics owns Pearson/average ranks.
This adapter supplies the missing calendar, dual-clock, null and archive policy.
The vn.py gateway/portfolio layer supplies none of these research contracts.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, date, datetime
from datetime import time as wall_time
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
from itertools import combinations
from pathlib import Path
from statistics import stdev
from typing import Any
from uuid import uuid4

from qtrade.market_data.akshare import SHANGHAI
from qtrade.monitoring.data_freshness import AUTHORITY
from qtrade.research import factors, metrics, signal_baselines
from qtrade.research.data_versions import file_hash
from qtrade.research.factor_registry import FactorRegistry, evaluate_builtin
from qtrade.research.strategy_contract import ready_dataset, supported_universe

SCHEMA = "qtrade.daily-factor-evaluation.v1"
POLICY_VERSION = "qtrade.factor-evaluation-policy.v1"
LIMITATIONS = [
    "CURRENT_LIQUIDITY_UNIVERSE_SURVIVORSHIP_AND_SELECTION_BIAS",
    "POST_HOC_VIEWED_HISTORY_NOT_UNSEEN_FORWARD_EVIDENCE",
    "HISTORICAL_PIT_NOT_CERTIFIED",
    "HISTORICAL_MASTER_DELISTED_STATUS_AND_LIMIT_PRICE_COVERAGE_UNCERTIFIED",
    "VENDOR_ADJUSTMENT_AND_CORPORATE_ACTIONS_NOT_INDEPENDENTLY_CERTIFIED",
    "CLOSE_LABEL_DIAGNOSTIC_NOT_EXECUTED_PORTFOLIO_NET_RETURN",
    "NO_PARAMETER_SEARCH_MULTIPLE_TESTING_OR_ALPHA_SIGNIFICANCE_CLAIM",
]
REQUEST_FIELDS = {
    "ordered_factor_refs", "instruments", "decisions", "label_horizon_sessions",
    "knowledge_mode", "quantiles", "min_pairs", "stability_block_sessions",
    "source_revision", "revalidates_evaluation_id",
}


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError("FACTOR_EVALUATION_" + code)


def _disabled() -> None:
    _require(os.environ.get("LIVE_TRADING_ENABLED", "false").lower() == "false",
             "LIVE_DISABLED_REQUIRED")


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _instant(value: Any) -> datetime:
    _require(isinstance(value, str), "AWARE_CLOCK_REQUIRED")
    result = datetime.fromisoformat(value)
    _require(result.tzinfo is not None and result.utcoffset() is not None,
             "AWARE_CLOCK_REQUIRED")
    return result


def _clock(value: Any) -> datetime | None:
    return None if value is None else _instant(value)


def metric_policy(*, quantiles: int, min_pairs: int,
                  stability_block_sessions: int) -> dict[str, Any]:
    for value, low, high in ((quantiles, 2, 20), (min_pairs, 2, 6000),
                             (stability_block_sessions, 1, 252)):
        _require(type(value) is int and low <= value <= high, "METRIC_POLICY_INVALID")
    _require(min_pairs >= quantiles, "MIN_PAIRS_MUST_COVER_GROUPS")
    return {
        "version": POLICY_VERSION, "quantiles": quantiles, "min_pairs": min_pairs,
        "correlation": "DAILY_PEARSON_AND_SPEARMAN_AVERAGE_TIES",
        "invalid_correlation": "NULL_WITH_REASON_NEVER_ZERO_FILL",
        "aggregation": "EQUAL_WEIGHT_VALID_DAYS_PAIRWISE_COMPLETE",
        "ir_ddof": 1, "ir_annualized": False,
        "groups": "ASCENDING_SCORE_THEN_INSTRUMENT_FLOOR_BOUNDARIES",
        "direction": "HIGH_SCORE_MINUS_LOW_SCORE_NO_SIGN_SELECTION",
        "turnover": "NEW_TOP_IDS_DIVIDED_BY_CURRENT_TOP_COUNT_ADJACENT_REQUESTED_SESSIONS",
        "stability_block_sessions": stability_block_sessions,
        "stability_blocks": "FIXED_CONSECUTIVE_REQUESTED_SESSIONS_FROM_START",
        "redundancy": "DAILY_SAME_INSTRUMENT_FACTOR_PEARSON_AND_SPEARMAN",
    }


def _request(value: dict[str, Any], now: datetime) -> dict[str, Any]:
    value = _copy(value)
    _require(set(value) == REQUEST_FIELDS, "REQUEST_FIELDS_INVALID")
    _require(re.fullmatch("[a-f0-9]{40}", value["source_revision"]) is not None,
             "SOURCE_REVISION_INVALID")
    _require(value["knowledge_mode"] in {"OBSERVED_SYSTEM", "RETROSPECTIVE_RECONSTRUCTION"},
             "KNOWLEDGE_MODE_INVALID")
    instruments = value["instruments"]
    _require(isinstance(instruments, list) and 1 <= len(instruments) <= 6000
             and instruments == sorted(set(instruments))
             and all(re.fullmatch(r"(?:SH|SZ)\d{6}", s) for s in instruments),
             "EXACT_SORTED_INSTRUMENT_SCOPE_REQUIRED")
    refs = value["ordered_factor_refs"]
    _require(isinstance(refs, list) and 1 <= len(refs) <= 4
             and len({_hash(ref) for ref in refs}) == len(refs), "UNIQUE_FACTOR_REFS_REQUIRED")
    _require(type(value["label_horizon_sessions"]) is int
             and 1 <= value["label_horizon_sessions"] <= 20, "LABEL_HORIZON_INVALID")
    decisions = value["decisions"]
    _require(isinstance(decisions, list) and 1 <= len(decisions) <= 2520,
             "DECISION_SCOPE_INVALID")
    sessions = []
    for decision in decisions:
        _require(isinstance(decision, dict) and set(decision) == {"session", "decision_at"},
                 "DECISION_FIELDS_INVALID")
        session = date.fromisoformat(decision["session"]).isoformat()
        _require(session == decision["session"], "SESSION_INVALID")
        instant = _instant(decision["decision_at"])
        local = instant.astimezone(SHANGHAI)
        _require(instant <= now and local.date().isoformat() == session
                 and local.time() >= wall_time(15),
                 "DECISION_CLOCK_INVALID")
        sessions.append(session)
    _require(sessions == sorted(set(sessions)), "SORTED_UNIQUE_DECISIONS_REQUIRED")
    prior = value["revalidates_evaluation_id"]
    _require(prior is None or (isinstance(prior, str)
             and re.fullmatch(r"factor-eval-[a-f0-9]{32}", prior) is not None),
             "REVALIDATION_ID_INVALID")
    metric_policy(quantiles=value["quantiles"], min_pairs=value["min_pairs"],
                  stability_block_sessions=value["stability_block_sessions"])
    return dict(value)


def _read_dataset(root: Path, database: Path, instruments: list[str], *,
                  decisions: list[dict[str, str]], required_closes: int,
                  label_horizon: int) -> dict[str, Any]:
    """Read only normalized files already covered by the exact READY version."""
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    binding = ready_dataset(root, database)
    version = json.loads((root / "version.json").read_bytes())
    _require(set(instruments) <= set(supported_universe(root)), "SCOPE_OUTSIDE_READY_UNIVERSE")
    calendar = [day for day in (root / "provider/calendars/day.txt").read_text().splitlines()
                if day <= binding["actual_end"]]
    _require(len(calendar) <= 4096, "NONPRODUCTION_CALENDAR_ROW_BUDGET_EXCEEDED")
    requested = [item["session"] for item in decisions]
    _require(set(requested) <= set(calendar), "DECISION_OUTSIDE_READY_CALENDAR")
    positions = [calendar.index(day) for day in requested]
    _require(positions == list(range(positions[0], positions[-1] + 1)),
             "DECISIONS_REQUIRE_CONTIGUOUS_COMMON_CALENDAR")
    first = max(0, positions[0] - required_closes + 1)
    last = min(len(calendar) - 1, positions[-1] + label_horizon)
    required_calendar = calendar[first:last + 1]
    intervals: dict[str, list[tuple[str, str]]] = {}
    for line in (root / "provider/instruments/workflow_supported.txt").read_text().splitlines():
        instrument, start, end = line.split("\t")
        intervals.setdefault(instrument, []).append((start, end))
    rows: dict[str, dict[str, dict[str, Any]]] = {}
    input_bytes, uncompressed_bytes, decoded_bytes = 0, 0, 0
    for instrument in instruments:
        name = f"stocks/{instrument[2:]}/daily.parquet"
        _require(name in version["files"], "NORMALIZED_DAILY_FILE_NOT_VERSION_BOUND")
        path = root / name
        input_bytes += path.stat().st_size
        _require(path.stat().st_size <= 32 * 1024**2 and input_bytes <= 256 * 1024**2,
                 "NONPRODUCTION_PARQUET_BYTE_BUDGET_EXCEEDED")
        # The second check binds the exact bytes consumed after full verification.
        _require(file_hash(path) == version["files"][name], "INPUT_CHANGED_BEFORE_READ")
        parquet = pq.ParquetFile(path)
        _require(parquet.metadata.num_rows <= len(calendar)
                 and {"date", "close"} <= set(parquet.schema_arrow.names),
                 "PARQUET_ROW_BUDGET_OR_REQUIRED_COLUMNS_INVALID")
        columns = [name for name in ("date", "close", "available_at", "observed_at")
                   if name in parquet.schema_arrow.names]
        physical = sum(parquet.metadata.row_group(group).column(column).total_uncompressed_size
            for group in range(parquet.metadata.num_row_groups)
            for column in range(parquet.metadata.row_group(group).num_columns)
            if parquet.metadata.row_group(group).column(column).path_in_schema in columns)
        uncompressed_bytes += physical
        _require(physical <= 64 * 1024**2 and uncompressed_bytes <= 256 * 1024**2,
                 "NONPRODUCTION_UNCOMPRESSED_COLUMN_BUDGET_EXCEEDED")
        date_type = parquet.schema_arrow.field("date").type
        lower, upper = required_calendar[0], required_calendar[-1]
        lower_bound: str | date = (date.fromisoformat(lower)
                                  if pa.types.is_date(date_type) else lower)
        upper_bound: str | date = (date.fromisoformat(upper)
                                  if pa.types.is_date(date_type) else upper)
        table = pq.read_table(path, columns=columns,
                              filters=[("date", ">=", lower_bound), ("date", "<=", upper_bound)])
        _require(table.num_rows <= len(required_calendar), "DECODED_WINDOW_ROW_BUDGET_EXCEEDED")
        decoded_bytes += table.nbytes
        _require(table.nbytes <= 32 * 1024**2 and decoded_bytes <= 128 * 1024**2,
                 "NONPRODUCTION_DECODED_BYTE_BUDGET_EXCEEDED")
        source = table.to_pylist()
        _require(file_hash(path) == version["files"][name], "INPUT_CHANGED_DURING_READ")
        lookup: dict[str, dict[str, Any]] = {}
        for row in source:
            day = str(row["date"])
            _require(date.fromisoformat(day).isoformat() == day and day in required_calendar
                     and day not in lookup, "NORMALIZED_ROW_GRID_INVALID")
            # No default collection/publication timestamp: unknown remains unknown.
            lookup[day] = {"close": row.get("close"),
                           "available_at": row.get("available_at"),
                           "observed_at": row.get("observed_at")}
            for key in ("available_at", "observed_at"):
                if isinstance(lookup[day][key], datetime):
                    lookup[day][key] = lookup[day][key].isoformat()
                _clock(lookup[day][key])
        rows[instrument] = lookup
    _require(ready_dataset(root, database) == binding, "READY_BINDING_CHANGED_DURING_READ")
    return {"data_binding": binding, "version_manifest": version, "calendar": calendar,
            "intervals": intervals, "rows": rows}


def _price(row: dict[str, Any] | None) -> Decimal | None:
    if row is None:
        return None
    try:
        value = Decimal(str(row.get("close")))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value.is_finite() and value > 0 else None


def _known(rows: list[dict[str, Any]], barrier: datetime) -> tuple[bool, str | None]:
    for row in rows:
        clocks = [_clock(row.get(key)) for key in ("available_at", "observed_at")]
        if any(clock is None for clock in clocks):
            return False, "DEPENDENCY_AVAILABILITY_UNKNOWN"
        if any(clock is not None and clock > barrier for clock in clocks):
            return False, "DEPENDENCY_NOT_KNOWN_AT_DECISION"
    return True, None


def _correlation(left: list[Decimal], right: list[Decimal], minimum: int) -> dict[str, Any]:
    reason = ("INSUFFICIENT_PAIRS" if len(left) < minimum else
              "CONSTANT_LEFT" if len(set(left)) < 2 else
              "CONSTANT_RIGHT" if len(set(right)) < 2 else None)
    if reason:
        return {"pairs": len(left), "pearson": None, "spearman": None, "reason": reason}
    return {"pairs": len(left), "pearson": str(metrics._pearson(left, right)),
            "spearman": str(metrics._pearson(metrics._ranks(left), metrics._ranks(right))),
            "reason": None}


def _average(values: list[Decimal]) -> str | None:
    return str(sum(values, Decimal(0)) / len(values)) if values else None


def _series(values: list[str | None]) -> dict[str, Any]:
    valid = [Decimal(item) for item in values if item is not None]
    average = _average(valid)
    reason = "INSUFFICIENT_DAYS" if len(valid) < 2 else None
    deviation = Decimal(str(stdev(float(item) for item in valid))) if len(valid) >= 2 else None
    if deviation == 0:
        reason = "ZERO_SAMPLE_STANDARD_DEVIATION"
    return {"valid_days": len(valid), "mean": average,
            "sample_std_ddof1": str(deviation) if deviation is not None else None,
            "ir": str(Decimal(average) / deviation)
            if reason is None and deviation and average is not None else None,
            "ir_reason": reason,
            "positive_fraction": str(Decimal(sum(item > 0 for item in valid)) / len(valid))
            if valid else None}


def _summarize(days: list[dict[str, Any]]) -> dict[str, Any]:
    spreads = [Decimal(day["long_short_spread"]) for day in days
               if day["long_short_spread"] is not None]
    turns = [Decimal(day["top_group_turnover"]) for day in days
             if day["top_group_turnover"] is not None]
    return {"requested_days": len(days),
            "ic": _series([day["ic"] for day in days]),
            "rank_ic": _series([day["rank_ic"] for day in days]),
            "mean_group_spread": _average(spreads), "mean_top_group_turnover": _average(turns),
            "valid_pairs": sum(day["pairs"] for day in days),
            "candidate_pairs": sum(day["candidates"] for day in days)}


def _daily_metrics(values: dict[str, Decimal], labels: dict[str, Decimal], *,
                   minimum: int, quantiles: int, previous_top: set[str] | None) -> dict[str, Any]:
    instruments = sorted(set(values) & set(labels))
    correlation = _correlation([values[s] for s in instruments],
                               [labels[s] for s in instruments], minimum)
    ordered = sorted(instruments, key=lambda s: (values[s], s))
    groups: list[dict[str, Any]] = []
    top: set[str] | None = None
    if len(instruments) >= minimum:
        for group in range(quantiles):
            members = ordered[group * len(ordered) // quantiles:
                              (group + 1) * len(ordered) // quantiles]
            groups.append({"group": group + 1, "count": len(members), "instruments": members,
                           "mean_label": _average([labels[s] for s in members])})
        top = set(groups[-1]["instruments"])
    return {"pairs": correlation["pairs"], "ic": correlation["pearson"],
            "rank_ic": correlation["spearman"], "correlation_reason": correlation["reason"],
            "groups": groups, "groups_reason": None if groups else "INSUFFICIENT_PAIRS",
            "long_short_spread": str(Decimal(groups[-1]["mean_label"])
                                     - Decimal(groups[0]["mean_label"])) if groups else None,
            "top_group_turnover": str(Decimal(len(top - previous_top)) / len(top))
            if top is not None and previous_top is not None else None,
            "turnover_reason": None if top is not None and previous_top is not None
            else "NO_ADJACENT_VALID_TOP_GROUP",
            "top_instruments": sorted(top) if top is not None else None}


def _source_snapshot() -> dict[str, Any]:
    from qtrade.monitoring import data_freshness
    from qtrade.research import (
        data_versions,
        factor_registry,
        model_registry,
        strategy_contract,
        workflow,
    )

    modules = [Path(__file__), Path(factors.__file__), Path(metrics.__file__),
               Path(signal_baselines.__file__), Path(factor_registry.__file__),
               Path(strategy_contract.__file__), Path(data_versions.__file__),
               Path(model_registry.__file__), Path(data_freshness.__file__),
               Path(workflow.__file__)]
    base = Path(__file__).parents[1]
    return {path.relative_to(base).as_posix(): {
                "sha256": file_hash(path), "source": path.read_bytes().decode("utf-8")}
            for path in modules}


def _label_policy(horizon: int, barrier: str) -> dict[str, Any]:
    return {"kind": "RAW_CLOSE_TO_CLOSE", "formula": "close[t+h]/close[t]-1",
        "horizon_sessions": horizon, "entry_offset": 0, "exit_offset": horizon,
        "frequency": "day", "price_basis": "QLIB_NORMALIZED_ADJUSTMENT_UNCERTIFIED",
        "maturity_barrier": barrier,
        "session_maturity": "SHANGHAI_SESSION_15_00_SCHEDULE_NOT_SOURCE_CLOCK",
        "unknown_exact_availability": "RETROSPECTIVE_SESSION_SCHEDULE_MATURITY_ONLY"}


def _cost_policy() -> dict[str, Any]:
    return {"kind": "GROSS_LABEL_DIAGNOSTIC", "fees_applied": False,
            "slippage_applied": False, "executable_return_claim": False}


def _split_policy() -> dict[str, Any]:
    return {"kind": "ONE_PREDECLARED_POSTHOC_DIAGNOSTIC_WINDOW", "fitting": False,
            "parameter_selection": False, "unseen_test_claim": False,
            "purge": "NOT_APPLICABLE_NO_FIT_OR_SPLITS"}


def _numeric_policy() -> dict[str, Any]:
    return {"decimal_precision": 28, "decimal_rounding": "ROUND_HALF_EVEN",
        "Emin": -999999, "Emax": 999999, "capitals": 1, "clamp": 0,
        "traps": ["DivisionByZero", "InvalidOperation", "Overflow"],
        "pearson_denominator_and_sample_std": "PYTHON_FLOAT64_SQRT_AND_STATISTICS_STDEV"}


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "NO_INSTALLED_DISTRIBUTION_SOURCE_IMPORT_ONLY"


def evaluate_daily(*, version_root: Path, data_database: Path, registry: FactorRegistry,
                   request: dict[str, Any], now: datetime,
                   evaluation_id: str | None = None) -> dict[str, Any]:
    """Run all factor, label and metric arithmetic under one frozen Decimal policy."""
    context = Context(prec=28, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999,
                      capitals=1, clamp=0, flags=[],
                      traps=[DivisionByZero, InvalidOperation, Overflow])
    with localcontext(context):
        return _evaluate_daily(version_root=version_root, data_database=data_database,
            registry=registry, request=request, now=now, evaluation_id=evaluation_id)


def _evaluate_daily(*, version_root: Path, data_database: Path, registry: FactorRegistry,
                    request: dict[str, Any], now: datetime,
                    evaluation_id: str | None = None) -> dict[str, Any]:
    """Compute diagnostics against one verified archive; never follow current heads.

    OBSERVED_SYSTEM checks each required feature dependency at its exact barrier.
    It records hypothetical dependency knowability, never historical computation.
    Labels use a separate run-time maturity barrier and never enter feature input.
    """
    _disabled()
    _require(now.tzinfo is not None and now.utcoffset() is not None, "AWARE_NOW_REQUIRED")
    _require(now <= datetime.now(UTC), "FUTURE_EVALUATION_BARRIER_FORBIDDEN")
    request = _request(request, now)
    identity = evaluation_id or "factor-eval-" + uuid4().hex
    _require(re.fullmatch(r"factor-eval-[a-f0-9]{32}", identity) is not None,
             "EVALUATION_ID_INVALID")
    _require(identity != request["revalidates_evaluation_id"], "SELF_REVALIDATION_FORBIDDEN")
    definitions = [registry.get(ref) for ref in request["ordered_factor_refs"]]
    _require(all(item["price_basis"] == "QLIB_NORMALIZED_ADJUSTMENT_UNCERTIFIED"
                 and item["frequency"] == "day" for item in definitions),
             "FACTOR_INPUT_BASIS_MISMATCH")
    _require(len(request["instruments"]) * len(request["decisions"])
             * sum(item["required_closes"] for item in definitions) <= 1_000_000,
             "NONPRODUCTION_DEPENDENCY_CELL_BUDGET_EXCEEDED")
    dataset = _read_dataset(version_root, data_database, request["instruments"],
        decisions=request["decisions"],
        required_closes=max(d["required_closes"] for d in definitions),
        label_horizon=request["label_horizon_sessions"])
    calendar, rows = dataset["calendar"], dataset["rows"]
    requested = [item["session"] for item in request["decisions"]]
    _require(set(requested) <= set(calendar), "DECISION_OUTSIDE_READY_CALENDAR")
    positions = [calendar.index(day) for day in requested]
    _require(positions == list(range(positions[0], positions[-1] + 1)),
             "DECISIONS_REQUIRE_CONTIGUOUS_COMMON_CALENDAR")
    policy = metric_policy(quantiles=request["quantiles"], min_pairs=request["min_pairs"],
                           stability_block_sessions=request["stability_block_sessions"])
    sources = _source_snapshot()
    specification = {
        **AUTHORITY, "schema_version": SCHEMA, "request": request,
        "data_binding": dataset["data_binding"], "calendar": calendar,
        "calendar_sha256": _hash(calendar), "metric_policy": policy,
        "universe": {"instruments": request["instruments"],
                     "selection": "EXPLICIT_SUBSET_OF_CURRENT_READY_SUPPORTED_UNIVERSE"},
        "label": _label_policy(request["label_horizon_sessions"], now.isoformat()),
        "decision_policy": "SAME_SHANGHAI_SESSION_AT_OR_AFTER_15_00_WITH_EXACT_DUAL_CLOCKS",
        "cost_policy": _cost_policy(), "split_policy": _split_policy(),
        "runtime_source_sha256": {name: item["sha256"] for name, item in sources.items()},
        "package_versions": {name: _package_version(name)
                             for name in ("pyarrow", "q-quant-platform")},
        "python_version": sys.version,
        "numeric_policy": _numeric_policy(),
    }
    daily: dict[str, list[dict[str, Any]]] = {str(i): [] for i in range(len(definitions))}
    ledger, samples, redundancy = [], [], []
    previous_tops: dict[str, set[str] | None] = dict.fromkeys(daily)
    horizon = request["label_horizon_sessions"]
    for decision, position in zip(request["decisions"], positions, strict=True):
        day, barrier = decision["session"], _instant(decision["decision_at"])
        labels: dict[str, Decimal] = {}
        day_values: dict[str, dict[str, Decimal]] = {}
        for factor_index, definition in enumerate(definitions):
            key = str(factor_index)
            histories: dict[str, tuple[Decimal, ...]] = {}
            required = definition["required_closes"]
            session_grid = calendar[max(0, position - required + 1):position + 1]
            facts: dict[str, dict[str, Any]] = {}
            for instrument in request["instruments"]:
                selected = [rows[instrument].get(session) for session in session_grid]
                reason = None
                if len(session_grid) != required:
                    reason = "INSUFFICIENT_COMMON_CALENDAR_WARMUP"
                elif not all(any(start <= session <= end
                                 for start, end in dataset["intervals"][instrument])
                             for session in session_grid):
                    reason = "OUTSIDE_FROZEN_ELIGIBILITY_INTERVAL"
                elif any(row is None for row in selected):
                    reason = "MISSING_COMMON_SESSION_CLOSE"
                elif any(_price(row) is None for row in selected):
                    reason = "INVALID_CLOSE"
                dependencies = [row for row in selected if row is not None]
                known, clock_reason = _known(dependencies, barrier) if not reason else (False, None)
                if reason is None and request["knowledge_mode"] == "OBSERVED_SYSTEM" and not known:
                    reason = clock_reason
                label_reason = None
                endpoint = position + horizon
                target = calendar[endpoint] if endpoint < len(calendar) else None
                entry_row = rows[instrument].get(day)
                exit_row = rows[instrument].get(target) if target is not None else None
                entry, exit_price = _price(entry_row), _price(exit_row)
                if target is None or datetime.combine(date.fromisoformat(target),
                        wall_time(15), SHANGHAI) > now:
                    label_reason = "LABEL_NOT_MATURE_IN_FROZEN_VERSION"
                elif not any(start <= target <= end
                             for start, end in dataset["intervals"][instrument]):
                    label_reason = "LABEL_OUTSIDE_FROZEN_ELIGIBILITY_INTERVAL"
                elif entry is None or exit_price is None:
                    label_reason = "LABEL_CLOSE_MISSING_OR_INVALID"
                elif any(clock is not None and clock > now for row in (entry_row, exit_row)
                         if row is not None for clock in (_clock(row.get("available_at")),
                                                         _clock(row.get("observed_at")))):
                    label_reason = "LABEL_NOT_AVAILABLE_AT_EVALUATION"
                elif request["knowledge_mode"] == "OBSERVED_SYSTEM":
                    label_known, _ = _known([row for row in (entry_row, exit_row)
                                            if row is not None], now)
                    if not label_known:
                        label_reason = "LABEL_AVAILABILITY_UNKNOWN"
                if label_reason is None and entry is not None and exit_price is not None:
                    labels[instrument] = exit_price / entry - 1
                if reason is None:
                    histories[instrument] = tuple(Decimal(str(row["close"]))
                                                  for row in dependencies)
                facts[instrument] = {"session": day, "instrument": instrument,
                    "factor_index": factor_index, "feature_reason": reason,
                    "label_reason": label_reason, "label_exit_session": target,
                    "dependencies_known_at_barrier": known, "clock_reason": clock_reason,
                    "history_sessions": session_grid,
                    "dependency_clocks": [{"session": session,
                        "available_at": row.get("available_at") if row else None,
                        "observed_at": row.get("observed_at") if row else None}
                        for session, row in zip(session_grid, selected, strict=True)]}
            values = evaluate_builtin(definition, histories) if histories else {}
            day_values[key] = values
            for instrument, fact in facts.items():
                ledger.append(fact)
                samples.append({"session": day, "instrument": instrument,
                    "factor_index": factor_index,
                    "factor_value": str(values[instrument]) if instrument in values else None,
                    "raw_label": str(labels[instrument]) if instrument in labels else None})
            result = _daily_metrics(values, labels, minimum=request["min_pairs"],
                                    quantiles=request["quantiles"], previous_top=previous_tops[key])
            result.update(session=day, candidates=len(request["instruments"]),
                          feature_valid=len(values), label_valid=len(labels))
            daily[key].append(result)
            previous_tops[key] = (set(result["top_instruments"])
                                  if result["top_instruments"] is not None else None)
        for left, right in combinations(daily, 2):
            common = sorted(set(day_values[left]) & set(day_values[right]))
            redundancy.append({"session": day, "left_factor_index": int(left),
                "right_factor_index": int(right), "instruments": common,
                **_correlation([day_values[left][s] for s in common],
                               [day_values[right][s] for s in common], request["min_pairs"])})
    summaries, stability = {}, {}
    size = request["stability_block_sessions"]
    for key, days in daily.items():
        summaries[key] = _summarize(days)
        stability[key] = [{"start": days[index]["session"],
            "end": days[min(index + size, len(days)) - 1]["session"],
            **_summarize(days[index:index + size])} for index in range(0, len(days), size)]
    redundant_summary = []
    for left, right in combinations(daily, 2):
        pair = [row for row in redundancy if row["left_factor_index"] == int(left)
                and row["right_factor_index"] == int(right)]
        redundant_summary.append({"left_factor_index": int(left), "right_factor_index": int(right),
            "pearson": _series([row["pearson"] for row in pair]),
            "spearman": _series([row["spearman"] for row in pair])})
    computed_at = datetime.now(UTC).isoformat()
    _require({name: file_hash(Path(__file__).parents[1] / name) for name in sources}
             == specification["runtime_source_sha256"], "RUNTIME_SOURCE_CHANGED_DURING_RUN")
    documents = {"specification": specification, "factor_definitions": definitions,
        "version_manifest": dataset["version_manifest"], "source_snapshot": sources,
        "eligibility_ledger": ledger, "samples": samples, "daily_metrics": daily,
        "stability": stability, "redundancy_daily": redundancy,
        "summary": {"factors": summaries, "redundancy": redundant_summary,
                    "limitations": LIMITATIONS,
                    "historical_computation_claim": False,
                    "actual_computed_at": computed_at}}
    manifest = {name: _hash(document) for name, document in documents.items()}
    return {**AUTHORITY, "schema_version": SCHEMA, "evaluation_id": identity,
        "state": "DIAGNOSTICS_COMPUTED" if any(
            summary["ic"]["valid_days"] for summary in summaries.values())
        else "INSUFFICIENT_VALID_SAMPLE", "computed_at": computed_at,
        "specification_sha256": manifest["specification"], "report_manifest": manifest,
        "report_manifest_sha256": _hash(manifest), "documents": documents}


def validate_report(report: dict[str, Any]) -> dict[str, Any]:
    report = _copy(report)
    _require(all(type(report.get(k)) is type(v) and report.get(k) == v
                 for k, v in AUTHORITY.items()), "REPORT_AUTHORITY_INVALID")
    _require(report.get("schema_version") == SCHEMA
             and re.fullmatch(r"factor-eval-[a-f0-9]{32}", report["evaluation_id"]) is not None,
             "REPORT_ID_INVALID")
    _require(set(report) == {*AUTHORITY, "schema_version", "evaluation_id", "state",
             "computed_at", "specification_sha256", "report_manifest",
             "report_manifest_sha256", "documents"}, "REPORT_FIELDS_INVALID")
    _require(set(report["documents"]) == {"specification", "factor_definitions",
        "version_manifest", "source_snapshot", "eligibility_ledger", "samples",
        "daily_metrics", "stability", "redundancy_daily", "summary"},
        "REPORT_DOCUMENTS_INVALID")
    manifest = {name: _hash(document) for name, document in report["documents"].items()}
    _require(report["report_manifest"] == manifest
             and report["report_manifest_sha256"] == _hash(manifest)
             and report["specification_sha256"] == manifest["specification"],
             "REPORT_MANIFEST_MISMATCH")
    specification, summary = report["documents"]["specification"], report["documents"]["summary"]
    _require(report["computed_at"] == summary["actual_computed_at"]
             and _instant(report["computed_at"])
             >= _instant(specification["label"]["maturity_barrier"]), "REPORT_CLOCK_MISMATCH")
    expected_state = "DIAGNOSTICS_COMPUTED" if any(
        item["ic"]["valid_days"] for item in summary["factors"].values()) \
        else "INSUFFICIENT_VALID_SAMPLE"
    _require(report["state"] == expected_state, "REPORT_STATE_MISMATCH")
    context = Context(prec=28, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999,
                      capitals=1, clamp=0, flags=[],
                      traps=[DivisionByZero, InvalidOperation, Overflow])
    with localcontext(context):
        _verify_saved_values(report["documents"])
    return dict(report)


def _verify_saved_temporal_contract(spec: dict[str, Any], definitions: list[dict[str, Any]],
                                    ledger: dict[tuple[str, int, str], Any]) -> None:
    """Replay the clocks/grid present in v1 archives, without inventing raw facts.

    The v1 bundle does not contain label-exit clocks or eligibility interval file
    bytes. Their source truth still requires the original verified data version.
    """
    request, calendar = spec["request"], spec["calendar"]
    _require(isinstance(calendar, list) and 1 <= len(calendar) <= 4096
             and all(isinstance(day, str) and date.fromisoformat(day).isoformat() == day
                     for day in calendar)
             and calendar == sorted(set(calendar)) and spec["calendar_sha256"] == _hash(calendar)
             and calendar[0] == spec["data_binding"]["start"]
             and calendar[-1] == spec["data_binding"]["actual_end"],
             "ARCHIVED_CALENDAR_GRID_MISMATCH")
    sessions = [item["session"] for item in request["decisions"]]
    _require(set(sessions) <= set(calendar), "ARCHIVED_DECISION_GRID_MISMATCH")
    positions = [calendar.index(day) for day in sessions]
    _require(positions == list(range(positions[0], positions[-1] + 1)),
             "ARCHIVED_DECISION_GRID_MISMATCH")
    barrier = _instant(spec["label"]["maturity_barrier"])
    pre_clock_reasons = {"INSUFFICIENT_COMMON_CALENDAR_WARMUP",
        "OUTSIDE_FROZEN_ELIGIBILITY_INTERVAL", "MISSING_COMMON_SESSION_CLOSE", "INVALID_CLOSE"}
    label_reasons = {None, "LABEL_NOT_MATURE_IN_FROZEN_VERSION",
        "LABEL_OUTSIDE_FROZEN_ELIGIBILITY_INTERVAL", "LABEL_CLOSE_MISSING_OR_INVALID",
        "LABEL_NOT_AVAILABLE_AT_EVALUATION", "LABEL_AVAILABILITY_UNKNOWN"}
    for decision, position in zip(request["decisions"], positions, strict=True):
        feature_barrier = _instant(decision["decision_at"])
        endpoint = position + request["label_horizon_sessions"]
        target = calendar[endpoint] if endpoint < len(calendar) else None
        immature = target is None or datetime.combine(date.fromisoformat(target),
            wall_time(15), SHANGHAI) > barrier
        for index, definition in enumerate(definitions):
            required = definition["required_closes"]
            grid = calendar[max(0, position - required + 1):position + 1]
            for instrument in request["instruments"]:
                fact = ledger[(decision["session"], index, instrument)]
                clocks = fact["dependency_clocks"]
                _require(fact["history_sessions"] == grid and isinstance(clocks, list)
                    and len(clocks) == len(grid)
                    and all(isinstance(clock, dict)
                            and set(clock) == {"session", "available_at", "observed_at"}
                            and clock["session"] == session
                            for session, clock in zip(grid, clocks, strict=True)),
                    "ARCHIVED_DEPENDENCY_GRID_MISMATCH")
                # Parse even excluded inputs so malformed clocks cannot hide in a rejection.
                for clock in clocks:
                    _clock(clock["available_at"])
                    _clock(clock["observed_at"])
                reason = fact["feature_reason"]
                _require(reason in pre_clock_reasons | {None,
                    "DEPENDENCY_AVAILABILITY_UNKNOWN", "DEPENDENCY_NOT_KNOWN_AT_DECISION"}
                    and (reason == "INSUFFICIENT_COMMON_CALENDAR_WARMUP")
                    == (len(grid) < required), "ARCHIVED_FEATURE_GRID_REASON_MISMATCH")
                if reason in pre_clock_reasons:
                    known, clock_reason = False, None
                else:
                    known, clock_reason = _known(clocks, feature_barrier)
                    _require(reason == (clock_reason if request["knowledge_mode"]
                             == "OBSERVED_SYSTEM" and not known else None),
                             "ARCHIVED_FEATURE_CLOCK_REASON_MISMATCH")
                _require(type(fact["dependencies_known_at_barrier"]) is bool
                         and fact["dependencies_known_at_barrier"] == known
                         and fact["clock_reason"] == clock_reason,
                         "ARCHIVED_DEPENDENCY_CLOCK_MISMATCH")
                _require(fact["label_exit_session"] == target
                         and fact["label_reason"] in label_reasons
                         and (fact["label_reason"] == "LABEL_NOT_MATURE_IN_FROZEN_VERSION")
                         == immature, "ARCHIVED_LABEL_MATURITY_MISMATCH")


def _verify_saved_values(documents: dict[str, Any]) -> None:
    """Replay archived values/policy, independently of cached metrics and summary.

    This checks internal numerical consistency, not raw-price truth or signatures.
    Raw-price recomputation requires the original independently verified version.
    """
    from qtrade.research.factor_registry import factor_ref, validate_definition
    from qtrade.research.strategy_contract import validate_data_binding

    spec = documents["specification"]
    request = _request(spec["request"], _instant(spec["label"]["maturity_barrier"]))
    _require(all(type(spec.get(k)) is type(v) and spec.get(k) == v for k, v in AUTHORITY.items())
             and spec["label"] == _label_policy(request["label_horizon_sessions"],
                                                spec["label"]["maturity_barrier"])
             and spec["cost_policy"] == _cost_policy() and spec["split_policy"] == _split_policy()
             and spec["numeric_policy"] == _numeric_policy()
             and spec["decision_policy"]
             == "SAME_SHANGHAI_SESSION_AT_OR_AFTER_15_00_WITH_EXACT_DUAL_CLOCKS"
             and documents["summary"]["limitations"] == LIMITATIONS
             and documents["summary"]["historical_computation_claim"] is False,
             "ARCHIVED_FIXED_RESEARCH_CLAIMS_MISMATCH")
    definitions = [validate_definition(value, verify_runtime_source=False)
                   for value in documents["factor_definitions"]]
    _require([factor_ref(value) for value in definitions] == request["ordered_factor_refs"],
             "ARCHIVED_FACTOR_BINDING_MISMATCH")
    binding = validate_data_binding(spec["data_binding"])
    _require(binding["data_version_id"] == documents["version_manifest"]["version_id"]
             and binding["manifest_sha256"]
             == documents["version_manifest"]["evidence_sha256"], "ARCHIVED_DATA_BINDING_MISMATCH")
    _require(spec["metric_policy"] == metric_policy(quantiles=request["quantiles"],
        min_pairs=request["min_pairs"],
        stability_block_sessions=request["stability_block_sessions"]),
        "ARCHIVED_METRIC_POLICY_MISMATCH")
    _require({name: sha256(item["source"].encode("utf-8")).hexdigest()
              for name, item in documents["source_snapshot"].items()}
             == spec["runtime_source_sha256"]
             == {name: item["sha256"] for name, item in documents["source_snapshot"].items()},
             "ARCHIVED_SOURCE_BINDING_MISMATCH")
    _require(set(spec["runtime_source_sha256"]) == {
        "research/factor_evaluation.py", "research/factors.py", "research/metrics.py",
        "research/signal_baselines.py", "research/factor_registry.py",
        "research/strategy_contract.py", "research/data_versions.py",
        "research/model_registry.py", "monitoring/data_freshness.py", "research/workflow.py"},
        "ARCHIVED_SOURCE_INVENTORY_MISMATCH")
    _require(spec["schema_version"] == SCHEMA
             and spec["universe"] == {"instruments": request["instruments"],
                 "selection": "EXPLICIT_SUBSET_OF_CURRENT_READY_SUPPORTED_UNIVERSE"}
             and all(definition["frequency"] == "day"
                 and definition["price_basis"] == "QLIB_NORMALIZED_ADJUSTMENT_UNCERTIFIED"
                 and all(name.startswith("src/qtrade/")
                         and spec["runtime_source_sha256"].get(name.removeprefix("src/qtrade/"))
                         == digest for name, digest in definition["source"]["files"].items())
                 for definition in definitions), "ARCHIVED_FACTOR_SOURCE_OR_SCOPE_MISMATCH")
    _require(len(request["instruments"]) * len(request["decisions"])
             * sum(item["required_closes"] for item in definitions) <= 1_000_000,
             "ARCHIVED_NONPRODUCTION_DEPENDENCY_CELL_BUDGET_EXCEEDED")
    keys = {(decision["session"], index, instrument)
            for decision in request["decisions"] for index in range(len(definitions))
            for instrument in request["instruments"]}
    sample_map = {(item["session"], item["factor_index"], item["instrument"]): item
                  for item in documents["samples"]}
    ledger_map = {(item["session"], item["factor_index"], item["instrument"]): item
                  for item in documents["eligibility_ledger"]}
    _require(set(sample_map) == set(ledger_map) == keys
             and len(documents["samples"]) == len(documents["eligibility_ledger"]) == len(keys),
             "ARCHIVED_EXACT_SAMPLE_SCOPE_MISMATCH")
    _verify_saved_temporal_contract(spec, definitions, ledger_map)
    daily: dict[str, list[dict[str, Any]]] = {str(index): [] for index in range(len(definitions))}
    previous: dict[str, set[str] | None] = dict.fromkeys(daily)
    redundancy = []
    for decision in request["decisions"]:
        day = decision["session"]
        scores = {}
        for key in daily:
            values: dict[str, Decimal] = {}
            labels: dict[str, Decimal] = {}
            for instrument in request["instruments"]:
                sample, ledger = (sample_map[(day, int(key), instrument)],
                                  ledger_map[(day, int(key), instrument)])
                _require((sample["factor_value"] is None) == (ledger["feature_reason"] is not None)
                         and (sample["raw_label"] is None) == (ledger["label_reason"] is not None),
                         "ARCHIVED_SAMPLE_ELIGIBILITY_MISMATCH")
                for name, target in (("factor_value", values), ("raw_label", labels)):
                    if sample[name] is not None:
                        number = Decimal(sample[name])
                        _require(number.is_finite(), "ARCHIVED_NONFINITE_SAMPLE")
                        target[instrument] = number
            scores[key] = values
            result = _daily_metrics(values, labels, minimum=request["min_pairs"],
                quantiles=request["quantiles"], previous_top=previous[key])
            result.update(session=day, candidates=len(request["instruments"]),
                          feature_valid=len(values), label_valid=len(labels))
            daily[key].append(result)
            previous[key] = set(result["top_instruments"]) if result["top_instruments"] else None
        for left, right in combinations(daily, 2):
            common = sorted(set(scores[left]) & set(scores[right]))
            redundancy.append({"session": day, "left_factor_index": int(left),
                "right_factor_index": int(right), "instruments": common,
                **_correlation([scores[left][s] for s in common],
                               [scores[right][s] for s in common], request["min_pairs"])})
    _require(daily == documents["daily_metrics"] and redundancy == documents["redundancy_daily"],
             "ARCHIVED_DAILY_NUMERICAL_REPLAY_MISMATCH")
    size = request["stability_block_sessions"]
    stability = {key: [{"start": days[index]["session"],
        "end": days[min(index + size, len(days)) - 1]["session"],
        **_summarize(days[index:index + size])} for index in range(0, len(days), size)]
        for key, days in daily.items()}
    redundant_summary = []
    for left, right in combinations(daily, 2):
        pair = [row for row in redundancy if row["left_factor_index"] == int(left)
                and row["right_factor_index"] == int(right)]
        redundant_summary.append({"left_factor_index": int(left), "right_factor_index": int(right),
            "pearson": _series([row["pearson"] for row in pair]),
            "spearman": _series([row["spearman"] for row in pair])})
    _require(stability == documents["stability"]
             and {key: _summarize(days) for key, days in daily.items()}
             == documents["summary"]["factors"]
             and redundant_summary == documents["summary"]["redundancy"],
             "ARCHIVED_SUMMARY_NUMERICAL_REPLAY_MISMATCH")


class FactorEvaluationStore:
    """Append-only transactional document bundles, matching local registry storage.

    No latest pointer, update, lifecycle event, model or execution registration.
    SQLite FULL synchronous commit makes a whole bundle visible or none of it.
    """

    def __init__(self, database: Path):
        _disabled()
        self.database = database.absolute()
        self._guard()
        self.database = database.resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database, timeout=10)) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS factor_evaluations "
                               "(evaluation_id TEXT PRIMARY KEY, payload TEXT NOT NULL, "
                               "payload_sha256 TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS factor_evaluation_attempts "
                "(evaluation_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS factor_evaluation_failures "
                "(evaluation_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            connection.commit()

    def _guard(self) -> None:
        database = self.database
        _require(not any(parent.is_symlink() for parent in (database, *database.parents)),
                 "STORE_SYMLINK_FORBIDDEN")
        for suffix in ("", "-wal", "-shm", "-journal"):
            candidate = Path(str(database) + suffix)
            _require(not candidate.is_symlink(), "STORE_SYMLINK_FORBIDDEN")
            _require(not candidate.exists() or candidate.stat().st_nlink == 1,
                     "STORE_HARDLINK_FORBIDDEN")

    def start(self, request: dict[str, Any], *, now: datetime) -> str:
        _disabled()
        self._guard()
        request = _request(request, now)
        identity = "factor-eval-" + uuid4().hex
        attempt = {**AUTHORITY, "evaluation_id": identity, "started_at": now.isoformat(),
                   "request": request, "request_sha256": _hash(request)}
        with closing(sqlite3.connect(self.database, timeout=10)) as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("INSERT INTO factor_evaluation_attempts VALUES(?,?)",
                               (identity, json.dumps(attempt, sort_keys=True)))
            connection.commit()
        return identity

    def fail(self, evaluation_id: str, *, error_type: str, now: datetime) -> None:
        _disabled()
        self._guard()
        # Only exception class names are retained, never arbitrary exception values/paths.
        _require(re.fullmatch("[A-Za-z][A-Za-z0-9_]{0,80}", error_type) is not None,
                 "FAILURE_TYPE_INVALID")
        with closing(sqlite3.connect(self.database, timeout=10)) as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            _require(connection.execute("SELECT 1 FROM factor_evaluation_attempts "
                "WHERE evaluation_id=?", (evaluation_id,)).fetchone() is not None,
                "UNKNOWN_ATTEMPT")
            _require(connection.execute("SELECT 1 FROM factor_evaluations WHERE evaluation_id=?",
                (evaluation_id,)).fetchone() is None, "COMPLETED_ATTEMPT_CANNOT_FAIL")
            connection.execute("INSERT INTO factor_evaluation_failures VALUES(?,?)", (
                evaluation_id, json.dumps({**AUTHORITY, "evaluation_id": evaluation_id,
                "failed_at": now.isoformat(), "error_type": error_type}, sort_keys=True)))
            connection.commit()

    def save(self, report: dict[str, Any]) -> dict[str, str]:
        _disabled()
        self._guard()
        report = validate_report(report)
        prior = report["documents"]["specification"]["request"]["revalidates_evaluation_id"]
        if prior is not None:
            self.get(prior)  # Revalidation cannot point to a missing or corrupt experiment.
        payload = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with closing(sqlite3.connect(self.database, timeout=10)) as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            try:
                attempt = connection.execute("SELECT payload FROM factor_evaluation_attempts "
                    "WHERE evaluation_id=?", (report["evaluation_id"],)).fetchone()
                if attempt is not None:
                    started = json.loads(attempt[0])
                    _require(started["request"]
                             == report["documents"]["specification"]["request"],
                             "ATTEMPT_REQUEST_MISMATCH")
                _require(connection.execute("SELECT 1 FROM factor_evaluation_failures "
                    "WHERE evaluation_id=?", (report["evaluation_id"],)).fetchone() is None,
                    "FAILED_ATTEMPT_CANNOT_COMPLETE")
                connection.execute("INSERT INTO factor_evaluations VALUES(?,?,?)",
                    (report["evaluation_id"], payload, sha256(payload.encode()).hexdigest()))
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                existing = connection.execute("SELECT 1 FROM factor_evaluations "
                    "WHERE evaluation_id=?", (report["evaluation_id"],)).fetchone()
                if existing is not None:
                    raise FileExistsError("FACTOR_EVALUATION_IMMUTABLE_ID_EXISTS") from exc
                raise
        return {key: report[key] for key in ("evaluation_id", "specification_sha256",
                                             "report_manifest_sha256")}

    def get(self, evaluation_id: str) -> dict[str, Any]:
        self._guard()
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True,
                                    timeout=3)) as connection:
            connection.execute("PRAGMA query_only=ON")
            row = connection.execute("SELECT payload,payload_sha256 FROM factor_evaluations "
                                     "WHERE evaluation_id=?", (evaluation_id,)).fetchone()
        _require(row is not None, "UNKNOWN_EVALUATION_ID")
        assert row is not None
        _require(sha256(row[0].encode()).hexdigest() == row[1], "STORED_PAYLOAD_CHANGED")
        report = validate_report(json.loads(row[0]))
        _require(report["evaluation_id"] == evaluation_id, "STORED_ID_MISMATCH")
        return report
