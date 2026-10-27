"""Sequential publisher cadence with simulated work/time; no IO, sleep or network."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scripts import publish_research_snapshot as publisher


@pytest.fixture
def loop(tmp_path, monkeypatch):
    class Clock:
        now = 0.0
        reads = 0

        def monotonic(self):
            self.reads += 1
            return self.now

    clock = Clock()

    class Stop:
        stopped = False
        wait_limit = 1

        def __init__(self):
            self.waits = []

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, seconds):
            self.waits.append(seconds)
            if not self.stopped:
                clock.now += seconds
            if len(self.waits) >= self.wait_limit:
                self.set()
            return self.stopped

    stop = Stop()
    writes = []
    handlers = {}
    monkeypatch.setenv('LIVE_TRADING_ENABLED', 'false')
    monkeypatch.setattr(publisher.time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(publisher, 'Event', lambda: stop)
    monkeypatch.setattr(publisher.signal, 'signal', lambda number, handler:
                        handlers.setdefault(number, handler))
    monkeypatch.setattr(publisher, 'write_json_atomic', lambda path, value:
                        writes.append((path, value)))
    arguments = ['publisher', '--root', str(tmp_path/'root'),
                 '--experiment-root', str(tmp_path/'experiments'),
                 '--publication', str(tmp_path/'public'), '--interval-seconds', '60',
                 '--unified-market-database', str(tmp_path/'ledger.sqlite')]
    monkeypatch.setattr(sys, 'argv', arguments)
    return clock, stop, writes, handlers, arguments


def test_short_long_and_failed_cycles_use_sequential_start_to_start_cadence(loop, monkeypatch,
                                                                          capsys):
    clock, stop, writes, _, arguments = loop
    stop.wait_limit = 4
    starts = []
    active = False
    work = iter([(10, False), (75, False), (25, True), (5, False)])

    def publish(root, experiments, publication, *, unified_market_database):
        nonlocal active
        assert not active
        active = True
        starts.append(clock.now)
        assert root == Path(arguments[2])
        assert experiments == Path(arguments[4]) and publication == Path(arguments[6])
        assert unified_market_database == Path(arguments[-1])
        duration, failed = next(work)
        clock.now += duration
        active = False
        if failed:
            raise OSError('private fixture error must not be logged')

    monkeypatch.setattr(publisher, 'publish', publish)
    publisher.main()
    assert starts == [0, 60, 135, 195]
    assert stop.waits == [50, 0, 35, 55]
    assert all(right-left >= 60 for left, right in zip(starts, starts[1:], strict=False))
    assert len(writes) == 1
    assert writes[0][0] == Path(arguments[2])/'publication-state.json'
    assert writes[0][1]['outcome'] == 'FAILED' and writes[0][1]['error'] == 'OSError'
    assert capsys.readouterr().out.splitlines() == [
        'PUBLICATION_SUCCEEDED', 'PUBLICATION_SUCCEEDED', 'PUBLICATION_FAILED:OSError',
        'PUBLICATION_SUCCEEDED']


@pytest.mark.parametrize('when', ['before_loop', 'during_publish', 'during_wait'])
def test_stop_prevents_another_cycle_and_signal_handlers_use_event(loop, monkeypatch, when):
    clock, stop, _, handlers, _ = loop
    starts = []
    if when == 'before_loop':
        stop.set()

    def publish(*args, **kwargs):
        starts.append(clock.now)
        clock.now += 5
        if when == 'during_publish':
            handlers[publisher.signal.SIGTERM](publisher.signal.SIGTERM, None)

    monkeypatch.setattr(publisher, 'publish', publish)
    publisher.main()
    assert set(handlers) == {publisher.signal.SIGINT, publisher.signal.SIGTERM}
    assert starts == ([] if when == 'before_loop' else [0])
    assert stop.waits == ([] if when == 'before_loop' else [55])
    assert stop.is_set()


@pytest.mark.parametrize('failed', [False, True])
def test_once_never_waits_or_repeats_even_when_cycle_exceeds_interval(loop, monkeypatch, failed):
    clock, stop, writes, _, arguments = loop
    arguments.append('--once')
    calls = []

    def publish(*args, **kwargs):
        calls.append(clock.now)
        clock.now += 80
        if failed:
            raise ValueError('private fixture failure')

    monkeypatch.setattr(publisher, 'publish', publish)
    if failed:
        with pytest.raises(SystemExit) as caught:
            publisher.main()
        assert caught.value.code == 1
        assert len(writes) == 1 and writes[0][1]['error'] == 'ValueError'
    else:
        publisher.main()
        assert writes == []
    assert calls == [0] and stop.waits == [] and clock.reads == 1


def test_live_guard_still_fails_before_event_clock_or_publication(loop, monkeypatch):
    clock, stop, writes, _, _ = loop
    monkeypatch.setenv('LIVE_TRADING_ENABLED', 'true')
    monkeypatch.setattr(publisher, 'publish', lambda *args, **kwargs:
                        pytest.fail('live guard must run before publishing'))
    with pytest.raises(RuntimeError, match='live trading must remain disabled'):
        publisher.main()
    assert clock.reads == 0 and stop.waits == [] and writes == []
