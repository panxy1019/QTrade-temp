import copy
import json
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from qtrade.data import Bar
from qtrade.market_data import SINA_FIVE_MINUTE_SESSION_TIMES
from scripts.prepare_baostock_forward_preregistration import (
    _canonical_sha256,
    build_preregistration,
)
from scripts.probe_baostock_forward_session import validate_forward_session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = PROJECT_ROOT / "deployment" / "evidence"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_packaged_forward_preregistration_is_signed_and_code_bound() -> None:
    path = EVIDENCE_ROOT / "forward-observation" / "preregistration.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    unsigned = {
        key: value
        for key, value in report.items()
        if key != "preregistration_sha256"
    }
    expected_paths = {
        "probe_baostock_forward_session.py": (
            PROJECT_ROOT / "scripts/probe_baostock_forward_session.py"
        ),
        "run_baostock_locked_forward_observation.py": (
            PROJECT_ROOT / "scripts/run_baostock_locked_forward_observation.py"
        ),
        "minute_backtest.py": PROJECT_ROOT / "src/qtrade/research/minute_backtest.py",
    }

    assert _canonical_sha256(unsigned) == report["preregistration_sha256"]
    assert report["implementation_sha256"] == {
        name: sha256(source.read_bytes()).hexdigest()
        for name, source in expected_paths.items()
    }
    assert report["timing_disclosure"] == {
        "development_policy_locked_before_first_session_open": True,
        "evaluation_contract_fixed_after_first_session_open": True,
        "target_session_data_fetched_before_this_lock": False,
        "claim_as_blind_preregistration": False,
    }


def _session(symbol: str, trading_day: date, *, close_offset: str = "0") -> tuple[Bar, ...]:
    return tuple(
        Bar(
            symbol=symbol,
            exchange="SSE" if symbol.startswith("6") else "SZSE",
            timestamp=datetime.combine(trading_day, value, SHANGHAI),
            trading_day=trading_day,
            open=Decimal("10"),
            high=Decimal("10.2"),
            low=Decimal("9.9"),
            close=Decimal("10.1") + Decimal(close_offset),
            volume=Decimal("1000"),
        )
        for value in SINA_FIVE_MINUTE_SESSION_TIMES
    )


def test_forward_preregistration_binds_locked_policy_and_implementation() -> None:
    development = json.loads(
        (EVIDENCE_ROOT / "development-policy-search" / "report.json").read_text()
    )
    dataset = json.loads(
        (EVIDENCE_ROOT / "long-five-minute-market-data" / "manifest.json").read_text()
    )
    hashes = {
        "probe_baostock_forward_session.py": "1" * 64,
        "run_baostock_locked_forward_observation.py": "2" * 64,
        "minute_backtest.py": "3" * 64,
    }
    report = build_preregistration(
        development=development,
        dataset=dataset,
        created_at=datetime(2026, 8, 26, 10, 30, tzinfo=SHANGHAI),
        implementation_hashes=hashes,
    )
    unsigned = {
        key: value
        for key, value in report.items()
        if key != "preregistration_sha256"
    }

    assert _canonical_sha256(unsigned) == report["preregistration_sha256"]
    assert report["development_evidence"]["policy_id"] == (
        "dev-floor-098-slow12x48-v1"
    )
    assert report["collection_lock"]["required_total_bars_per_session"] == 3600
    assert report["collection_lock"]["partial_sessions_enter_dataset"] is False
    assert report["collection_lock"]["minimum_complete_future_sessions"] == 20
    assert report["timing_disclosure"]["claim_as_blind_preregistration"] is False
    assert report["causal_contract"]["external_order_transmission"] is False


def test_forward_session_requires_complete_grid_and_exact_anchor() -> None:
    symbols = ("600036", "000858")
    anchor_day = date(2026, 8, 25)
    target_day = date(2026, 8, 26)
    frozen = [bar for symbol in symbols for bar in _session(symbol, anchor_day)]
    fetched = {
        symbol: (*_session(symbol, anchor_day), *_session(symbol, target_day))
        for symbol in symbols
    }

    accepted, anchor_hash = validate_forward_session(
        symbols=symbols,
        anchor_day=anchor_day,
        target_day=target_day,
        frozen_bars=frozen,
        fetched_by_symbol=fetched,
    )
    assert len(accepted) == 96
    assert len(anchor_hash) == 64
    assert {bar.trading_day for bar in accepted} == {target_day}

    incomplete = dict(fetched)
    incomplete[symbols[0]] = incomplete[symbols[0]][:-1]
    with pytest.raises(ValueError, match="incomplete"):
        validate_forward_session(
            symbols=symbols,
            anchor_day=anchor_day,
            target_day=target_day,
            frozen_bars=frozen,
            fetched_by_symbol=incomplete,
        )

    revised = copy.deepcopy(fetched)
    revised[symbols[0]] = (
        *_session(symbols[0], anchor_day, close_offset="0.01"),
        *_session(symbols[0], target_day),
    )
    with pytest.raises(ValueError, match="anchor revision"):
        validate_forward_session(
            symbols=symbols,
            anchor_day=anchor_day,
            target_day=target_day,
            frozen_bars=frozen,
            fetched_by_symbol=revised,
        )
