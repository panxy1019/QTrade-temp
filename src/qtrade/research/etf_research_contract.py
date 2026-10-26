"""Prospectively frozen ETF allocations and source-bound SHADOW observations.

This separate archive channel has no order runtime or execution admission.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from qtrade.market_data.akshare import SHANGHAI
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.etf_trend import LIMITATIONS, verify_etf_archive
from qtrade.research.forward_prediction import next_execution
from qtrade.research.model_registry import checked_document, checked_identity
from qtrade.research.multi_asset import MomentumAllocation

SCHEMA = "qtrade.etf-research-contract.v1"
BLOCKERS = [
    "ETF_LOT_FEE_SETTLEMENT_AND_LIMIT_RULES_NOT_ADMITTED",
    "DISTRIBUTIONS_AND_SPLITS_NOT_RECONCILED",
    "CLOSE_ONLY_NOT_FULL_BAR_EXECUTION_INPUT",
]


def write_json_immutable(path: Path, value: dict[str, Any]) -> None:
    # Serverless publication validation excludes cluster scripts from its bundle.
    # Only cluster-side writers load the filesystem implementation.
    from scripts.atomic_json import write_json_immutable as write

    write(path, value)


def _same(left: object, right: object) -> bool:
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
        right, sort_keys=True, allow_nan=False)


@dataclass(frozen=True)
class EtfAllocationPolicy:
    top_k: int = 3
    risk_degree: Decimal = Decimal("0.95")

    def validate(self) -> None:
        if type(self.top_k) is not int or not 1 <= self.top_k <= 50:
            raise ValueError("ETF_CONTRACT_TOPK_INVALID")
        if (not isinstance(self.risk_degree, Decimal) or not self.risk_degree.is_finite()
                or not Decimal("0.1") <= self.risk_degree <= Decimal("0.95")):
            raise ValueError("ETF_CONTRACT_RISK_DEGREE_INVALID")


def etf_contract(policy: EtfAllocationPolicy) -> dict[str, Any]:
    policy.validate()
    return {
        "schema_version": SCHEMA,
        "security_type": "ETF",
        "frequency": "day",
        "universe_policy": "CURRENT_OBSERVED_ETF_TARGETS_NOT_HISTORICAL_PIT",
        "price_basis": "SINA_NATIVE_ADJUSTMENT_UNCERTIFIED",
        "signal": {"family": "etf_positive_trend20", "lookback_sessions": 20,
                   "formula": "close[t]/close[t-20]-1", "filter": "score>0"},
        "portfolio": {"method": "TOPK_EQUAL_WEIGHT_POSITIVE", "top_k": policy.top_k,
                      "risk_degree": str(policy.risk_degree), "rebalance_sessions": 1,
                      "weight_precision": "0.000000000001"},
        "execution": {"kind": "SHADOW_NO_EXECUTION", "intended_clock": "NEXT_EXCHANGE_OPEN",
                      "rules_admitted": False, "fee_policy": None, "settlement_policy": None},
        "evaluation": {"cohort": "ETF_NATIVE_CLOSE_SEPARATE_FROM_STOCK_MODELS",
                       "historical_state": "BLOCKED_PENDING_VERIFIED_SAME_COHORT_HISTORY",
                       "historical_metrics": None},
    }


def changed_etf_contract_axes(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    """Derive behavior changes; display names and observed outcomes are not axes."""
    for value in (left, right):
        if value.get("schema_version") != SCHEMA:
            raise ValueError("ETF_CONTRACT_SCHEMA_INVALID")
        portfolio = value["portfolio"]
        policy = EtfAllocationPolicy(portfolio["top_k"], Decimal(portfolio["risk_degree"]))
        if not _same(value, etf_contract(policy)):
            raise ValueError("ETF_CONTRACT_BEHAVIOR_INVALID")
    return [key for key in ("security_type", "frequency", "universe_policy", "price_basis",
                           "signal", "portfolio", "execution", "evaluation")
            if left[key] != right[key]]


def _calendar(calendar: Calendar | None) -> Calendar:
    return calendar or Calendar(Path(__file__).resolve().parents[3]/
                                "configs/observer_calendar_2026.json")


def _clock(now: datetime | None) -> datetime:
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise ValueError("ETF_CONTRACT_LIVE_MUST_REMAIN_DISABLED")
    stamp = now or datetime.now(UTC)
    if stamp.tzinfo is None:
        raise ValueError("ETF_CONTRACT_AWARE_CLOCK_REQUIRED")
    return stamp


def _source(root: Path, calendar: Calendar) -> tuple[dict[str, Any], datetime]:
    from scripts.unified_store import UnifiedBatchStore

    value = verify_etf_archive(root, calendar)
    checked_identity(root.name)
    receipt = json.loads((root/"receipt.json").read_bytes())
    captures = UnifiedBatchStore(root/"capture-store").read_captures(receipt["raw"])
    return value, max(datetime.fromisoformat(row["received_at"]) for row in captures)


def _load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or path.stat().st_size > 300_000:
        raise ValueError("ETF_CONTRACT_DOCUMENT_BOUND_INVALID")
    value: dict[str, Any] = json.loads(path.read_bytes())
    checked_document(value)
    if any(type(value.get(k)) is not type(v) for k, v in AUTHORITY.items()):
        raise ValueError("ETF_CONTRACT_AUTHORITY_INVALID")
    return value


def _inputs(root: Path, policy: EtfAllocationPolicy, revision: str,
            calendar: Calendar) -> dict[str, Any]:
    if not isinstance(revision, str) or re.fullmatch("[a-f0-9]{40}", revision) is None:
        raise ValueError("ETF_CONTRACT_IMPLEMENTATION_REVISION_REQUIRED")
    contract = etf_contract(policy)
    diagnostic, observed = _source(root, calendar)
    if diagnostic["state"] != "CLOSE_DIAGNOSTIC_READY":
        raise ValueError("ETF_CONTRACT_INPUT_UNAVAILABLE_NOT_CASH")
    model = MomentumAllocation((20,), top_k=policy.top_k, rebalance_every=1,
                               reserve=Decimal(1)-policy.risk_degree)
    allocation = model.weights({symbol: tuple(Decimal(row["close"]) for row in rows)
                                for symbol, rows in diagnostic["close_histories"].items()},
                               session_index=0)
    assert allocation is not None
    weights = [{"instrument": ("SH" if symbol.startswith("5") else "SZ")+symbol,
                "weight": str(weight.quantize(Decimal("0.000000000001"), rounding=ROUND_DOWN))}
               for symbol, weight in sorted(allocation.items())]
    execution = next_execution(diagnostic["as_of"], calendar)
    bindings = {
        "contract_id": signed(contract)["evidence_sha256"], "diagnostic_id": root.name,
        "diagnostic_sha256": diagnostic["evidence_sha256"],
        "receipt_sha256": diagnostic["receipt_sha256"],
        "master_sha256": diagnostic["bindings"]["master_sha256"],
        "calendar_sha256": diagnostic["bindings"]["calendar_sha256"],
        "source_revision": diagnostic["bindings"]["code_revision"],
        "implementation_revision": revision, "as_of": diagnostic["as_of"],
        "expected_execution_at": execution.isoformat(),
        "weights_sha256": signed({"weights": weights})["evidence_sha256"],
    }
    return {"contract": contract, "bindings": bindings, "weights": weights,
            "source_observed_at": observed.isoformat(),
            "qualified_inputs": diagnostic["qualified_close_symbols"],
            "excluded_inputs": diagnostic["excluded"]}


def _target(inputs: dict[str, Any], frozen: datetime) -> dict[str, Any]:
    return signed({
        **AUTHORITY, "schema_version": "qtrade.etf-allocation-target.v1",
        "strategy_id": "etf_positive_trend20_native_close_rotation_v1", "mode": "SHADOW",
        "state": "FROZEN_RESEARCH_TARGET", "target_id": signed(inputs["bindings"])[
            "evidence_sha256"], **inputs, "generated_at": frozen.isoformat(),
        "frozen_at": frozen.isoformat(), "execution_admitted": False,
        "paper_admission": {"state": "BLOCKED", "reasons": BLOCKERS},
        "orders": [], "fills": [], "holdings": [], "cash": None,
        "net_asset_value": None, "forward_return": None,
        "limitations": LIMITATIONS+["TARGET_WEIGHTS_ARE_NOT_HOLDINGS",
                                     "SHADOW_HAS_NO_EXECUTION_OR_PORTFOLIO_RETURN"],
    })


def _verify_target(value: dict[str, Any], inputs: dict[str, Any], now: datetime) -> None:
    frozen = datetime.fromisoformat(value["frozen_at"])
    observed = datetime.fromisoformat(inputs["source_observed_at"])
    execution = datetime.fromisoformat(inputs["bindings"]["expected_execution_at"])
    if frozen.tzinfo is None or not observed <= frozen < execution or frozen > now:
        raise ValueError("ETF_CONTRACT_FREEZE_CLOCK_INVALID")
    if not _same(value, _target(inputs, frozen)):
        raise ValueError("ETF_CONTRACT_TARGET_REPLAY_MISMATCH")


def validate_etf_target(value: dict[str, Any], *, calendar: Calendar | None = None,
                        now: datetime | None = None) -> dict[str, Any]:
    """Strict bounded data-only admission; private raw replay remains separate."""
    try:
        if len(json.dumps(value, allow_nan=False).encode()) > 300_000:
            raise ValueError("ETF_CONTRACT_PUBLIC_BOUND")
        checked_document(value)
        stamp, calendar = _clock(now), _calendar(calendar)
        portfolio = value["contract"]["portfolio"]
        policy = EtfAllocationPolicy(portfolio["top_k"], Decimal(portfolio["risk_degree"]))
        if not _same(value["contract"], etf_contract(policy)):
            raise ValueError("ETF_CONTRACT_PUBLIC_BEHAVIOR_INVALID")
        bindings = value["bindings"]
        keys = {"contract_id", "diagnostic_id", "diagnostic_sha256", "receipt_sha256",
                "master_sha256", "calendar_sha256", "source_revision", "implementation_revision",
                "as_of", "expected_execution_at", "weights_sha256"}
        if set(bindings) != keys:
            raise ValueError("ETF_CONTRACT_PUBLIC_BINDINGS_INVALID")
        for key in keys-{"source_revision", "implementation_revision", "as_of",
                         "expected_execution_at"}:
            checked_identity(bindings[key])
        for key in ("source_revision", "implementation_revision"):
            if (not isinstance(bindings[key], str)
                    or re.fullmatch("[a-f0-9]{40}", bindings[key]) is None):
                raise ValueError("ETF_CONTRACT_PUBLIC_REVISION_INVALID")
        as_of = date.fromisoformat(bindings["as_of"])
        if (as_of.isoformat() != bindings["as_of"]
                or bindings["expected_execution_at"] != next_execution(
                    str(as_of), calendar).isoformat()
                or bindings["calendar_sha256"] != signed(calendar.document)["evidence_sha256"]
                or bindings["contract_id"] != signed(value["contract"])["evidence_sha256"]):
            raise ValueError("ETF_CONTRACT_PUBLIC_CONTEXT_INVALID")
        source_binding = {"master_sha256": bindings["master_sha256"], "as_of": str(as_of),
                          "code_revision": bindings["source_revision"],
                          "calendar_sha256": bindings["calendar_sha256"],
                          "contract": "ETF_CLOSE_DIAGNOSTIC_V1"}
        if signed(source_binding)["evidence_sha256"] != bindings["diagnostic_id"]:
            raise ValueError("ETF_CONTRACT_PUBLIC_SOURCE_ID_INVALID")
        weights, seen, total = value["weights"], set(), Decimal(0)
        if not isinstance(weights, list) or len(weights) > policy.top_k:
            raise ValueError("ETF_CONTRACT_PUBLIC_WEIGHTS_INVALID")
        for row in weights:
            if (set(row) != {"instrument", "weight"}
                    or not isinstance(row["instrument"], str)
                    or re.fullmatch(r"(?:SH5|SZ1)\d{5}", row["instrument"]) is None
                    or row["instrument"] in seen or not isinstance(row["weight"], str)
                    or len(row["weight"]) > 40):
                raise ValueError("ETF_CONTRACT_PUBLIC_WEIGHT_INVALID")
            weight = Decimal(row["weight"])
            if not weight.is_finite() or weight <= 0:
                raise ValueError("ETF_CONTRACT_PUBLIC_WEIGHT_INVALID")
            seen.add(row["instrument"])
            total += weight
        if (total > policy.risk_degree or signed({"weights": weights})["evidence_sha256"]
                != bindings["weights_sha256"]):
            raise ValueError("ETF_CONTRACT_PUBLIC_WEIGHT_SUM_INVALID")
        if weights:
            expected_weight = (policy.risk_degree/len(weights)).quantize(
                Decimal("0.000000000001"), rounding=ROUND_DOWN)
            if (any(row["weight"] != str(expected_weight) for row in weights)
                    or [row["instrument"][2:] for row in weights] != sorted(
                        row["instrument"][2:] for row in weights)):
                raise ValueError("ETF_CONTRACT_PUBLIC_EQUAL_WEIGHT_INVALID")
        qualified, excluded = value["qualified_inputs"], value["excluded_inputs"]
        if (type(qualified) is not int or not 1 <= qualified <= 50
                or len(weights) > qualified or not isinstance(excluded, list)
                or len(excluded)+qualified > 50):
            raise ValueError("ETF_CONTRACT_PUBLIC_COVERAGE_INVALID")
        excluded_symbols = set()
        for row in excluded:
            if (set(row) != {"symbol", "reason"} or not isinstance(row["symbol"], str)
                    or re.fullmatch(r"[15]\d{5}", row["symbol"]) is None
                    or row["symbol"] in excluded_symbols
                    or row["reason"] != "INCOMPLETE_OR_INVALID_21_SESSION_SOURCE"):
                raise ValueError("ETF_CONTRACT_PUBLIC_EXCLUSION_INVALID")
            excluded_symbols.add(row["symbol"])
        if {row["instrument"][2:] for row in weights} & excluded_symbols:
            raise ValueError("ETF_CONTRACT_PUBLIC_EXCLUDED_WEIGHT_INVALID")
        inputs = {key: value[key] for key in ("contract", "bindings", "weights",
                  "source_observed_at", "qualified_inputs", "excluded_inputs")}
        source_clock = datetime.fromisoformat(inputs["source_observed_at"])
        if (source_clock.tzinfo is None
                or calendar.expected(source_clock, "daily").date() != as_of):
            raise ValueError("ETF_CONTRACT_PUBLIC_SOURCE_CLOCK_INVALID")
        _verify_target(value, inputs, stamp)
        return value
    except (KeyError, TypeError, AttributeError, OverflowError, InvalidOperation) as error:
        raise ValueError("ETF_CONTRACT_PUBLIC_TARGET_INVALID") from error


def freeze_etf_target(archive: Path, output: Path, revision: str, *,
                      policy: EtfAllocationPolicy | None = None,
                      calendar: Calendar | None = None,
                      now: datetime | None = None) -> dict[str, Any]:
    """Durably freeze before next open; replay never creates retrospective evidence."""
    inputs = _inputs(archive, policy or EtfAllocationPolicy(), revision, _calendar(calendar))
    stamp = _clock(now)  # Recheck the real clock after expensive raw verification.
    identity = signed(inputs["bindings"])["evidence_sha256"]
    target = output/identity/"target.json"
    if target.exists():
        value = _load(target)
        _verify_target(value, inputs, stamp)
        _verify_admission(target, value, stamp)
        return value
    if not datetime.fromisoformat(inputs["source_observed_at"]) <= stamp < datetime.fromisoformat(
            inputs["bindings"]["expected_execution_at"]):
        raise ValueError("ETF_CONTRACT_PROSPECTIVE_FREEZE_REQUIRED")
    value = _target(inputs, stamp)
    validate_etf_target(value, calendar=calendar, now=stamp)
    write_json_immutable(target, value)
    # The target write includes file and directory fsync. A pre-write timestamp
    # cannot attest that this completed before open; a crash leaves no admission.
    durable = _clock(now)
    execution = datetime.fromisoformat(inputs["bindings"]["expected_execution_at"])
    if not stamp <= durable < execution:
        raise ValueError("ETF_CONTRACT_DURABLE_ADMISSION_LATE")
    write_json_immutable(target.parent/"admission.json", _admission(value, durable))
    _verify_admission(target, value, _clock(now))
    return value


def _admission(target: dict[str, Any], durable: datetime) -> dict[str, Any]:
    return signed({**AUTHORITY, "schema_version": "qtrade.etf-target-admission.v1",
                   "target_id": target["target_id"],
                   "target_sha256": target["evidence_sha256"],
                   "durable_at": durable.isoformat()})


def _verify_admission(target_path: Path, target: dict[str, Any], now: datetime) -> None:
    path = target_path.parent/"admission.json"
    if not path.exists():
        raise ValueError("ETF_CONTRACT_DURABLE_ADMISSION_MISSING")
    try:
        value = _load(path)
        durable = datetime.fromisoformat(value["durable_at"])
        frozen = datetime.fromisoformat(target["frozen_at"])
        execution = datetime.fromisoformat(target["bindings"]["expected_execution_at"])
        if (durable.tzinfo is None or not frozen <= durable < execution or durable > now
                or not _same(value, _admission(target, durable))):
            raise ValueError("ETF_CONTRACT_DURABLE_ADMISSION_INVALID")
    except (KeyError, TypeError, AttributeError, OverflowError) as error:
        raise ValueError("ETF_CONTRACT_DURABLE_ADMISSION_INVALID") from error


def verify_etf_target(target_path: Path, archive: Path, *, calendar: Calendar | None = None,
                      now: datetime | None = None) -> dict[str, Any]:
    """Recompute allocation and every bound source before admitting a stored target."""
    value = validate_etf_target(_load(target_path), calendar=calendar, now=now)
    portfolio = value["contract"]["portfolio"]
    policy = EtfAllocationPolicy(portfolio["top_k"], Decimal(portfolio["risk_degree"]))
    inputs = _inputs(archive, policy, value["bindings"]["implementation_revision"],
                     _calendar(calendar))
    _verify_target(value, inputs, _clock(now))
    _verify_admission(target_path, value, _clock(now))
    return value


def _shadow(target: dict[str, Any], bindings: dict[str, Any], session: str,
            source_clock: datetime, processed: datetime, closes: dict[str, str]) -> dict[str, Any]:
    selected = {row["instrument"] for row in target["weights"]}
    return signed({
        **AUTHORITY, "schema_version": "qtrade.etf-shadow-observation.v1",
        "mode": "SHADOW", "state": "SHADOW_OBSERVED", "bindings": bindings,
        "observation_id": signed(bindings)["evidence_sha256"], "session": session,
        "observed_at": source_clock.isoformat(), "processed_at": processed.isoformat(),
        "target_weights": target["weights"],
        "observed_closes": [{"instrument": symbol, "close": closes[symbol]}
                            for symbol in sorted(selected & closes.keys())],
        "missing_target_inputs": sorted(selected-closes.keys()),
        "execution_admitted": False, "paper_admission": {"state": "BLOCKED", "reasons": BLOCKERS},
        "orders": [], "fills": [], "holdings": [], "cash": None,
        "net_asset_value": None, "forward_return": None, "limitations": target["limitations"],
    })


def validate_etf_shadow_observation(value: dict[str, Any], target: dict[str, Any], *,
                                    calendar: Calendar | None = None,
                                    now: datetime | None = None) -> dict[str, Any]:
    try:
        if len(json.dumps(value, allow_nan=False).encode()) > 300_000:
            raise ValueError("ETF_CONTRACT_PUBLIC_BOUND")
        checked_document(value)
        calendar, stamp = _calendar(calendar), _clock(now)
        validate_etf_target(target, calendar=calendar, now=stamp)
        bindings = value["bindings"]
        if set(bindings) != {"target_id", "diagnostic_id", "diagnostic_sha256", "receipt_sha256"}:
            raise ValueError("ETF_CONTRACT_PUBLIC_OBSERVATION_BINDINGS_INVALID")
        for identity in bindings.values():
            checked_identity(identity)
        execution = datetime.fromisoformat(target["bindings"]["expected_execution_at"])
        observed = datetime.fromisoformat(value["observed_at"])
        processed = datetime.fromisoformat(value["processed_at"])
        if (bindings["target_id"] != target["target_id"] or observed.tzinfo is None
                or processed.tzinfo is None or not execution < observed <= processed <= stamp
                or value["session"] != execution.astimezone(SHANGHAI).date().isoformat()
                or calendar.expected(observed, "daily").date().isoformat() != value["session"]):
            raise ValueError("ETF_CONTRACT_PUBLIC_OBSERVATION_CLOCK_INVALID")
        rows = value["observed_closes"]
        if not isinstance(rows, list) or len(rows) > 50:
            raise ValueError("ETF_CONTRACT_PUBLIC_CLOSES_BOUND")
        closes = {}
        selected = {row["instrument"] for row in target["weights"]}
        for row in rows:
            if (set(row) != {"instrument", "close"} or row["instrument"] not in selected
                    or row["instrument"] in closes or not isinstance(row["close"], str)
                    or len(row["close"]) > 80):
                raise ValueError("ETF_CONTRACT_PUBLIC_CLOSE_INVALID")
            close = Decimal(row["close"])
            if not close.is_finite() or close <= 0:
                raise ValueError("ETF_CONTRACT_PUBLIC_CLOSE_INVALID")
            closes[row["instrument"]] = row["close"]
        expected = _shadow(target, bindings, value["session"], observed, processed, closes)
        if not _same(value, expected):
            raise ValueError("ETF_CONTRACT_PUBLIC_SHADOW_MISMATCH")
        return value
    except (KeyError, TypeError, AttributeError, OverflowError, InvalidOperation) as error:
        raise ValueError("ETF_CONTRACT_PUBLIC_OBSERVATION_INVALID") from error


def observe_etf_shadow(target_path: Path, target_archive: Path, observed_archive: Path, *,
                       calendar: Calendar | None = None,
                       now: datetime | None = None) -> dict[str, Any]:
    """Seal only actual next-session closes. This never enters the STOCK runtime."""
    calendar = _calendar(calendar)
    target = verify_etf_target(target_path, target_archive, calendar=calendar, now=now)
    observed, source_clock = _source(observed_archive, calendar)
    stamp = _clock(now)
    execution = datetime.fromisoformat(target["bindings"]["expected_execution_at"])
    if (observed["as_of"] != execution.astimezone(SHANGHAI).date().isoformat()
            or not execution < source_clock <= stamp):
        raise ValueError("ETF_CONTRACT_ACTUAL_NEXT_SESSION_REQUIRED")
    bindings = {"target_id": target["target_id"], "diagnostic_id": observed_archive.name,
                "diagnostic_sha256": observed["evidence_sha256"],
                "receipt_sha256": observed["receipt_sha256"]}
    identity = signed(bindings)["evidence_sha256"]
    path = target_path.parent/"observations"/(identity+".json")
    if path.exists():
        old = _load(path)
        processed = datetime.fromisoformat(old["processed_at"])
    else:
        old, processed = None, stamp
    if processed.tzinfo is None or not source_clock <= processed <= stamp:
        raise ValueError("ETF_CONTRACT_OBSERVATION_CLOCK_INVALID")
    closes = {("SH" if symbol.startswith("5") else "SZ")+symbol: rows[-1]["close"]
              for symbol, rows in observed["close_histories"].items()}
    value = _shadow(target, bindings, observed["as_of"], source_clock, processed, closes)
    validate_etf_shadow_observation(value, target, calendar=calendar, now=stamp)
    if old is not None:
        if not _same(old, value):
            raise ValueError("ETF_CONTRACT_IMMUTABLE_OBSERVATION_CHANGED")
    else:
        write_json_immutable(path, value)
    return value


def verify_etf_shadow_observation(path: Path, target_path: Path, target_archive: Path,
                                  observed_archive: Path, *, calendar: Calendar | None = None,
                                  now: datetime | None = None) -> dict[str, Any]:
    """Read-only reconstruction; missing observations are never created by verification."""
    calendar = _calendar(calendar)
    target = verify_etf_target(target_path, target_archive, calendar=calendar, now=now)
    value = validate_etf_shadow_observation(_load(path), target, calendar=calendar, now=now)
    observed, source_clock = _source(observed_archive, calendar)
    bindings = {"target_id": target["target_id"], "diagnostic_id": observed_archive.name,
                "diagnostic_sha256": observed["evidence_sha256"],
                "receipt_sha256": observed["receipt_sha256"]}
    closes = {("SH" if symbol.startswith("5") else "SZ")+symbol: rows[-1]["close"]
              for symbol, rows in observed["close_histories"].items()}
    expected = _shadow(target, bindings, observed["as_of"], source_clock,
                       datetime.fromisoformat(value["processed_at"]), closes)
    if not _same(value, expected):
        raise ValueError("ETF_CONTRACT_OBSERVATION_RAW_REPLAY_MISMATCH")
    return value
