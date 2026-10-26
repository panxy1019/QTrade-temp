"""Data-only bounded historical simulation publication; no raw replay or execution authority.

This module deliberately imports no scripts. The cluster runner replays private
sources; public checks bind its documents and reconcile their simulated journals.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import (
    ROUND_DOWN,
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
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.metrics import evaluate_returns

SCHEMA = "qtrade.etf-history-publication.v1"
MAXIMUM_BYTES = 3_000_000
STRATEGIES = ("positive20_top3_daily", "fixed_pool_equal_weight_daily")
KINDS = ("ledger", "trades", "targets")
NAMES = {f"{strategy}/{kind}.json" for strategy in STRATEGIES for kind in KINDS}
METRICS = ("total_return", "annualized_return", "annualized_volatility", "sharpe",
           "maximum_drawdown", "benchmark_return", "excess_return", "information_ratio",
           "number_of_periods", "turnover", "exposure", "number_of_trades", "transaction_costs")
LIMITATIONS = [
    "ASSUMPTION_BASED_HISTORICAL_SIMULATION_NOT_PAPER_OR_FORWARD_EVIDENCE",
    "CURRENT_DIAGNOSTIC_COHORT_NOT_HISTORICAL_PIT_MEMBERSHIP",
    "SINA_NATIVE_PRICES_ADJUSTMENT_AND_DISTRIBUTIONS_SPLITS_NOT_RECONCILED",
    "LOT_FEE_SLIPPAGE_AND_NEXT_OPEN_FILL_RULES_ARE_SIMULATION_ASSUMPTIONS",
    "NO_CAPACITY_PARTICIPATION_SPREAD_LIMIT_SUSPENSION_OR_SETTLEMENT_ENFORCEMENT",
    "LONG_ONLY_NO_BORROWING_CASH_INTEREST_ZERO_NO_TERMINAL_LIQUIDATION",
    "RETROSPECTIVELY_CAPTURED_HISTORY_HAS_NO_HISTORICAL_AVAILABLE_AT_PROOF",
    "SHORT_COMMON_WINDOW_ANNUALIZED_METRICS_ARE_DESCRIPTIVE_NOT_CERTIFICATION",
    "NO_LEGACY_STITCHING_NO_FILL_FORWARD_NO_AUTOMATIC_SHADOW_METRIC_UPDATE",
]
CONTEXT = {"precision": 28, "rounding": "ROUND_HALF_EVEN", "emin": -999999,
           "emax": 999999, "capitals": 1, "clamp": 0,
           "traps": ["InvalidOperation", "DivisionByZero", "Overflow"]}
SOURCE_LIMITATIONS = {
    "HISTORICAL_PIT_MASTER_MEMBERSHIP_NOT_REPLAYED",
    "LONGER_COMMON_HISTORY_AND_AMOUNT_COVERAGE_NOT_CERTIFIED",
    "LEGACY_NEW_SOURCE_EQUIVALENCE_NOT_CERTIFIED",
    "DISTRIBUTIONS_SPLITS_AND_ADJUSTMENT_BASIS_NOT_RECONCILED",
    "ETF_LOT_FEE_SETTLEMENT_LIMIT_SUSPENSION_AND_PREVIOUS_VOLUME_RULES_NOT_ADMITTED",
    "ACTUAL_NEXT_SESSION_FORWARD_ACCEPTANCE_REQUIRED",
}


def _same(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
        right, sort_keys=True, allow_nan=False)


def _exact(value: Any, fields: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("ETF_HISTORY_PUBLIC_FIELDS_INVALID")


def _document(value: Any, fields: set[str]) -> None:
    _exact(value, fields | set(AUTHORITY) | {"evidence_sha256"})
    if (not _same({k: value[k] for k in AUTHORITY}, AUTHORITY)
            or not _same(signed({k: v for k, v in value.items()
                                 if k != "evidence_sha256"}), value)):
        raise ValueError("ETF_HISTORY_PUBLIC_CHECKSUM_OR_AUTHORITY_INVALID")


def _hash(value: Any, length: int = 64) -> None:
    if not isinstance(value, str) or re.fullmatch(f"[a-f0-9]{{{length}}}", value) is None:
        raise ValueError("ETF_HISTORY_PUBLIC_IDENTITY_INVALID")


def _integer(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("ETF_HISTORY_PUBLIC_INTEGER_INVALID")
    return int(value)


def _number(value: Any, minimum: Decimal | None = None,
            maximum: Decimal | None = None) -> Decimal:
    if not isinstance(value, str) or not 1 <= len(value) <= 100:
        raise ValueError("ETF_HISTORY_PUBLIC_NUMBER_INVALID")
    number = Decimal(value)
    if (not number.is_finite() or abs(number) > Decimal("1e100")
            or minimum is not None and number < minimum
            or maximum is not None and number > maximum):
        raise ValueError("ETF_HISTORY_PUBLIC_NUMBER_INVALID")
    return number


def _equal(left: Decimal, right: Decimal, relative: Decimal = Decimal("1e-18")) -> None:
    if abs(left-right) > relative*max(Decimal(1), abs(left), abs(right)):
        raise ValueError("ETF_HISTORY_PUBLIC_JOURNAL_OR_METRIC_MISMATCH")


def _date(value: Any, calendar: Calendar) -> date:
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("ETF_HISTORY_PUBLIC_DATE_INVALID")
    day = date.fromisoformat(value)
    if str(day) != value or not calendar.is_open(day):
        raise ValueError("ETF_HISTORY_PUBLIC_DATE_INVALID")
    return day


def _dates(value: Any, calendar: Calendar, minimum: int = 1) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= 3000:
        raise ValueError("ETF_HISTORY_PUBLIC_SESSIONS_INVALID")
    for day in value:
        _date(day, calendar)
    if value != sorted(set(value)):
        raise ValueError("ETF_HISTORY_PUBLIC_SESSIONS_INVALID")
    return list(value)


def _allocation_contract() -> dict[str, Any]:
    # Exact v1 public behavior, without importing the private archive adapter.
    return {"schema_version": "qtrade.etf-research-contract.v1", "security_type": "ETF",
        "frequency": "day", "universe_policy": "CURRENT_OBSERVED_ETF_TARGETS_NOT_HISTORICAL_PIT",
        "price_basis": "SINA_NATIVE_ADJUSTMENT_UNCERTIFIED",
        "signal": {"family": "etf_positive_trend20", "lookback_sessions": 20,
                   "formula": "close[t]/close[t-20]-1", "filter": "score>0"},
        "portfolio": {"method": "TOPK_EQUAL_WEIGHT_POSITIVE", "top_k": 3,
            "risk_degree": "0.95", "rebalance_sessions": 1, "weight_precision": "0.000000000001"},
        "execution": {"kind": "SHADOW_NO_EXECUTION", "intended_clock": "NEXT_EXCHANGE_OPEN",
                      "rules_admitted": False, "fee_policy": None, "settlement_policy": None},
        "evaluation": {"cohort": "ETF_NATIVE_CLOSE_SEPARATE_FROM_STOCK_MODELS",
            "historical_state": "BLOCKED_PENDING_VERIFIED_SAME_COHORT_HISTORY",
            "historical_metrics": None}}


def _assumptions(value: Any) -> tuple[Decimal, int, Decimal, Decimal]:
    fixed = {"status": "ASSUMED_NOT_EXECUTION_ADMITTED", "currency": "CNY",
        "risk_degree_both_strategies": "0.95", "cash_interest_rate": "0",
        "timing": "CLOSE_DECISION_NEXT_SESSION_OPEN", "warmup_closes": 21,
        "execution_order": "SELL_BEFORE_BUY_THEN_SYMBOL_ASCENDING",
        "cost_basis": "FILLED_NOTIONAL_TIMES_RATE_EACH_SIDE_NO_MINIMUM_OR_STAMP_TAX",
        "slippage_basis": "OPEN_TIMES_ONE_PLUS_BUY_OR_MINUS_SELL_RATE",
        "capacity": "UNLIMITED_ASSUMED_NO_VOLUME_OR_LIMIT_ADMISSION",
        "decimal_context": CONTEXT, "weight_precision": "0.000000000001",
        "weight_rounding": "ROUND_DOWN", "terminal_liquidation": False,
        "long_only": True, "borrowing": False}
    _exact(value, set(fixed) | {"initial_cash", "lot_size", "transaction_cost_rate",
                              "slippage_rate"})
    if not _same({k: value[k] for k in fixed}, fixed):
        raise ValueError("ETF_HISTORY_PUBLIC_ASSUMPTIONS_INVALID")
    cash = _number(value["initial_cash"], Decimal(1000), Decimal("1e12"))
    lot = _integer(value["lot_size"], 1, 10000)
    fee = _number(value["transaction_cost_rate"], Decimal(0), Decimal(".1"))
    slippage = _number(value["slippage_rate"], Decimal(0), Decimal(".1"))
    if slippage >= Decimal(".1"):
        raise ValueError("ETF_HISTORY_PUBLIC_ASSUMPTIONS_INVALID")
    return cash, lot, fee, slippage


def _bindings(value: Any, calendar: Calendar, generated: datetime) -> tuple[list[str], list[str]]:
    _exact(value, {"audit_sha256", "audit_file_sha256", "source_observed_at", "source_bindings",
        "implementation_revision", "implementation_sources", "cohort", "sessions",
        "warmup_sessions", "evaluation_sessions"})
    for key in ("audit_sha256", "audit_file_sha256"):
        _hash(value[key])
    _hash(value["implementation_revision"], 40)
    sources = {"scripts/run_etf_history_comparison.py", "scripts/audit_etf_history.py",
        "src/qtrade/research/multi_asset.py", "src/qtrade/research/metrics.py",
        "src/qtrade/research/etf_research_contract.py"}
    _exact(value["implementation_sources"], sources)
    for digest in value["implementation_sources"].values():
        _hash(digest)
    cohort = value["cohort"]
    if (not isinstance(cohort, list) or not 1 <= len(cohort) <= 50 or any(
            not isinstance(s, str) or re.fullmatch(r"[15]\d{5}", s) is None for s in cohort)
            or cohort != sorted(set(cohort))):
        raise ValueError("ETF_HISTORY_PUBLIC_COHORT_INVALID")
    grid = _dates(value["sessions"], calendar, 22)
    if value["warmup_sessions"] != grid[:21] or value["evaluation_sessions"] != grid[21:]:
        raise ValueError("ETF_HISTORY_PUBLIC_WARMUP_INVALID")
    source = value["source_bindings"]
    _exact(source, {"diagnostic_id", "diagnostic_sha256", "receipt_sha256", "raw", "master_sha256",
                   "calendar_sha256", "source_revision", "implementation_revision", "as_of"})
    for key in ("diagnostic_id", "diagnostic_sha256", "receipt_sha256", "master_sha256",
                "calendar_sha256"):
        _hash(source[key])
    for key in ("source_revision", "implementation_revision"):
        _hash(source[key], 40)
    if (source["as_of"] != grid[-1] or source["calendar_sha256"] != signed(calendar.document)[
            "evidence_sha256"]):
        raise ValueError("ETF_HISTORY_PUBLIC_SOURCE_CONTEXT_INVALID")
    observed = datetime.fromisoformat(value["source_observed_at"])
    if (observed.tzinfo is None or observed > generated
            or str(calendar.expected(observed, "daily").date()) != grid[-1]):
        raise ValueError("ETF_HISTORY_PUBLIC_SOURCE_CLOCK_INVALID")
    diagnostic_binding = {"master_sha256": source["master_sha256"], "as_of": grid[-1],
        "code_revision": source["source_revision"], "calendar_sha256": source["calendar_sha256"],
        "contract": "ETF_CLOSE_DIAGNOSTIC_V1"}
    if signed(diagnostic_binding)["evidence_sha256"] != source["diagnostic_id"]:
        raise ValueError("ETF_HISTORY_PUBLIC_DIAGNOSTIC_ID_INVALID")
    raw = source["raw"]
    _exact(raw, {"sha256", "bytes", "suffix", "captures", "uncompressed_bytes", "body_hashes"})
    _hash(raw["sha256"])
    for key in ("bytes", "uncompressed_bytes"):
        _integer(raw[key], 1, 64_000_000)
    if raw["suffix"] != ".jsonl.gz" or _integer(raw["captures"], 1, 50) != len(cohort):
        raise ValueError("ETF_HISTORY_PUBLIC_RAW_REFERENCE_INVALID")
    if not isinstance(raw["body_hashes"], list) or len(raw["body_hashes"]) != len(cohort):
        raise ValueError("ETF_HISTORY_PUBLIC_RAW_REFERENCE_INVALID")
    for digest in raw["body_hashes"]:
        _hash(digest)
    return cohort, grid


def _grid(value: Any, grid: list[str], cohort: list[str], calendar: Calendar) -> None:
    _exact(value, {"policy", "expected_sessions", "common_sessions", "all_common_runs",
        "selected_sessions", "excluded_latest_diagnostic_symbols_retained_in_cohort",
        "no_dynamic_cohort_shrinking", "legacy_rows_used"})
    if (value["policy"] != "LATEST_FULL_FIXED_COHORT_CONTIGUOUS_RUN_ENDING_AS_OF"
            or value["selected_sessions"] != grid
            or value["no_dynamic_cohort_shrinking"] is not True
            or type(value["legacy_rows_used"]) is not int or value["legacy_rows_used"] != 0):
        raise ValueError("ETF_HISTORY_PUBLIC_GRID_POLICY_INVALID")
    expected = _dates(value["expected_sessions"], calendar)
    common = _dates(value["common_sessions"], calendar)
    start, end = date.fromisoformat(expected[0]), date.fromisoformat(expected[-1])
    full = [str(start+timedelta(days=i)) for i in range((end-start).days+1)
            if calendar.is_open(start+timedelta(days=i))]
    if expected != full or expected[-1] != grid[-1] or not set(common) <= set(expected):
        raise ValueError("ETF_HISTORY_PUBLIC_GRID_INVALID")
    runs: list[list[str]] = []
    current: list[str] = []
    for day in expected:
        if day in set(common):
            current.append(day)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    if not runs or value["all_common_runs"] != runs or grid != runs[-1]:
        raise ValueError("ETF_HISTORY_PUBLIC_CONTIGUOUS_GRID_INVALID")
    excluded = value["excluded_latest_diagnostic_symbols_retained_in_cohort"]
    if not isinstance(excluded, list) or len(excluded) > len(cohort):
        raise ValueError("ETF_HISTORY_PUBLIC_EXCLUSIONS_INVALID")
    seen = set()
    for row in excluded:
        _exact(row, {"symbol", "reason"})
        if (row["symbol"] not in cohort or row["symbol"] in seen
                or row["reason"] != "INCOMPLETE_OR_INVALID_21_SESSION_SOURCE"):
            raise ValueError("ETF_HISTORY_PUBLIC_EXCLUSIONS_INVALID")
        seen.add(row["symbol"])


def _markets(ledger: Any, cohort: list[str], grid: list[str]
             ) -> list[dict[str, dict[str, Decimal]]]:
    if not isinstance(ledger, list) or len(ledger) != len(grid):
        raise ValueError("ETF_HISTORY_PUBLIC_JOURNAL_BOUND_INVALID")
    result = []
    for row in ledger:
        bars = row["source_bars"]
        if not isinstance(bars, list) or len(bars) != len(cohort):
            raise ValueError("ETF_HISTORY_PUBLIC_SOURCE_BARS_INVALID")
        market = {}
        symbols = []
        for bar in bars:
            _exact(bar, {"symbol", "open", "high", "low", "close", "volume"})
            symbols.append(bar["symbol"])
            values = {key: _number(bar[key], Decimal(0) if key == "volume" else Decimal("1e-100"))
                      for key in ("open", "high", "low", "close", "volume")}
            if not values["low"] <= min(values["open"], values["close"]) <= max(
                    values["open"], values["close"]) <= values["high"]:
                raise ValueError("ETF_HISTORY_PUBLIC_SOURCE_OHLCV_INVALID")
            market[bar["symbol"]] = values
        if symbols != cohort:
            raise ValueError("ETF_HISTORY_PUBLIC_SOURCE_COHORT_INVALID")
        result.append(market)
    return result


def _targets(records: Any, strategy: str, cohort: list[str], grid: list[str],
             markets: list[dict[str, dict[str, Decimal]]]) -> None:
    if not isinstance(records, list) or len(records) != len(grid)-20:
        raise ValueError("ETF_HISTORY_PUBLIC_TARGET_COUNT_INVALID")
    for index, row in enumerate(records, 20):
        _exact(row, {"session_index", "weights", "signal_as_of", "next_execution_session",
                     "execution_state"})
        last = index == len(grid)-1
        if (_integer(row["session_index"], 20, len(grid)-1) != index
                or row["signal_as_of"] != grid[index]
                or row["next_execution_session"] != (None if last else grid[index+1])
                or row["execution_state"] != (
                    "NO_NEXT_OPEN_IN_INPUT" if last else "SIMULATED_NEXT_OPEN")):
            raise ValueError("ETF_HISTORY_PUBLIC_TARGET_CLOCK_INVALID")
        weights = row["weights"]
        if not isinstance(weights, list) or len(weights) > (3 if strategy == STRATEGIES[0] else 50):
            raise ValueError("ETF_HISTORY_PUBLIC_TARGET_WEIGHTS_INVALID")
        symbols = []
        expected = (Decimal(".95")/len(weights)).quantize(
            Decimal(".000000000001"), rounding=ROUND_DOWN) if weights else None
        for weight in weights:
            _exact(weight, {"symbol", "weight"})
            symbols.append(weight["symbol"])
            if weight["symbol"] not in cohort or weight["weight"] != str(expected):
                raise ValueError("ETF_HISTORY_PUBLIC_TARGET_WEIGHTS_INVALID")
        if (symbols != sorted(set(symbols))
                or strategy == STRATEGIES[1] and symbols != cohort):
            raise ValueError("ETF_HISTORY_PUBLIC_TARGET_COHORT_INVALID")
        if strategy == STRATEGIES[0]:
            scores = {symbol: markets[index][symbol]["close"]/markets[index-20][symbol]["close"]-1
                      for symbol in cohort}
            selected = sorted((symbol for symbol in cohort if scores[symbol] > 0),
                              key=lambda symbol: (-scores[symbol], symbol))[:3]
            if symbols != sorted(selected):
                raise ValueError("ETF_HISTORY_PUBLIC_POSITIVE20_TARGET_MISMATCH")


LEDGER_NUMBERS = {"opening_cash", "equity_at_open", "cash", "holdings_value", "net_asset_value",
    "daily_return", "drawdown", "exposure", "gross_traded_notional", "turnover_initial_cash",
    "turnover_previous_nav", "assumed_fees", "assumed_slippage_cost", "prior_holdings_price_pnl",
    "trade_open_to_close_pnl"}
TRADE_NUMBERS = {"raw_open", "execution_price", "notional", "assumed_fee",
                 "assumed_slippage_cost", "cash_delta"}


def _journal(ledger: Any, trades: Any, targets: Any, cohort: list[str], grid: list[str],
             assumption: tuple[Decimal, int, Decimal, Decimal],
             markets: list[dict[str, dict[str, Decimal]]]) -> dict[str, Any]:
    initial, lot, fee_rate, slip_rate = assumption
    if (not isinstance(ledger, list) or len(ledger) != len(grid) or not isinstance(trades, list)
            or len(trades) > min(20000, len(grid)*len(cohort))):
        raise ValueError("ETF_HISTORY_PUBLIC_JOURNAL_BOUND_INVALID")
    by_day: dict[str, list[dict[str, Any]]] = {}
    last_order: tuple[str, int, str] | None = None
    for index, trade in enumerate(trades):
        _exact(trade, TRADE_NUMBERS | {"trade_index", "session", "signal_as_of", "symbol",
                                     "side", "quantity"})
        if (_integer(trade["trade_index"], 0, 20000) != index or trade["session"] not in grid[21:]
                or trade["symbol"] not in cohort or trade["side"] not in {"BUY", "SELL"}):
            raise ValueError("ETF_HISTORY_PUBLIC_TRADE_IDENTITY_INVALID")
        day_index = grid.index(trade["session"])
        if trade["signal_as_of"] != grid[day_index-1]:
            raise ValueError("ETF_HISTORY_PUBLIC_TRADE_CLOCK_INVALID")
        order = (trade["session"], 0 if trade["side"] == "SELL" else 1, trade["symbol"])
        if last_order is not None and order <= last_order:
            raise ValueError("ETF_HISTORY_PUBLIC_TRADE_ORDER_INVALID")
        last_order = order
        quantity = _integer(trade["quantity"], 1, 10**18)
        if quantity % lot:
            raise ValueError("ETF_HISTORY_PUBLIC_TRADE_LOT_INVALID")
        values = {key: _number(trade[key]) for key in TRADE_NUMBERS}
        _equal(values["raw_open"], markets[day_index][trade["symbol"]]["open"])
        if values["raw_open"] <= 0 or values["execution_price"] <= 0:
            raise ValueError("ETF_HISTORY_PUBLIC_TRADE_PRICE_INVALID")
        sign = 1 if trade["side"] == "BUY" else -1
        _equal(values["execution_price"], values["raw_open"]*(1+sign*slip_rate))
        _equal(values["notional"], quantity*values["execution_price"])
        _equal(values["assumed_fee"], values["notional"]*fee_rate)
        _equal(values["assumed_slippage_cost"], quantity*abs(
            values["execution_price"]-values["raw_open"]))
        _equal(values["cash_delta"], -sign*values["notional"]-values["assumed_fee"])
        if sign == 1 and trade["symbol"] not in {
                weight["symbol"] for weight in targets[day_index-21]["weights"]}:
            raise ValueError("ETF_HISTORY_PUBLIC_BUY_OUTSIDE_PREVIOUS_TARGET")
        by_day.setdefault(trade["session"], []).append(trade)
    cash, previous_nav, peak = initial, initial, initial
    positions: dict[str, int] = {}
    total_fee = total_notional = total_slip = Decimal(0)
    returns, exposures = [], []
    for index, row in enumerate(ledger):
        _exact(row, LEDGER_NUMBERS | {"session", "phase", "executed_signal_as_of",
                                     "opening_positions", "holdings", "source_bars",
                                     "reconciliation"})
        if (row["session"] != grid[index] or row["phase"] != (
                "WARMUP" if index < 21 else "EVALUATION") or row["reconciliation"] != "PASSED"
                or row["executed_signal_as_of"] != (grid[index-1] if index >= 21 else None)):
            raise ValueError("ETF_HISTORY_PUBLIC_LEDGER_CLOCK_INVALID")
        values = {key: _number(row[key]) for key in LEDGER_NUMBERS}
        _exact(row["opening_positions"], set(positions))
        for symbol, quantity in row["opening_positions"].items():
            if _integer(quantity, 0, 10**18) != positions[symbol]:
                raise ValueError("ETF_HISTORY_PUBLIC_OPENING_POSITION_INVALID")
        _equal(values["opening_cash"], cash)
        market = markets[index]
        opening_nav = cash+sum((quantity*market[symbol]["open"]
                                for symbol, quantity in positions.items()), start=Decimal(0))
        _equal(values["equity_at_open"], opening_nav)
        price_pnl = sum((quantity*(market[symbol]["close"]-markets[index-1][symbol]["close"])
                         for symbol, quantity in positions.items()), start=Decimal(0))
        _equal(values["prior_holdings_price_pnl"], price_pnl)
        trade_pnl = Decimal(0)
        if index >= 21:
            previous_weights = {weight["symbol"]: _number(weight["weight"])
                                for weight in targets[index-21]["weights"]}
            desired = {symbol: int((opening_nav*weight/(market[symbol]["open"]*lot)
                                   ).to_integral_value(rounding=ROUND_DOWN))*lot
                       for symbol, weight in previous_weights.items()}
            simulated_cash, simulated_positions = cash, dict(positions)
            expected_trades = []
            for side, symbols in (("SELL", sorted(set(positions) | set(desired))),
                                  ("BUY", sorted(desired))):
                sign = 1 if side == "BUY" else -1
                for symbol in symbols:
                    current_quantity = simulated_positions.get(symbol, 0)
                    requested = max(0, sign*(desired.get(symbol, 0)-current_quantity))
                    price = market[symbol]["open"]*(1+sign*slip_rate)
                    quantity = requested
                    if sign == 1:
                        affordable = int((simulated_cash/(price*lot*(1+fee_rate))
                                          ).to_integral_value(rounding=ROUND_DOWN))*lot
                        quantity = min(requested, affordable)
                    if quantity:
                        expected_trades.append((side, symbol, quantity))
                        simulated_cash -= sign*(quantity*price)+quantity*price*fee_rate
                        simulated_positions[symbol] = current_quantity+sign*quantity
            actual_trades = [(trade["side"], trade["symbol"], trade["quantity"])
                             for trade in by_day.get(row["session"], [])]
            if actual_trades != expected_trades:
                raise ValueError("ETF_HISTORY_PUBLIC_ASSUMED_REBALANCE_MISMATCH")
        daily_fee = daily_notional = daily_slip = Decimal(0)
        for trade in by_day.get(row["session"], []):
            sign = 1 if trade["side"] == "BUY" else -1
            cash += _number(trade["cash_delta"])
            positions[trade["symbol"]] = positions.get(trade["symbol"], 0)+sign*trade["quantity"]
            if cash < 0 or positions[trade["symbol"]] < 0:
                raise ValueError("ETF_HISTORY_PUBLIC_NEGATIVE_CASH_OR_POSITION")
            daily_fee += _number(trade["assumed_fee"])
            daily_notional += _number(trade["notional"])
            daily_slip += _number(trade["assumed_slippage_cost"])
            trade_pnl += sign*trade["quantity"]*(market[trade["symbol"]]["close"]-
                                                 market[trade["symbol"]]["open"])
        _equal(values["trade_open_to_close_pnl"], trade_pnl)
        holdings = row["holdings"]
        if not isinstance(holdings, list) or len(holdings) > len(cohort):
            raise ValueError("ETF_HISTORY_PUBLIC_HOLDINGS_INVALID")
        seen, held = [], {}
        market_value = Decimal(0)
        for holding in holdings:
            _exact(holding, {"symbol", "quantity", "close", "market_value"})
            symbol = holding["symbol"]
            if symbol not in cohort:
                raise ValueError("ETF_HISTORY_PUBLIC_HOLDING_COHORT_INVALID")
            seen.append(symbol)
            held[symbol] = _integer(holding["quantity"], 1, 10**18)
            if held[symbol] % lot:
                raise ValueError("ETF_HISTORY_PUBLIC_HOLDING_LOT_INVALID")
            close = _number(holding["close"], Decimal("1e-100"))
            _equal(close, market[symbol]["close"])
            value = _number(holding["market_value"], Decimal(0))
            _equal(value, held[symbol]*close)
            market_value += value
        if (seen != sorted(set(seen))
                or held != {key: value for key, value in positions.items() if value}):
            raise ValueError("ETF_HISTORY_PUBLIC_POSITION_JOURNAL_MISMATCH")
        nav = cash+market_value
        if nav <= 0:
            raise ValueError("ETF_HISTORY_PUBLIC_NONPOSITIVE_NAV")
        for key, expected in (("cash", cash), ("holdings_value", market_value),
                ("net_asset_value", nav), ("daily_return", nav/previous_nav-1),
                ("exposure", market_value/nav), ("assumed_fees", daily_fee),
                ("assumed_slippage_cost", daily_slip), ("gross_traded_notional", daily_notional),
                ("turnover_initial_cash", daily_notional/initial),
                ("turnover_previous_nav", daily_notional/previous_nav)):
            _equal(values[key], expected)
        _equal(nav-previous_nav, values["prior_holdings_price_pnl"]+
               values["trade_open_to_close_pnl"]-daily_slip-daily_fee)
        peak = max(peak, nav)
        _equal(values["drawdown"], nav/peak-1)
        if values["equity_at_open"] <= 0:
            raise ValueError("ETF_HISTORY_PUBLIC_OPENING_NAV_INVALID")
        if index < 21:
            if positions or cash != initial:
                raise ValueError("ETF_HISTORY_PUBLIC_WARMUP_TRADE_INVALID")
            _equal(values["equity_at_open"], initial)
        total_fee += daily_fee
        total_notional += daily_notional
        total_slip += daily_slip
        previous_nav = nav
        returns.append(values["daily_return"])
        exposures.append(values["exposure"])
    return {"cash": cash, "nav": previous_nav, "fees": total_fee, "slippage": total_slip,
            "turnover": total_notional/initial, "returns": returns[21:],
            "exposure": sum(exposures[21:], start=Decimal(0))/len(grid[21:]),
            "trade_count": len(trades)}


def _validate(value: dict[str, Any], now: datetime) -> dict[str, Any]:
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > MAXIMUM_BYTES:
        raise ValueError("ETF_HISTORY_PUBLICATION_TOO_LARGE")
    _document(value, {"schema_version", "historical_simulation_only",
        "raw_source_replay_performed_by_public_validator", "report", "artifacts"})
    if (value["schema_version"] != SCHEMA or value["historical_simulation_only"] is not True
            or value["raw_source_replay_performed_by_public_validator"] is not False):
        raise ValueError("ETF_HISTORY_PUBLIC_SCOPE_INVALID")
    report = value["report"]
    _document(report, {"schema_version", "simulation_id", "stage", "generated_at", "security_type",
        "execution_admitted", "paper_admission", "shadow_contract_modified",
        "whole_step1_certified",
        "return_adjustment_certified", "bindings", "assumptions", "allocation_contract",
        "allocation_contract_id", "grid_selection", "strategies", "artifacts", "limitations",
        "source_audit_missing_inputs"})
    if (report["schema_version"] != "qtrade.etf-history-comparison.v1"
            or report["stage"] != "ASSUMPTION_BASED_HISTORICAL_SIMULATION"
            or report["security_type"] != "ETF" or report["paper_admission"] != "BLOCKED"
            or any(report[key] is not False for key in (
                "execution_admitted", "shadow_contract_modified", "whole_step1_certified",
                "return_adjustment_certified"))):
        raise ValueError("ETF_HISTORY_PUBLIC_REPORT_SCOPE_INVALID")
    _hash(report["simulation_id"])
    generated = datetime.fromisoformat(report["generated_at"])
    if generated.tzinfo is None or generated > now:
        raise ValueError("ETF_HISTORY_PUBLIC_REPORT_CLOCK_INVALID")
    calendar = Calendar(Path(__file__).resolve().parents[3]/"configs/observer_calendar_2026.json")
    cohort, grid = _bindings(report["bindings"], calendar, generated)
    assumptions = _assumptions(report["assumptions"])
    _grid(report["grid_selection"], grid, cohort, calendar)
    if (not _same(report["allocation_contract"], _allocation_contract())
            or report["allocation_contract_id"] != signed(_allocation_contract())["evidence_sha256"]
            or report["simulation_id"] != signed({"bindings": report["bindings"],
                "assumptions": report["assumptions"]})["evidence_sha256"]):
        raise ValueError("ETF_HISTORY_PUBLIC_BEHAVIOR_ID_INVALID")
    for key in ("limitations", "source_audit_missing_inputs"):
        reasons = report[key]
        if (not isinstance(reasons, list) or not 1 <= len(reasons) <= 32 or any(
                not isinstance(reason, str)
                or re.fullmatch(r"[A-Z][A-Z0-9_]{0,199}", reason) is None
                for reason in reasons)):
            raise ValueError("ETF_HISTORY_PUBLIC_LIMITATIONS_INVALID")
    if not set(LIMITATIONS) <= set(report["limitations"]):
        raise ValueError("ETF_HISTORY_PUBLIC_LIMITATIONS_MISSING")
    if not set(report["source_audit_missing_inputs"]) >= SOURCE_LIMITATIONS:
        raise ValueError("ETF_HISTORY_PUBLIC_SOURCE_LIMITATIONS_MISSING")
    _exact(value["artifacts"], NAMES)
    _exact(report["artifacts"], NAMES)
    journals = {}
    first_source_bars = None
    for strategy in STRATEGIES:
        records = {}
        for kind in KINDS:
            name = f"{strategy}/{kind}.json"
            artifact = value["artifacts"][name]
            _document(artifact, {"schema_version", "simulation_id", "strategy_id",
                                 "historical_simulation_only", "records"})
            if (artifact["schema_version"] != f"qtrade.etf-historical-{kind}.v1"
                    or artifact["simulation_id"] != report["simulation_id"]
                    or artifact["strategy_id"] != strategy
                    or artifact["historical_simulation_only"] is not True):
                raise ValueError("ETF_HISTORY_PUBLIC_ARTIFACT_CONTEXT_INVALID")
            binding = report["artifacts"][name]
            _exact(binding, {"evidence_sha256", "file_sha256"})
            encoded = (json.dumps(artifact, ensure_ascii=False, indent=2)+"\n").encode()
            if (binding["evidence_sha256"] != artifact["evidence_sha256"]
                    or binding["file_sha256"] != sha256(encoded).hexdigest()):
                raise ValueError("ETF_HISTORY_PUBLIC_ARTIFACT_HASH_INVALID")
            records[kind] = artifact["records"]
        markets = _markets(records["ledger"], cohort, grid)
        source_bars = [row["source_bars"] for row in records["ledger"]]
        if first_source_bars is None:
            first_source_bars = source_bars
        elif not _same(source_bars, first_source_bars):
            raise ValueError("ETF_HISTORY_PUBLIC_STRATEGIES_USE_DIFFERENT_BARS")
        _targets(records["targets"], strategy, cohort, grid, markets)
        journals[strategy] = _journal(records["ledger"], records["trades"], records["targets"],
                                      cohort, grid, assumptions, markets)
    _exact(report["strategies"], set(STRATEGIES))
    for strategy in STRATEGIES:
        summary, journal = report["strategies"][strategy], journals[strategy]
        _exact(summary, set(METRICS) | {"terminal_cash", "terminal_net_asset_value",
            "total_assumed_fees", "total_assumed_slippage_cost", "reconciliation"})
        if summary["reconciliation"] != "PASSED":
            raise ValueError("ETF_HISTORY_PUBLIC_RECONCILIATION_REQUIRED")
        metrics = evaluate_returns(journal["returns"],
            benchmark_returns=journals[STRATEGIES[1]]["returns"], turnover=journal["turnover"],
            exposure=journal["exposure"], trade_count=journal["trade_count"],
            transaction_costs=journal["fees"]/assumptions[0])
        for key in METRICS:
            _equal(_number(summary[key]), Decimal(str(getattr(metrics, key))), Decimal("1e-12"))
        for key, expected in (("terminal_cash", journal["cash"]),
                ("terminal_net_asset_value", journal["nav"]),
                ("total_assumed_fees", journal["fees"]),
                ("total_assumed_slippage_cost", journal["slippage"])):
            _equal(_number(summary[key]), expected)
    return value


def validate_etf_history_publication(value: dict[str, Any], *,
                                     now: datetime | None = None) -> dict[str, Any]:
    """Check public data and internal accounting; never assert private raw replay."""
    stamp = now or datetime.now(UTC)
    if stamp.tzinfo is None:
        raise ValueError("ETF_HISTORY_PUBLIC_AWARE_CLOCK_REQUIRED")
    try:
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999,
                                 capitals=1, clamp=0,
                                 traps=[InvalidOperation, DivisionByZero, Overflow])):
            return _validate(value, stamp)
    except (KeyError, TypeError, AttributeError, OverflowError, RecursionError,
            InvalidOperation, DivisionByZero, Overflow) as error:
        raise ValueError("ETF_HISTORY_PUBLICATION_INVALID") from error


def build_etf_history_publication(root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Read one immutable comparison directory and six artifacts; never write or replay sources."""
    if root.is_symlink():
        raise ValueError("ETF_HISTORY_PUBLIC_ARCHIVE_SYMLINK")
    total = 0

    def read(name: str) -> tuple[dict[str, Any], bytes]:
        nonlocal total
        path = root/name
        if path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(
                root.resolve()) or not 0 < path.stat().st_size <= MAXIMUM_BYTES:
            raise ValueError("ETF_HISTORY_PUBLIC_INPUT_BOUND_INVALID")
        raw = path.read_bytes()
        total += len(raw)
        if total > MAXIMUM_BYTES:
            raise ValueError("ETF_HISTORY_PUBLICATION_TOO_LARGE")
        return json.loads(raw), raw

    report, _ = read("comparison.json")
    if report["simulation_id"] != root.name:
        raise ValueError("ETF_HISTORY_PUBLIC_DIRECTORY_ID_INVALID")
    artifacts = {}
    for name in sorted(NAMES):
        artifact, raw = read(name)
        if sha256(raw).hexdigest() != report["artifacts"][name]["file_sha256"]:
            raise ValueError("ETF_HISTORY_PUBLIC_ARTIFACT_HASH_INVALID")
        artifacts[name] = artifact
    envelope = signed({**AUTHORITY, "schema_version": SCHEMA, "historical_simulation_only": True,
        "raw_source_replay_performed_by_public_validator": False,
        "report": report, "artifacts": artifacts})
    return validate_etf_history_publication(envelope, now=now)
