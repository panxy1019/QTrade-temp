"""Nonproduction factor score -> native Qlib portfolio execution adapter.

Reuse audit: the frozen factor/evaluation/READY contracts own input admission;
Qlib 0.9.7 TopkDropout/Exchange own simulation; existing comparison metrics own
net-cost analytics. No registration, model fit, lifecycle or gateway operation.
An old registration's adapter=False remains historical metadata, never rewritten.
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.data_versions import file_hash
from qtrade.research.factor_evaluation import evaluate_daily, validate_report
from qtrade.research.factor_registry import FactorRegistry
from qtrade.research.factor_strategy_contract import (
    _SOURCE_FILES,
    validate_strategy,
    verify_bound_strategy,
)
from qtrade.research.workflow import canonical, digest

KIND = "FACTOR_STRATEGY_BACKTEST_V1"
REQUEST_SCHEMA = "qtrade.factor-strategy-backtest-request.v1"
SCORE_SCHEMA = "qtrade.factor-strategy-backtest-scores.v1"
RESULT_SCHEMA = "qtrade.factor-strategy-backtest-result.v1"
MAX_SCORE_ROWS = 100_000
SOURCES = (
    *_SOURCE_FILES,
    "src/qtrade/research/factor_strategy_backtest.py",
    "scripts/run_factor_strategy_backtest.py",
    "scripts/run_qlib_strategy_comparison.py",
    "src/qtrade/research/strategy_comparison.py",
    "scripts/run_qlib_baseline.py",
)
LIMITATIONS = [
    "NONPRODUCTION_RETROSPECTIVE_PORTFOLIO_DIAGNOSTIC",
    "CURRENT_POOL_SELECTION_AND_SURVIVORSHIP_BIAS",
    "NO_UNSEEN_OR_FORWARD_PERFORMANCE_CLAIM",
    "HISTORICAL_PIT_AND_VENDOR_ADJUSTMENTS_NOT_CERTIFIED",
    "DAILY_OPEN_LIMIT_AND_CAPACITY_PROXIES_NOT_AUCTION_REPLAY",
    "EXPLICIT_FEES_ARE_RESEARCH_ASSUMPTIONS_NOT_BROKER_QUOTES",
    "CASH_ZERO_REFERENCE_NOT_MARKET_BENCHMARK",
    "TERMINAL_HOLDINGS_MARKED_NOT_LIQUIDATED",
    "DECIMAL_SCORES_CONVERT_TO_NATIVE_FLOAT64_WITH_NATIVE_TIE_ORDER",
    "NO_MODEL_ACTIVATION_PAPER_OR_LIVE_AUTHORITY",
]


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError("FACTOR_BACKTEST_" + code)


def disabled() -> None:
    require(
        os.environ.get("LIVE_TRADING_ENABLED", "false").lower() == "false", "LIVE_DISABLED_REQUIRED"
    )


def validate_request(value: Any) -> dict[str, Any]:
    disabled()
    require(
        type(value) is dict
        and set(value)
        == {
            "schema_version",
            "kind",
            "job_id",
            "actor",
            "strategy_id",
            "strategy_version",
            "strategy_contract_sha256",
            "registration_job_id",
            "data_version_id",
            "source_revision",
            "live_trading_enabled",
        },
        "REQUEST_FIELDS",
    )
    require(
        len(canonical(value)) <= 16_000
        and value["schema_version"] == REQUEST_SCHEMA
        and value["kind"] == KIND
        and value["live_trading_enabled"] is False,
        "REQUEST_SCOPE",
    )
    for field, length in (
        ("job_id", 32),
        ("registration_job_id", 32),
        ("strategy_version", 64),
        ("strategy_contract_sha256", 64),
        ("data_version_id", 64),
        ("source_revision", 40),
    ):
        require(
            type(value[field]) is str
            and re.fullmatch(f"[a-f0-9]{{{length}}}", value[field]) is not None,
            "REQUEST_IDENTITY",
        )
    require(
        value["strategy_id"] == "factor-" + value["strategy_version"]
        and value["job_id"] != value["registration_job_id"]
        and type(value["actor"]) is str
        and re.fullmatch(r"[A-Za-z0-9._@-]{1,80}", value["actor"]) is not None,
        "REQUEST_ASSOCIATION",
    )
    return dict(value)


def source_snapshot() -> dict[str, str]:
    root = Path(__file__).resolve().parents[3]
    return {name: file_hash(root / name) for name in SOURCES}


class _OneReport:
    def __init__(self, report: dict[str, Any]):
        self.report = report

    def get(self, identity: str) -> dict[str, Any]:
        require(identity == self.report["evaluation_id"], "EVALUATION_REFERENCE")
        return self.report


def prepare_scores(
    definition: dict[str, Any],
    report: dict[str, Any],
    *,
    factors: FactorRegistry,
    version_root: Path,
    data_database: Path,
) -> dict[str, Any]:
    """Recompute the selected factor from actual READY bytes; never fit to labels.

    The existing evaluator supplies the exact common grid and both clocks. Its
    temporary in-memory revalidation is not saved as a new experiment/registry job.
    Include feature-valid rows even when that security's diagnostic label is null.
    """
    disabled()
    report = validate_report(report)
    definition = verify_bound_strategy(
        definition,
        factors=factors,
        evaluations=_OneReport(report),
        version_root=version_root,
        data_database=data_database,
    )
    original = report["documents"]["specification"]
    request = json.loads(canonical(original["request"]))
    selected = request["ordered_factor_refs"].index(definition["spec"]["factor_ref"])
    request["ordered_factor_refs"] = [definition["spec"]["factor_ref"]]
    require(
        len(request["instruments"]) * len(request["decisions"]) <= MAX_SCORE_ROWS, "SCORE_ROW_BOUND"
    )
    actual = evaluate_daily(
        version_root=version_root,
        data_database=data_database,
        registry=factors,
        request=request,
        now=datetime.fromisoformat(original["label"]["maturity_barrier"]),
    )
    documents = actual["documents"]
    for name in ("samples", "eligibility_ledger"):
        expected = [
            {**row, "factor_index": 0}
            for row in report["documents"][name]
            if row["factor_index"] == selected
        ]
        require(canonical(documents[name]) == canonical(expected), "READY_SCORE_REPLAY_MISMATCH")
    calendar = original["calendar"]
    binding = definition["evaluation_context"]["data_binding"]
    test = definition["spec"]["segments"]["test"]
    schedule = []
    for decision in request["decisions"]:
        offset = calendar.index(decision["session"])
        require(offset + 1 < len(calendar), "NEXT_EXECUTION_SESSION_MISSING")
        day = calendar[offset + 1]
        execution = datetime.combine(
            date.fromisoformat(day), time(9, 30), timezone(timedelta(hours=8))
        )
        require(
            test[0] <= day <= test[1]
            and day <= binding["actual_end"]
            and datetime.fromisoformat(decision["decision_at"]) < execution,
            "EXECUTION_OUTSIDE_FROZEN_TEST_OR_CLOCK",
        )
        schedule.append(
            {**decision, "execution_session": day, "execution_at": execution.isoformat()}
        )
    rows = []
    for sample, fact in zip(documents["samples"], documents["eligibility_ledger"], strict=True):
        require(
            (sample["session"], sample["instrument"]) == (fact["session"], fact["instrument"]),
            "SCORE_LEDGER_GRID",
        )
        rows.append(
            {
                "session": sample["session"],
                "instrument": sample["instrument"],
                "score": sample["factor_value"],
                "raw_diagnostic_label": sample["raw_label"],
                "feature_reason": fact["feature_reason"],
                "label_reason": fact["label_reason"],
                "dependencies_known_at_barrier": fact["dependencies_known_at_barrier"],
                "clock_reason": fact["clock_reason"],
            }
        )
    for item in schedule:
        require(
            any(row["session"] == item["session"] and row["score"] is not None for row in rows),
            "NO_VALID_SCORES_FOR_SESSION",
        )
    return signed(
        {
            **AUTHORITY,
            "schema_version": SCORE_SCHEMA,
            "strategy_id": definition["strategy_id"],
            "strategy_contract_sha256": definition["evidence_sha256"],
            "evaluation_ref": definition["spec"]["evaluation_ref"],
            "data_binding": binding,
            "factor_ref": definition["spec"]["factor_ref"],
            "schedule": schedule,
            "rows": rows,
            "knowledge_mode": request["knowledge_mode"],
            "actual_ready_recomputed": True,
            "labels_used_to_select_or_trade": False,
            "source_samples_sha256": digest(documents["samples"]),
            "source_eligibility_sha256": digest(documents["eligibility_ledger"]),
            "actual_recomputed_at": actual["computed_at"],
            "execution_policy": "QLIB_0_9_7_NATIVE_PREVIOUS_SESSION_SIGNAL_NO_DOUBLE_SHIFT",
            "limitations": LIMITATIONS,
        }
    )


def score_series(scores: dict[str, Any]) -> tuple[Any, Any]:
    import pandas as pd  # type: ignore[import-untyped]

    pairs, values, labels = [], [], []
    for row in scores["rows"]:
        if row["score"] is None:
            continue
        number = float(Decimal(row["score"]))
        require(math.isfinite(number), "NONFINITE_NATIVE_SCORE")
        pairs.append((pd.Timestamp(row["session"]), row["instrument"]))
        values.append(number)
        labels.append(
            float(Decimal(row["raw_diagnostic_label"]))
            if row["raw_diagnostic_label"] is not None
            else float("nan")
        )
    index = pd.MultiIndex.from_tuples(pairs, names=["datetime", "instrument"])
    require(not index.has_duplicates, "DUPLICATE_NATIVE_SCORE")
    return (
        pd.Series(values, index=index, name="score", dtype=float).sort_index(),
        pd.Series(labels, index=index, name="label", dtype=float).sort_index(),
    )


def native_config(definition: dict[str, Any]) -> dict[str, Any]:
    definition = validate_strategy(definition, verify_runtime_source=True)
    portfolio, execution = definition["spec"]["portfolio"], definition["spec"]["execution"]
    return {
        "portfolio": {
            "topk": portfolio["topk"],
            "n_drop": portfolio["n_drop"],
            "hold_thresh": portfolio["hold_thresh"],
            "risk_degree": float(portfolio["risk_degree"]),
            "only_tradable": False,
            "forbid_all_trade_at_limit": False,
        },
        "exchange": {
            "freq": "day",
            "deal_price": "open",
            "trade_unit": 100,
            "limit_threshold": ("$buy_block", "$sell_block"),
            "volume_threshold": ("cum", "$capacity"),
            "open_cost": float(Decimal(execution["buy_cost_bps"]) / 10000),
            "close_cost": float(Decimal(execution["sell_cost_bps"]) / 10000),
            "min_cost": float(execution["min_cost"]),
            "impact_cost": 0.0,
        },
        "account": float(execution["initial_cash"]),
        "benchmark": None,
    }


def validate_quotes(frame: Any, instruments: list[str], days: list[str]) -> None:
    """No missing factor can silently disable lot rounding; missing masks fail closed."""
    import numpy as np
    import pandas as pd

    require(
        set(frame.index.names) == {"instrument", "datetime"} and not frame.index.has_duplicates,
        "QUOTE_INDEX",
    )
    expected = pd.MultiIndex.from_product(
        [instruments, pd.to_datetime(days)], names=["instrument", "datetime"]
    )
    quotes = frame.reorder_levels(["instrument", "datetime"]).reindex(expected)
    for field in ("$buy_block", "$sell_block", "$capacity"):
        require(field in quotes and np.isfinite(quotes[field]).all(), "MASK_OR_CAPACITY_MISSING")
    require(
        quotes["$buy_block"].isin([0, 1]).all()
        and quotes["$sell_block"].isin([0, 1]).all()
        and (quotes["$capacity"] >= 0).all(),
        "MASK_OR_CAPACITY_INVALID",
    )
    for _, row in quotes.iterrows():
        close = row["$close"]
        if math.isfinite(close) and close > 0:
            require(
                math.isfinite(row["$factor"]) and row["$factor"] > 0,
                "FACTOR_MISSING_WOULD_DISABLE_LOTS",
            )
        tradable = (
            math.isfinite(close) and close > 0 and math.isfinite(row["$open"]) and row["$open"] > 0
        )
        require(
            tradable or (row["$buy_block"] == row["$sell_block"] == 1 and row["$capacity"] == 0),
            "MISSING_PRICE_NOT_EXPLICITLY_BLOCKED",
        )


def validate_portfolio_report(report: Any, days: list[str]) -> None:
    import numpy as np

    require(
        not report.empty
        and not report.index.has_duplicates
        and [str(day.date()) for day in report.index] == days,
        "EXECUTION_REPORT_CALENDAR",
    )
    for field in ("return", "cost", "turnover", "bench"):
        require(
            field in report and np.isfinite(report[field].to_numpy()).all(),
            "NONFINITE_PORTFOLIO_REPORT",
        )
    require(
        (report["cost"] >= 0).all()
        and (report["turnover"] >= 0).all()
        and (report["return"] - report["cost"] > -1).all()
        and (report["bench"] == 0).all(),
        "INVALID_PORTFOLIO_REPORT",
    )


def native_backtest(
    provider: Path, definition: dict[str, Any], scores: dict[str, Any]
) -> tuple[Any, list[dict[str, Any]], dict[str, Any]]:
    """Only native Qlib is accepted; absence never returns simulated fixture results."""
    disabled()
    qlib = import_module("qlib")
    require(qlib.__version__ == "0.9.7", "AUDITED_QLIB_0_9_7_REQUIRED")
    qlib.init(
        provider_uri=str(provider),
        region="cn",
        kernels=1,
        expression_cache=None,
        dataset_cache=None,
    )
    data = import_module("qlib.data").D
    native = import_module("qlib.contrib.strategy.signal_strategy")
    backtest = import_module("qlib.contrib.evaluate").backtest_daily
    days = [row["execution_session"] for row in scores["schedule"]]
    instruments = definition["evaluation_context"]["instruments"]
    quotes = data.features(
        instruments,
        ["$open", "$close", "$factor", "$buy_block", "$sell_block", "$capacity"],
        start_time=days[0],
        end_time=days[-1],
    )
    validate_quotes(quotes, instruments, days)
    prediction, _ = score_series(scores)
    config = native_config(definition)
    strategy = native.TopkDropoutStrategy(signal=prediction, **config["portfolio"])
    # Qlib benchmark=None yields null bench values, not a zero-return benchmark.
    # Supply an explicit cash reference; no market index is silently selected.
    pd = import_module("pandas")
    cash_reference = pd.Series(0.0, index=pd.to_datetime(days))
    report, positions = backtest(
        start_time=days[0],
        end_time=days[-1],
        strategy=strategy,
        account=config["account"],
        benchmark=cash_reference,
        exchange_kwargs={**config["exchange"], "codes": instruments},
    )
    validate_portfolio_report(report, days)
    holdings = []
    for day, position in positions.items():
        cash = float(position.get_cash())
        stocks = {
            code: float(position.get_stock_amount(code))
            for code in sorted(position.get_stock_list())
        }
        require(
            math.isfinite(cash)
            and cash >= -1e-6
            and set(stocks) <= set(instruments)
            and all(math.isfinite(v) and v >= 0 for v in stocks.values()),
            "POSITION_AUTHORITY",
        )
        holdings.append({"session": str(day.date()), "cash": cash, "holdings": stocks})
    require([row["session"] for row in holdings] == days, "POSITION_CALENDAR")
    import inspect
    from importlib.metadata import version

    packages = {name: version(name) for name in ("pyqlib", "pandas", "numpy")}
    packages["qlib_signal_strategy_source_sha256"] = file_hash(Path(inspect.getfile(native)))
    packages["qlib_exchange_source_sha256"] = file_hash(
        Path(inspect.getfile(import_module("qlib.backtest.exchange")))
    )
    return report, holdings, packages
