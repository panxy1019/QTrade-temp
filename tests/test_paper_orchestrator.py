from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Event, Thread

from qtrade.data import Bar
from qtrade.domain import Instrument, RuntimeMode
from qtrade.execution import FeeSchedule
from qtrade.instruments import (
    TradingCalendar,
    TradingDayStatus,
    TradingRule,
    TradingStatus,
)
from qtrade.risk import RiskLimits
from qtrade.runtime import PaperBarRunner, build_paper_runtime


class SequenceStrategy:
    def __init__(self, targets: list[int]) -> None:
        self._targets = iter(targets)

    def on_bar(self, bar: Bar, current_quantity: int) -> int:
        return next(self._targets)


def make_bar(day: date, hour: int, price: str = "10") -> Bar:
    value = Decimal(price)
    return Bar(
        "510300",
        "SSE",
        datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=hour),
        day,
        value,
        value,
        value,
        value,
        Decimal("100000"),
    )


def run_bar(runner: PaperBarRunner, bar: Bar):
    return runner.on_bar(bar, received_at=bar.timestamp)


def test_paper_bar_runner_uses_next_bar_pipeline_and_persists_t1_lots(tmp_path):
    days = (date(2026, 8, 12), date(2026, 8, 13), date(2026, 8, 14))
    calendar = TradingCalendar(
        tuple(
            TradingDayStatus("SSE", day, TradingStatus.OPEN, date(2026, 1, 1), "fixture")
            for day in days
        )
    )
    bundle = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=tmp_path / "runtime.db",
    )
    bundle.reconcile_and_recover(actor="operator")
    bundle.runtime.start(actor="operator")
    runner = PaperBarRunner(
        bundle=bundle,
        instrument=Instrument("510300", "SSE", "ETF", 100, Decimal("0.001")),
        trading_rule=TradingRule(
            "domestic-etf",
            "SSE",
            "DOMESTIC_EQUITY_ETF",
            date(2026, 1, 1),
            None,
            100,
            100,
            Decimal("0.001"),
            Decimal("0.1"),
            1,
            "fixture://rule",
            "1",
        ),
        calendar=calendar,
        strategy=SequenceStrategy([100, 0, 0, 0]),
        strategy_id="sequence",
        portfolio_id="paper",
    )

    first = run_bar(runner, make_bar(days[0], 1))
    bought = run_bar(runner, make_bar(days[0], 2))
    same_day_sell = run_bar(runner, make_bar(days[0], 3))
    next_day_sell = run_bar(runner, make_bar(days[1], 1))

    assert first.order is None
    assert bought.trade is not None and bought.trade.quantity == 100
    assert same_day_sell.order is not None
    assert same_day_sell.order.rejection_reason == "insufficient position"
    assert next_day_sell.trade is not None and next_day_sell.trade.side.value == "SELL"
    assert bundle.ledger.account.position_quantity("510300") == 0
    assert bundle.sellability.sellable_quantity("510300", days[1]) == 0

    restarted = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=tmp_path / "runtime.db",
    )
    assert restarted.ledger.account.position_quantity("510300") == 0
    assert restarted.sellability.sellable_quantity("510300", days[2]) == 0
    assert restarted.risk.state.value == "HALTED"


def test_open_t1_acquisition_lot_survives_restart(tmp_path):
    days = (date(2026, 8, 12), date(2026, 8, 13))
    calendar = TradingCalendar(
        tuple(
            TradingDayStatus("SSE", day, TradingStatus.OPEN, date(2026, 1, 1), "fixture")
            for day in days
        )
    )
    runtime_path = tmp_path / "open-lot.db"
    bundle = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=runtime_path,
    )
    bundle.reconcile_and_recover(actor="operator")
    bundle.runtime.start(actor="operator")
    runner = PaperBarRunner(
        bundle=bundle,
        instrument=Instrument("510300", "SSE", "ETF", 100, Decimal("0.001")),
        trading_rule=TradingRule(
            "domestic-etf",
            "SSE",
            "DOMESTIC_EQUITY_ETF",
            date(2026, 1, 1),
            None,
            100,
            100,
            Decimal("0.001"),
            Decimal("0.1"),
            1,
            "fixture://rule",
            "1",
        ),
        calendar=calendar,
        strategy=SequenceStrategy([100, 100]),
        strategy_id="sequence",
        portfolio_id="paper",
    )
    run_bar(runner, make_bar(days[0], 1))
    bought = run_bar(runner, make_bar(days[0], 2))
    assert bought.trade is not None

    restarted = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=runtime_path,
    )
    assert restarted.ledger.account.position_quantity("510300") == 100
    assert restarted.sellability.sellable_quantity("510300", days[0]) == 0
    assert restarted.sellability.sellable_quantity("510300", days[1]) == 100
    assert restarted.risk.state.value == "HALTED"


def test_pending_target_survives_restart_and_executes_on_next_bar(tmp_path):
    days = (date(2026, 8, 12), date(2026, 8, 13))
    calendar = TradingCalendar(
        tuple(
            TradingDayStatus("SSE", day, TradingStatus.OPEN, date(2026, 1, 1), "fixture")
            for day in days
        )
    )
    runtime_path = tmp_path / "pending-target.db"
    kwargs = {
        "mode": RuntimeMode.PAPER,
        "opening_cash": Decimal("100000"),
        "limits": RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        "fees": FeeSchedule("test", Decimal("0"), Decimal("0")),
        "journal_path": runtime_path,
    }
    instrument = Instrument("510300", "SSE", "ETF", 100, Decimal("0.001"))
    rule = TradingRule(
        "domestic-etf",
        "SSE",
        "DOMESTIC_EQUITY_ETF",
        date(2026, 1, 1),
        None,
        100,
        100,
        Decimal("0.001"),
        Decimal("0.1"),
        1,
        "fixture://rule",
        "1",
    )
    first_bundle = build_paper_runtime(**kwargs)
    first_bundle.reconcile_and_recover(actor="operator")
    first_bundle.runtime.start(actor="operator")
    first_runner = PaperBarRunner(
        bundle=first_bundle,
        instrument=instrument,
        trading_rule=rule,
        calendar=calendar,
        strategy=SequenceStrategy([100]),
        strategy_id="restartable",
        portfolio_id="paper",
    )
    assert run_bar(first_runner, make_bar(days[0], 1)).order is None

    restarted = build_paper_runtime(**kwargs)
    restarted.reconcile_and_recover(actor="operator")
    restarted.runtime.start(actor="operator")
    resumed_runner = PaperBarRunner(
        bundle=restarted,
        instrument=instrument,
        trading_rule=rule,
        calendar=calendar,
        strategy=SequenceStrategy([100]),
        strategy_id="restartable",
        portfolio_id="paper",
    )
    result = run_bar(resumed_runner, make_bar(days[0], 2))
    assert result.trade is not None and result.trade.quantity == 100
    assert restarted.ledger.account.position_quantity("510300") == 100


def test_concurrent_bar_callbacks_cannot_double_target_exposure(tmp_path):
    days = (date(2026, 8, 12), date(2026, 8, 13))
    calendar = TradingCalendar(
        tuple(
            TradingDayStatus("SSE", day, TradingStatus.OPEN, date(2026, 1, 1), "fixture")
            for day in days
        )
    )
    bundle = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=tmp_path / "concurrent.db",
    )
    bundle.reconcile_and_recover(actor="operator")
    bundle.runtime.start(actor="operator")
    runner = PaperBarRunner(
        bundle=bundle,
        instrument=Instrument("510300", "SSE", "ETF", 100, Decimal("0.001")),
        trading_rule=TradingRule(
            "domestic-etf",
            "SSE",
            "DOMESTIC_EQUITY_ETF",
            date(2026, 1, 1),
            None,
            100,
            100,
            Decimal("0.001"),
            Decimal("0.1"),
            1,
            "fixture://rule",
            "1",
        ),
        calendar=calendar,
        strategy=SequenceStrategy([100, 100, 100]),
        strategy_id="concurrent",
        portfolio_id="paper",
    )
    run_bar(runner, make_bar(days[0], 1))
    original_submit = bundle.service.submit
    submit_entered = Event()
    release_submit = Event()

    def delayed_submit(*args, **kwargs):
        submit_entered.set()
        assert release_submit.wait(5)
        return original_submit(*args, **kwargs)

    bundle.service.submit = delayed_submit  # type: ignore[method-assign]
    errors: list[Exception] = []

    def process(bar: Bar) -> None:
        try:
            run_bar(runner, bar)
        except Exception as exc:
            errors.append(exc)

    first = Thread(target=process, args=(make_bar(days[0], 2),))
    second = Thread(target=process, args=(make_bar(days[0], 3),))
    first.start()
    assert submit_entered.wait(5)
    second.start()
    release_submit.set()
    first.join(5)
    second.join(5)

    assert not errors
    assert not first.is_alive() and not second.is_alive()
    assert bundle.ledger.account.position_quantity("510300") == 100
    filled = [order for order in bundle.oms.orders if order.filled_quantity == 100]
    assert len(filled) == 1


def test_checkpoint_failure_halts_runtime(tmp_path, monkeypatch):
    day = date(2026, 8, 12)
    bundle = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=tmp_path / "checkpoint-failure.db",
    )
    bundle.reconcile_and_recover(actor="operator")
    bundle.runtime.start(actor="operator")
    runner = PaperBarRunner(
        bundle=bundle,
        instrument=Instrument("510300", "SSE", "ETF", 100, Decimal("0.001")),
        trading_rule=TradingRule(
            "domestic-etf",
            "SSE",
            "DOMESTIC_EQUITY_ETF",
            day,
            None,
            100,
            100,
            Decimal("0.001"),
            Decimal("0.1"),
            1,
            "fixture://rule",
            "1",
        ),
        calendar=TradingCalendar(
            (
                TradingDayStatus("SSE", day, TradingStatus.OPEN, day, "fixture"),
                TradingDayStatus(
                    "SSE", day + timedelta(days=1), TradingStatus.OPEN, day, "fixture"
                ),
            )
        ),
        strategy=SequenceStrategy([100]),
        strategy_id="failure",
        portfolio_id="paper",
    )

    def fail_checkpoint(state_key, payload):
        raise OSError("injected checkpoint failure")

    monkeypatch.setattr(bundle.journal, "save_state", fail_checkpoint)
    try:
        run_bar(runner, make_bar(day, 1))
    except OSError as exc:
        assert str(exc) == "injected checkpoint failure"
    else:
        raise AssertionError("checkpoint failure must propagate")
    assert bundle.risk.state.value == "HALTED"
    assert bundle.metrics.reconciliation_status == "FAILED"


def test_same_identity_runner_instances_share_serialization(tmp_path):
    days = (date(2026, 8, 12), date(2026, 8, 13))
    calendar = TradingCalendar(
        tuple(
            TradingDayStatus("SSE", day, TradingStatus.OPEN, date(2026, 1, 1), "fixture")
            for day in days
        )
    )
    bundle = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=tmp_path / "two-runners.db",
    )
    bundle.reconcile_and_recover(actor="operator")
    bundle.runtime.start(actor="operator")
    instrument = Instrument("510300", "SSE", "ETF", 100, Decimal("0.001"))
    rule = TradingRule(
        "domestic-etf",
        "SSE",
        "DOMESTIC_EQUITY_ETF",
        date(2026, 1, 1),
        None,
        100,
        100,
        Decimal("0.001"),
        Decimal("0.1"),
        1,
        "fixture://rule",
        "1",
    )

    def new_runner(targets):
        return PaperBarRunner(
            bundle=bundle,
            instrument=instrument,
            trading_rule=rule,
            calendar=calendar,
            strategy=SequenceStrategy(targets),
            strategy_id="shared",
            portfolio_id="paper",
        )

    seeder = new_runner([100])
    run_bar(seeder, make_bar(days[0], 1))
    first_runner = new_runner([100])
    second_runner = new_runner([100])
    errors: list[Exception] = []

    def process(runner, bar):
        try:
            run_bar(runner, bar)
        except Exception as exc:
            errors.append(exc)

    first = Thread(target=process, args=(first_runner, make_bar(days[0], 2)))
    second = Thread(target=process, args=(second_runner, make_bar(days[0], 3)))
    first.start()
    second.start()
    first.join(5)
    second.join(5)

    assert not errors
    assert bundle.ledger.account.position_quantity("510300") == 100
    assert len([order for order in bundle.oms.orders if order.filled_quantity == 100]) == 1
