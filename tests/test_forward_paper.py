from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest

from qtrade.domain import RuntimeMode
from qtrade.execution import FeeSchedule
from qtrade.research.forward_paper import (
    ForwardPaperBook,
    ForwardTarget,
    ObservedSecurity,
    ObservedSession,
)


def target(day=27, weights=None):
    return ForwardTarget(
        "momentum20",
        f"signal-{day}",
        "data-v1",
        "source-v1",
        datetime(2026, 10, day - 1, 10, tzinfo=UTC),
        datetime(2026, 10, day, 1, 30, tzinfo=UTC),
        weights if weights is not None else {"600000": D("0.5")},
        ("Current universe has survivorship bias",),
    )


def session(day=27, **updates):
    security = ObservedSecurity(
        "600000",
        "SSE",
        D("10"),
        D("11"),
        1_000_000,
        100,
        D("0.01"),
        False,
        False,
        D("11"),
        D("9"),
        True,
        "test-fixture-evidence",
        previous_session_volume=1_000_000,
    )
    security = replace(security, **updates)
    return ObservedSession(
        date(2026, 10, day),
        date(2026, 10, day - 1),
        date(2026, 10, day + 1),
        datetime(2026, 10, day, 1, 30, tzinfo=UTC),
        datetime(2026, 10, day, 7, tzinfo=UTC),
        datetime(2026, 10, day, 8, tzinfo=UTC),
        "test-calendar",
        "test-market",
        {"600000": security},
    )


def book(tmp_path, mode=RuntimeMode.PAPER):
    return ForwardPaperBook(
        tmp_path,
        strategy_id="momentum20",
        mode=mode,
        opening_cash=D("10000"),
        fees=FeeSchedule("fixture", D("0.0003"), D("0.0005"), D("5")),
    )


def freeze(b, t):
    return b.freeze(t, now=t.generated_at + timedelta(seconds=1))


def test_forward_pipeline_cash_fees_holdings_and_idempotent_restart(tmp_path):
    b = book(tmp_path)
    b.recover(actor="test human")
    t, market = target(), session()
    freeze(b, t)
    result = b.observe(t, market, now=market.observed_at)
    assert result["holdings"] == {"600000": 500}
    assert result["cash"] == "4995.00"
    assert result["net_asset_value"] == "10495.00"
    assert D(result["forward_return"]) == D(".0495")
    assert len(result["fills"]) == 1
    assert b.bundle.sellability.sellable_quantity("600000", market.trading_day) == 0
    assert b.observe(t, market, now=market.observed_at) == result
    restarted = book(tmp_path)
    assert restarted.bundle.risk.state.value == "HALTED"
    assert restarted.observe(t, market, now=market.observed_at) == result
    t2, m2 = target(28, {}), session(28)
    freeze(restarted, t2)
    with pytest.raises(RuntimeError):
        restarted.observe(t2, m2, now=m2.observed_at)
    restarted.recover(actor="test human recovery")
    sold = restarted.observe(t2, m2, now=m2.observed_at)
    assert sold["holdings"] == {}
    assert sold["cash"] == "9987.50"
    assert sold["fills"][0]["side"] == "SELL"


def test_shadow_never_creates_fill_holdings_or_return(tmp_path):
    b = book(tmp_path, RuntimeMode.SHADOW)
    t, m = target(), session()
    freeze(b, t)
    result = b.observe(t, m, now=m.observed_at)
    assert result["status"] == "SHADOW_OBSERVED"
    assert result["fills"] == [] and result["holdings"] == {}
    assert result["forward_return"] is None and result["cash"] is None
    assert not b.bundle.oms.orders


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"st": None}, "UNKNOWN_TRADING_CONSTRAINTS"),
        ({"suspended": True}, "SUSPENDED"),
        ({"st": True}, "ST_EXCLUDED"),
        ({"corporate_action_clear": False}, "CORPORATE_ACTION_REQUIRES_RECONCILIATION"),
        ({"raw_open": D("11")}, "LIMIT_UP_BUY_BLOCKED"),
        ({"previous_session_volume": 100}, "ONE_PERCENT_PREVIOUS_SESSION_VOLUME_LIMIT"),
        ({"previous_session_volume": None}, "UNKNOWN_PREVIOUS_SESSION_VOLUME"),
    ],
)
def test_execution_constraints_fail_closed(tmp_path, updates, reason):
    b = book(tmp_path)
    b.recover(actor="test human")
    t, m = target(), session(**updates)
    freeze(b, t)
    result = b.observe(t, m, now=m.observed_at)
    assert result["blocked"]["600000"] == reason
    assert result["fills"] == [] and result["holdings"] == {}


def test_no_retroactive_targets_future_session_or_rewriting(tmp_path):
    b = book(tmp_path)
    t, m = target(), session()
    with pytest.raises(ValueError, match="prospectively"):
        b.freeze(t, now=m.observed_at)
    with pytest.raises(ValueError, match="not frozen"):
        b.observe(t, m, now=m.observed_at)
    freeze(b, t)
    with pytest.raises(ValueError, match="completed"):
        b.observe(t, m, now=m.open_at)
    b.recover(actor="test human")
    b.observe(t, m, now=m.observed_at)
    with pytest.raises(ValueError, match="immutable session"):
        b.observe(t, replace(m, market_evidence="rewritten"), now=m.observed_at)


def test_partial_event_crash_blocks_automatic_replay_and_recovery(tmp_path, monkeypatch):
    b = book(tmp_path)
    b.recover(actor="test human")
    t, m = target(), session()
    freeze(b, t)
    original = b.bundle.gateway.fill

    def crash_after_fill(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("simulated interruption after durable fill")

    monkeypatch.setattr(b.bundle.gateway, "fill", crash_after_fill)
    with pytest.raises(RuntimeError, match="interruption"):
        b.observe(t, m, now=m.observed_at)
    assert b.bundle.ledger.account.position_quantity("600000") == 500
    assert b.bundle.risk.state.value == "HALTED"
    with pytest.raises(ValueError, match="uncertain"):
        b.recover(actor="test human")
    with pytest.raises(ValueError, match="uncertain"):
        b.observe(t, m, now=m.observed_at)
    assert b.bundle.ledger.account.position_quantity("600000") == 500


def test_missing_session_and_held_corporate_action_halt(tmp_path):
    b = book(tmp_path)
    b.recover(actor="test human")
    t, m = target(), session()
    freeze(b, t)
    b.observe(t, m, now=m.observed_at)
    skipped = target(29)
    freeze(b, skipped)
    with pytest.raises(ValueError, match="missing or out-of-order"):
        b.observe(skipped, session(29), now=session(29).observed_at)
    next_target, next_market = target(28), session(28, corporate_action_clear=None)
    freeze(b, next_target)
    result = b.observe(next_target, next_market, now=next_market.observed_at)
    assert result["status"] == "HALTED_UNVALUED"
    assert result["forward_return"] is None
    assert b.bundle.risk.state.value == "HALTED"


def test_immutable_account_identity_and_invalid_weights(tmp_path):
    book(tmp_path)
    with pytest.raises(ValueError, match="configuration mismatch"):
        book(tmp_path, RuntimeMode.SHADOW)
    with pytest.raises(ValueError, match="cash reserve"):
        target(weights={"600000": D("1")}).validate()
    with pytest.raises(ValueError, match="weights"):
        target(weights={"600000": D("NaN")}).validate()


def test_held_invalid_open_halts_before_new_orders(tmp_path):
    b = book(tmp_path)
    b.recover(actor="test human")
    t, m = target(), session()
    freeze(b, t)
    b.observe(t, m, now=m.observed_at)
    t2, m2 = target(28), session(28, raw_open=D("NaN"))
    freeze(b, t2)
    result = b.observe(t2, m2, now=m2.observed_at)
    assert result["status"] == "HALTED_UNVALUED"
    assert result["fills"] == [] and result["net_asset_value"] is None
    assert b.bundle.risk.state.value == "HALTED"


def test_limit_down_cannot_sell_existing_position(tmp_path):
    b = book(tmp_path)
    b.recover(actor="test human")
    t, m = target(), session()
    freeze(b, t)
    b.observe(t, m, now=m.observed_at)
    t2, m2 = target(28, {}), session(28, raw_open=D("9"))
    freeze(b, t2)
    result = b.observe(t2, m2, now=m2.observed_at)
    assert result["blocked"]["600000"] == "LIMIT_DOWN_SELL_BLOCKED"
    assert result["holdings"] == {"600000": 500} and result["fills"] == []


def test_malformed_constraints_cannot_admit_execution(tmp_path):
    b = book(tmp_path)
    b.recover(actor="test human")
    t, m = target(), session(corporate_action_clear="yes")
    freeze(b, t)
    result = b.observe(t, m, now=m.observed_at)
    assert result["blocked"]["600000"] == "INVALID_TRADING_CONSTRAINTS"
    assert result["fills"] == []


def test_paused_state_never_automatically_recovers(tmp_path):
    b = book(tmp_path)
    b.recover(actor="test human")
    b.bundle.runtime.pause(actor="test human pause")
    t, m = target(), session()
    freeze(b, t)
    with pytest.raises(RuntimeError, match="PAUSED"):
        b.observe(t, m, now=m.observed_at)
    assert b.bundle.risk.state.value == "PAUSED"


def test_live_environment_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(ValueError, match="remain false"):
        book(tmp_path)


def test_open_liquidity_cap_does_not_use_future_same_day_total_volume(tmp_path):
    results = []
    for volume in (0, 100, 100_000_000):
        b = book(tmp_path / str(volume))
        b.recover(actor="test human")
        t, m = target(), session(volume=volume, previous_session_volume=1_000_000)
        freeze(b, t)
        results.append(b.observe(t, m, now=m.observed_at))
    assert all(result["holdings"] == {"600000": 500} for result in results)
    assert all(len(result["fills"]) == 1 for result in results)
    assert all(not result["blocked"] for result in results)
