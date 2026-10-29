"""Nonproduction reuse probes for STEP2; this does not implement a Factor Registry.

Only synthetic in-memory inputs and pytest temporary directories are used. These
checks establish reusable behavior, not historical PIT or native Qlib acceptance.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from qtrade.data import Bar
from qtrade.research.artifacts import ExperimentArtifactStore
from qtrade.research.data_versions import content_id
from qtrade.research.experiment import ExperimentRecord, TimeSplit
from qtrade.research.factor_search import diagnose_price_factors
from qtrade.research.factors import PriceFactorSpec
from qtrade.research.metrics import FactorObservation, evaluate_factor, evaluate_factor_periods
from qtrade.research.pit_repair import known_state
from qtrade.research.signal_baselines import FACTORS, average_ranks, signal_family


@pytest.fixture(autouse=True)
def research_only(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")


def _bars() -> list[Bar]:
    # Consecutive synthetic dates are index fixtures, not an exchange calendar.
    records = []
    for index in range(24):
        day = date(2020, 1, 1) + timedelta(days=index)
        for number, step in enumerate((".10", ".06", ".02", "-.01", "-.03"), start=1):
            close = Decimal(30) + Decimal(step) * index
            records.append(Bar(
                f"60000{number}", "SSE", datetime.combine(day, datetime.min.time(), UTC)
                + timedelta(hours=7), day, close, close, close, close, Decimal(10000),
            ))
    return records


def _diagnose(bars):
    return diagnose_price_factors(
        bars, (PriceFactorSpec("momentum", 2), PriceFactorSpec("reversal", 2)),
        start=date(2020, 1, 4), end=date(2020, 1, 18),
        sample_every=3, forward_sessions=2, quantiles=5,
    )


def test_daily_signal_aliases_resolve_to_existing_factor_values_and_rank_composition():
    days = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(21)]
    histories = {
        symbol: [{"date": day, "close": str(Decimal(30) + step * i)}
                 for i, day in enumerate(days)]
        for symbol, step in {"SH600001": Decimal(".1"), "SH600002": Decimal("-.1"),
                             "SH600003": Decimal("0")}.items()
    }
    result = signal_family(histories, days, as_of=days[-1], security_type="STOCK")
    ranks = {}
    for alias, spec in FACTORS.items():
        actual = {row["instrument"]: Decimal(str(row["score"]))
                  for row in result["signals"][alias]}
        expected = {symbol: spec.value(tuple(Decimal(row["close"]) for row in rows))
                    for symbol, rows in histories.items()}
        assert set(actual) == set(expected)
        assert all(abs(actual[symbol] - value) < Decimal("1e-14")
                   for symbol, value in expected.items())
        ranks[alias] = average_ranks(expected)
    composite = {row["instrument"]: Decimal(str(row["score"]))
                 for row in result["signals"]["multifactor_equal_rank"]}
    assert all(abs(composite[symbol] - sum(
        (values[symbol] for values in ranks.values()), Decimal(0)
    ) / len(ranks)) < Decimal("1e-14") for symbol in histories)


def test_discovery_report_cannot_change_when_only_prices_after_its_label_window_change():
    bars = _bars()
    original = _diagnose(bars)
    changed = []
    for bar in bars:
        price = bar.close * Decimal("1.01")
        changed.append(replace(bar, open=price, high=price, low=price, close=price)
                       if bar.trading_day > date(2020, 1, 18) else bar)
    assert _diagnose(changed) == original
    assert _diagnose([bar for bar in bars if bar.trading_day <= date(2020, 1, 18)]) == original


def test_rank_ic_and_groups_survive_positive_affine_feature_rescaling_with_ties():
    values = [Decimal(value) for value in (1, 1, 2, 3, 5, 8)]
    returns = [Decimal(value) / 100 for value in (2, 0, 1, 3, 6, 5)]
    first = evaluate_factor(values, returns, quantiles=3)
    rescaled = evaluate_factor([value * 7 + 23 for value in values], returns, quantiles=3)
    assert abs(first.ic - rescaled.ic) < Decimal("1e-14")
    assert first.rank_ic == rescaled.rank_ic
    assert first.quantile_returns == rescaled.quantile_returns
    assert first.long_short_spread == rescaled.long_short_spread


def test_period_evaluation_aligns_turnover_by_instrument_when_rows_are_reordered():
    periods = []
    for offset, scores in enumerate(((1, 2, 3, 4, 5, 6), (1, 2, 3, 5, 4, 6))):
        day = date(2020, 1, 2) + timedelta(days=offset)
        periods.append([
            FactorObservation(symbol, Decimal(score), Decimal(score) / 100, day,
                              datetime.combine(day, datetime.min.time(), UTC))
            for symbol, score in zip("ABCDEF", scores, strict=True)
        ])
    metrics = evaluate_factor_periods(periods, quantiles=3)
    assert metrics.factor_turnover == Decimal(".5")
    assert evaluate_factor_periods([list(reversed(period)) for period in periods],
                                   quantiles=3) == metrics


@pytest.mark.parametrize("late_clock", ["available_at", "observed_at"])
def test_dependency_reuse_rejects_either_clock_one_microsecond_after_decision(late_clock):
    decision = datetime(2020, 1, 2, 7, tzinfo=UTC)
    evidence = {
        "symbol": "600001", "date": "2020-01-02", "source": "synthetic-source",
        "source_sha256": "a" * 64, "available_at": decision.isoformat(),
        "observed_at": decision.isoformat(),
    }
    evidence[late_clock] = (decision + timedelta(microseconds=1)).isoformat()
    with pytest.raises(ValueError, match="STATE_NOT_KNOWN_AT_DECISION"):
        known_state(evidence, symbol="600001", day="2020-01-02", decision_at=decision)


def test_dependency_reuse_accepts_equal_instants_with_different_timezone_offsets():
    decision = datetime(2020, 1, 2, 7, tzinfo=UTC)
    evidence = {
        "symbol": "600001", "date": "2020-01-02", "source": "synthetic-source",
        "source_sha256": "a" * 64, "available_at": decision.isoformat(),
        "observed_at": decision.astimezone(timezone(timedelta(hours=8))).isoformat(),
    }
    assert known_state(evidence, symbol="600001", day="2020-01-02",
                       decision_at=decision) == evidence


@pytest.mark.parametrize("missing_clock", ["available_at", "observed_at"])
def test_dependency_reuse_does_not_invent_a_missing_clock(missing_clock):
    decision = datetime(2020, 1, 2, 7, tzinfo=UTC)
    evidence = {
        "symbol": "600001", "date": "2020-01-02", "source": "synthetic-source",
        "source_sha256": "a" * 64, "available_at": decision.isoformat(),
        "observed_at": decision.isoformat(),
    }
    evidence.pop(missing_clock)
    with pytest.raises(ValueError, match="STATE_AVAILABILITY_UNKNOWN"):
        known_state(evidence, symbol="600001", day="2020-01-02", decision_at=decision)


def test_later_pit_identity_creates_an_additional_experiment_and_preserves_old_bytes(tmp_path):
    files = {"provider/features.bin": "a" * 64}
    old_version = content_id(files, {"source_snapshot_sha256": "b" * 64})
    new_version = content_id(files, {"source_snapshot_sha256": "c" * 64})
    assert new_version != old_version  # Same prices do not erase new state provenance.
    split = TimeSplit(date(2017, 1, 1), date(2018, 12, 31), date(2019, 1, 1),
                      date(2019, 6, 30), date(2019, 7, 1), date(2020, 1, 2))
    original = ExperimentRecord(
        "synthetic-factor-eval-original", "d" * 40, old_version, ("600001", "600002"),
        ("momentum-20",), "synthetic-forward-return", split, "no-model-factor-diagnostic",
        {}, 0, Decimal(0), {"ic": Decimal(".1")}, (),
    )
    store = ExperimentArtifactStore(tmp_path / "isolated-experiments")
    original_path, original_hash = store.save(original)
    original_bytes = original_path.read_bytes()
    revised = replace(original, experiment_id="synthetic-factor-eval-pit-revalidation",
                      data_version=new_version, metrics={"ic": Decimal(".2")})
    revised_path, revised_hash = store.save(revised)
    assert revised_path != original_path and revised_hash != original_hash
    assert original_path.read_bytes() == original_bytes
    with pytest.raises(FileExistsError, match="immutable"):
        store.save(replace(revised, experiment_id=original.experiment_id))
    assert original_path.read_bytes() == original_bytes
    assert len(list(original_path.parent.glob("*.json"))) == 2
