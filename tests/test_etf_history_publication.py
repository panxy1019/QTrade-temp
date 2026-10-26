from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import ROUND_UP, Inexact, localcontext
from hashlib import sha256

import pytest
from test_etf_history_comparison import execute, inputs  # noqa: F401

from qtrade.monitoring.data_freshness import signed
from qtrade.research.etf_history_publication import (
    MAXIMUM_BYTES,
    STRATEGIES,
    build_etf_history_publication,
    validate_etf_history_publication,
)


def resign(value):
    return signed({key: item for key, item in value.items() if key != "evidence_sha256"})


@pytest.fixture
def publication(inputs):  # noqa: F811
    root, _, _ = inputs
    _, _, _, report = execute(inputs)
    directory = root/"results"/report["simulation_id"]
    return directory, build_etf_history_publication(directory)


def reseal(value):
    """An attacker can recalculate SHA256; semantic checks must still reject bad claims."""
    value = deepcopy(value)
    for name, artifact in list(value["artifacts"].items()):
        value["artifacts"][name] = artifact = resign(artifact)
        value["report"]["artifacts"][name] = {
            "evidence_sha256": artifact["evidence_sha256"],
            "file_sha256": sha256((json.dumps(artifact, ensure_ascii=False, indent=2)+"\n").encode(
            )).hexdigest()}
    value["report"] = resign(value["report"])
    return resign(value)


def test_builder_is_read_only_and_preserves_complete_bounded_documents(publication):
    root, value = publication
    before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert validate_etf_history_publication(value) == value
    assert build_etf_history_publication(root) == value
    assert {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
    assert len(value["artifacts"]) == 6
    assert value["raw_source_replay_performed_by_public_validator"] is False
    assert value["historical_simulation_only"] is True
    assert value["report"]["paper_admission"] == "BLOCKED"
    assert len(json.dumps(value).encode()) < MAXIMUM_BYTES
    assert len(value["artifacts"][STRATEGIES[1]+"/ledger.json"]["records"]) == 55


@pytest.mark.parametrize("damage", ["authority", "paper", "adjustment", "step1", "raw_replay",
    "private_path", "missing_artifact", "hash", "strategy", "metric_nan", "metric_float",
    "metric_forgery", "missing_limit", "cash", "weight", "cohort", "future", "source_clock",
    "warmup", "grid", "cost", "lot_bool", "contract", "negative_position", "trade_day",
    "trade_qty", "trade_fee", "trade_price", "holding", "target_day", "target_last",
    "source_limit", "opening_nav", "pnl_offset", "source_missing", "source_cohort",
    "source_bad_ohlcv", "different_source_bars", "positive20_target"])
def test_resigned_semantic_overclaims_and_inconsistent_journals_fail_closed(publication, damage):
    _, original = publication
    value = deepcopy(original)
    report = value["report"]
    ledger = value["artifacts"][STRATEGIES[1]+"/ledger.json"]["records"]
    trades = value["artifacts"][STRATEGIES[1]+"/trades.json"]["records"]
    targets = value["artifacts"][STRATEGIES[1]+"/targets.json"]["records"]
    if damage == "authority":
        value["live_trading_enabled"] = 0
    elif damage == "paper":
        report["paper_admission"] = "ADMITTED"
    elif damage == "adjustment":
        report["return_adjustment_certified"] = True
    elif damage == "step1":
        report["whole_step1_certified"] = True
    elif damage == "raw_replay":
        value["raw_source_replay_performed_by_public_validator"] = True
    elif damage == "private_path":
        report["path"] = "/root/private"
    elif damage == "missing_artifact":
        value["artifacts"].pop(STRATEGIES[0]+"/targets.json")
    elif damage == "hash":
        report["bindings"]["audit_sha256"] = "not-a-hash"
    elif damage == "strategy":
        report["strategies"]["stock_model"] = report["strategies"].pop(STRATEGIES[0])
    elif damage in {"metric_nan", "metric_float", "metric_forgery"}:
        report["strategies"][STRATEGIES[0]]["total_return"] = {
            "metric_nan": "NaN", "metric_float": .1, "metric_forgery": "99"}[damage]
    elif damage == "missing_limit":
        report["limitations"] = report["limitations"][1:]
    elif damage == "source_limit":
        report["source_audit_missing_inputs"] = report["source_audit_missing_inputs"][1:]
    elif damage == "cash":
        ledger[-1]["cash"] = "100000000"
    elif damage == "opening_nav":
        ledger[-1]["equity_at_open"] = "100000000"
    elif damage == "pnl_offset":
        from decimal import Decimal
        ledger[-1]["prior_holdings_price_pnl"] = str(
            Decimal(ledger[-1]["prior_holdings_price_pnl"])+100)
        ledger[-1]["trade_open_to_close_pnl"] = str(
            Decimal(ledger[-1]["trade_open_to_close_pnl"])-100)
    elif damage == "source_missing":
        ledger[0].pop("source_bars")
    elif damage == "source_cohort":
        ledger[0]["source_bars"] = ledger[0]["source_bars"][:-1]
    elif damage == "source_bad_ohlcv":
        ledger[0]["source_bars"][0]["high"] = "1"
    elif damage == "different_source_bars":
        ledger[0]["source_bars"][0]["volume"] = "10001"
    elif damage == "positive20_target":
        momentum_targets = value["artifacts"][STRATEGIES[0]+"/targets.json"]["records"]
        momentum_targets[-1]["weights"] = []
    elif damage == "weight":
        targets[0]["weights"] = targets[0]["weights"][:-1]
    elif damage == "cohort":
        report["bindings"]["cohort"] = report["bindings"]["cohort"][:-1]
    elif damage == "future":
        report["generated_at"] = (datetime.now(UTC)+timedelta(days=1)).isoformat()
    elif damage == "source_clock":
        report["generated_at"] = "2026-10-26T08:59:59+00:00"
    elif damage == "warmup":
        report["bindings"]["warmup_sessions"] = report["bindings"]["warmup_sessions"][:-1]
    elif damage == "grid":
        report["grid_selection"]["all_common_runs"][0].pop(3)
    elif damage == "cost":
        report["assumptions"]["cost_basis"] = "ACTUAL_BROKER_RULES"
    elif damage == "lot_bool":
        report["assumptions"]["lot_size"] = True
    elif damage == "contract":
        report["allocation_contract"]["execution"]["rules_admitted"] = True
    elif damage == "negative_position":
        ledger[-1]["holdings"][0]["quantity"] = -100
    elif damage == "trade_day":
        trades[0]["signal_as_of"] = trades[0]["session"]
    elif damage == "trade_qty":
        trades[0]["quantity"] += 1
    elif damage == "trade_fee":
        trades[0]["assumed_fee"] = "0"
    elif damage == "trade_price":
        trades[0]["execution_price"] = "99"
    elif damage == "holding":
        ledger[-1]["holdings"][0]["market_value"] = "1"
    elif damage == "target_day":
        targets[0]["next_execution_session"] = targets[0]["signal_as_of"]
    else:
        targets[-1]["execution_state"] = "SIMULATED_NEXT_OPEN"
    with pytest.raises(ValueError):
        validate_etf_history_publication(reseal(value))


def test_same_checks_work_under_hostile_callers_decimal_context(publication):
    _, value = publication
    with localcontext() as context:
        context.prec = 8
        context.rounding = ROUND_UP
        context.traps[Inexact] = True
        context.Emin, context.Emax = -50, 50
        context.clamp = 1
        assert validate_etf_history_publication(value) == value
        assert context.prec == 8 and context.traps[Inexact]


def test_builder_checks_actual_artifact_bytes_and_missing_file(publication):
    root, _ = publication
    path = root/STRATEGIES[0]/"ledger.json"
    path.write_bytes(path.read_bytes()+b" ")
    with pytest.raises(ValueError, match="ARTIFACT_HASH"):
        build_etf_history_publication(root)
    path.unlink()
    with pytest.raises(OSError):
        build_etf_history_publication(root)


def test_oversize_publication_is_rejected_before_traversing_untrusted_records(publication):
    _, value = publication
    value["padding"] = "x"*MAXIMUM_BYTES
    with pytest.raises(ValueError, match="TOO_LARGE"):
        validate_etf_history_publication(value)


def test_fresh_import_does_not_require_deploy_excluded_scripts():
    script = '''
import importlib.abc, sys
class NoScripts(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "scripts" or fullname.startswith("scripts."):
            raise AssertionError("DEPLOY_EXCLUDED_SCRIPT_IMPORT:"+fullname)
sys.meta_path.insert(0, NoScripts())
from qtrade.research.etf_history_publication import validate_etf_history_publication
assert callable(validate_etf_history_publication)
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            timeout=30, check=False)
    assert result.returncode == 0, result.stderr
