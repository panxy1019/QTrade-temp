from __future__ import annotations

import math
import queue
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from datetime import time as datetime_time
from decimal import Decimal, InvalidOperation
from importlib import import_module
from threading import Lock, Thread
from typing import Protocol, cast
from uuid import uuid4

from qtrade.data import Bar, BarQualityGate, QualityIssue
from qtrade.market_data.models import (
    CanonicalTick,
    MarketSnapshot,
    ProviderCapabilities,
    ProviderConnectionStatus,
    ProviderStatus,
)
from qtrade.market_data.provider import BarHandler, TickHandler

SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")
AKSHARE_PROVIDER = "AKShare"


class AkshareClient(Protocol):
    @property
    def version(self) -> str: ...

    def etf_spot_rows(self) -> Iterable[Mapping[str, object]]: ...

    def etf_daily_rows(
        self, symbol: str, start_date: str, end_date: str
    ) -> Iterable[Mapping[str, object]]: ...

    def etf_daily_sina_rows(self, symbol: str) -> Iterable[Mapping[str, object]]: ...

    def etf_minute_rows(
        self, symbol: str, start_at: str, end_at: str, period: str
    ) -> Iterable[Mapping[str, object]]: ...

    def stock_daily_tx_rows(
        self, symbol: str, start_date: str, end_date: str, adjust: str = ""
    ) -> Iterable[Mapping[str, object]]: ...

    def index_daily_sina_rows(self, symbol: str) -> Iterable[Mapping[str, object]]: ...


class NativeAkshareClient:
    """Thin optional SDK boundary. Importing QTrade never imports AKShare."""

    def __init__(self) -> None:
        try:
            module = import_module("akshare")
        except ImportError as error:
            raise RuntimeError(
                "AKShare is not installed; install the pinned qtrade akshare extra"
            ) from error
        self._module = module

    @property
    def version(self) -> str:
        return str(self._module.__version__)

    def etf_spot_rows(self) -> Iterable[Mapping[str, object]]:
        frame = self._module.fund_etf_spot_em()
        return cast(list[dict[str, object]], frame.to_dict(orient="records"))

    def etf_daily_rows(
        self, symbol: str, start_date: str, end_date: str
    ) -> Iterable[Mapping[str, object]]:
        frame = self._module.fund_etf_hist_em(
            symbol=symbol,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="",
        )
        return cast(list[dict[str, object]], frame.to_dict(orient="records"))

    def etf_daily_sina_rows(self, symbol: str) -> Iterable[Mapping[str, object]]:
        prefix = "sh" if symbol.startswith("5") else "sz"
        frame = self._module.fund_etf_hist_sina(symbol=f"{prefix}{symbol}")
        return cast(list[dict[str, object]], frame.to_dict(orient="records"))

    def etf_minute_rows(
        self, symbol: str, start_at: str, end_at: str, period: str
    ) -> Iterable[Mapping[str, object]]:
        frame = self._module.fund_etf_hist_min_em(
            symbol=symbol,
            start_date=start_at,
            end_date=end_at,
            period=period,
            adjust="",
        )
        return cast(list[dict[str, object]], frame.to_dict(orient="records"))

    def stock_daily_tx_rows(
        self, symbol: str, start_date: str, end_date: str, adjust: str = ""
    ) -> Iterable[Mapping[str, object]]:
        prefix = "sh" if symbol.startswith("6") else "sz"
        frame = self._module.stock_zh_a_hist_tx(
            symbol=f"{prefix}{symbol}",
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
            timeout=15,
        )
        return cast(list[dict[str, object]], frame.to_dict(orient="records"))

    def index_daily_sina_rows(self, symbol: str) -> Iterable[Mapping[str, object]]:
        prefix = "sh" if symbol.startswith(("0", "9")) else "sz"
        frame = self._module.stock_zh_index_daily(symbol=f"{prefix}{symbol}")
        return cast(list[dict[str, object]], frame.to_dict(orient="records"))


@dataclass(frozen=True, slots=True)
class AksharePollingPolicy:
    minimum_request_interval: timedelta = timedelta(seconds=1)
    snapshot_poll_interval: timedelta = timedelta(seconds=30)
    request_timeout: timedelta = timedelta(seconds=20)
    maximum_attempts: int = 3
    initial_backoff: timedelta = timedelta(seconds=1)
    maximum_quote_age: timedelta = timedelta(minutes=2)
    circuit_failure_threshold: int = 3
    circuit_cooldown: timedelta = timedelta(minutes=1)

    def __post_init__(self) -> None:
        durations = (
            self.minimum_request_interval,
            self.snapshot_poll_interval,
            self.request_timeout,
            self.initial_backoff,
            self.maximum_quote_age,
            self.circuit_cooldown,
        )
        if any(value <= timedelta(0) for value in durations):
            raise ValueError("AKShare polling durations must be positive")
        if self.maximum_attempts < 1 or self.circuit_failure_threshold < 1:
            raise ValueError("AKShare retry and circuit limits must be positive")


class AkshareMarketDataProvider:
    """Controlled AKShare adapter for research and REAL_DATA_PAPER only.

    AKShare aggregates public web endpoints and is not an authoritative or
    production-grade live feed. Every successful poll remains simulated-PAPER
    input; this class has no account or order capability.
    """

    def __init__(
        self,
        client: AkshareClient | None = None,
        *,
        policy: AksharePollingPolicy | None = None,
        noncommercial_research_acknowledged: bool = False,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        session_nonce_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        if not noncommercial_research_acknowledged:
            raise ValueError(
                "AKShare requires explicit noncommercial research-use acknowledgement"
            )
        self._client = client or NativeAkshareClient()
        self._policy = policy or AksharePollingPolicy()
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep
        self._session_nonce_factory = session_nonce_factory
        self._connection = ProviderConnectionStatus.DISCONNECTED
        self._history_ready = False
        self._subscriptions: set[str] = set()
        self._tick_handlers: list[TickHandler] = []
        self._bar_handlers: list[BarHandler] = []
        self._last_event_at: datetime | None = None
        self._last_heartbeat_at: datetime | None = None
        self._last_poll_monotonic: float | None = None
        self._last_request_monotonic: float | None = None
        self._last_rows: tuple[Mapping[str, object], ...] = ()
        self._sequences: dict[str, int] = {}
        self._last_snapshot_identity: dict[
            str, tuple[datetime, Decimal, Decimal, Decimal]
        ] = {}
        self._failures = 0
        self._circuit_opened_at: float | None = None
        self._session_nonce: str | None = None
        self._lock = Lock()
        self._poll_lock = Lock()
        self._request_lock = Lock()
        self._timed_out_request_active = False

    @property
    def capabilities(self) -> ProviderCapabilities:
        if self._session_nonce is None:
            raise RuntimeError("AKShare provider has not completed an upstream probe")
        return ProviderCapabilities(
            AKSHARE_PROVIDER,
            "1",
            self._client.version,
            "canonical-tick-v1",
            self._session_nonce,
            historical=True,
            realtime=True,
            ticks=False,
            minute_bars=True,
            etf=True,
            stocks=False,
            indices=False,
            corporate_actions=False,
            snapshot_polling=True,
            production_grade=False,
        )

    def connect(self) -> None:
        with self._lock:
            if self._connection is ProviderConnectionStatus.CONNECTED:
                return
            self._connection = ProviderConnectionStatus.CONNECTING
        try:
            rows = self._request(self._client.etf_spot_rows)
            if not rows:
                raise RuntimeError("AKShare ETF snapshot probe returned no rows")
            self._validate_probe_rows(rows)
        except Exception:
            with self._lock:
                self._connection = ProviderConnectionStatus.DISCONNECTED
                self._session_nonce = None
            raise
        with self._lock:
            self._last_rows = rows
            self._last_poll_monotonic = self._monotonic()
            self._session_nonce = self._session_nonce_factory()
            self._sequences.clear()
            self._last_snapshot_identity.clear()
            self._connection = ProviderConnectionStatus.CONNECTED

    def connect_history(self, *, probe_symbol: str = "510300") -> None:
        if not _is_etf_symbol(probe_symbol):
            raise ValueError("AKShare history probe requires an exchange ETF")
        rows = self._request(lambda: self._client.etf_daily_sina_rows(probe_symbol))
        bars = self._bars_sina(probe_symbol, rows)
        if not bars:
            raise RuntimeError("AKShare Sina ETF history probe returned no canonical bars")
        issues = BarQualityGate().validate(bars, expected_symbol=probe_symbol)
        if issues:
            raise ValueError(
                "AKShare Sina ETF history failed quality gate: "
                + ",".join(sorted({issue.code for issue in issues}))
            )
        self._history_ready = True
        self._session_nonce = self._session_nonce_factory()

    def disconnect(self) -> None:
        with self._lock:
            self._connection = ProviderConnectionStatus.DISCONNECTED
            self._subscriptions.clear()
            self._session_nonce = None
            self._history_ready = False

    def close(self) -> None:
        self.disconnect()

    def subscribe(self, instruments: tuple[str, ...]) -> None:
        self._require_connected()
        _validate_instruments(instruments)
        with self._lock:
            self._subscriptions.update(instruments)

    def unsubscribe(self, instruments: tuple[str, ...]) -> None:
        self._require_connected()
        with self._lock:
            self._subscriptions.difference_update(instruments)

    def get_snapshot(self, instruments: tuple[str, ...]) -> tuple[MarketSnapshot, ...]:
        self._require_connected()
        _validate_instruments(instruments)
        rows = self._poll_rows()
        received_at = self._aware_now()
        requested = set(instruments)
        snapshots = tuple(
            self._snapshot(row, received_at)
            for row in rows
            if str(row.get("代码", "")).strip() in requested
        )
        found = {value.instrument for value in snapshots}
        missing = sorted(requested - found)
        if missing:
            raise ValueError("AKShare snapshot omitted instruments: " + ",".join(missing))
        return snapshots

    def poll_once(self) -> tuple[CanonicalTick, ...]:
        self._require_connected()
        with self._lock:
            instruments = tuple(sorted(self._subscriptions))
        if not instruments:
            raise RuntimeError("AKShare polling requires an explicit subscription universe")
        snapshots = self.get_snapshot(instruments)
        values: list[CanonicalTick] = []
        for snapshot in snapshots:
            volume = self._snapshot_decimal(snapshot, "volume")
            turnover = self._snapshot_decimal(snapshot, "turnover")
            identity = (snapshot.timestamp_exchange, snapshot.price, volume, turnover)
            if self._last_snapshot_identity.get(snapshot.instrument) == identity:
                continue
            self._last_snapshot_identity[snapshot.instrument] = identity
            sequence = self._sequences.get(snapshot.instrument, 0)
            self._sequences[snapshot.instrument] = sequence + 1
            tick = CanonicalTick(
                event_id=(
                    f"akshare:{self.capabilities.session_nonce}:"
                    f"{snapshot.instrument}:{snapshot.timestamp_exchange.isoformat()}:"
                    f"{sequence}"
                ),
                provider=AKSHARE_PROVIDER,
                instrument=snapshot.instrument,
                exchange=snapshot.exchange,
                timestamp_exchange=snapshot.timestamp_exchange,
                timestamp_received=snapshot.timestamp_received,
                sequence=sequence,
                price=snapshot.price,
                volume=volume,
                turnover=turnover,
                bid_price=snapshot.bid_price,
                ask_price=snapshot.ask_price,
                bid_volume=None,
                ask_volume=None,
                data_version=f"akshare-{self._client.version}-etf-spot",
                ingestion_time=snapshot.timestamp_received,
                reconnect_id=self.capabilities.session_nonce,
                ingest_ordinal=None,
                session_nonce=self.capabilities.session_nonce,
            )
            values.append(tick)
            for handler in tuple(self._tick_handlers):
                handler(tick)
        if values:
            self._last_event_at = max(value.timestamp_received for value in values)
        return tuple(values)

    def select_liquid_etf_universe(self, *, size: int = 30) -> tuple[str, ...]:
        if not 20 <= size <= 50:
            raise ValueError("AKShare ETF universe size must be between 20 and 50")
        self._require_connected()
        rows = self._poll_rows()
        excluded = (
            "货币",
            "债",
            "黄金",
            "原油",
            "商品",
            "豆粕",
            "纳指",
            "标普",
            "恒生",
            "港股",
            "日经",
            "德国",
            "法国",
            "沙特",
            "印度",
        )
        ranked: list[tuple[Decimal, str]] = []
        for row in rows:
            symbol = str(row.get("代码", "")).strip()
            name = str(row.get("名称", "")).strip()
            if not _is_etf_symbol(symbol) or any(word in name for word in excluded):
                continue
            amount = _decimal(row.get("成交额"), field="成交额", allow_zero=True)
            price = _decimal(row.get("最新价"), field="最新价")
            if amount > 0 and price > 0:
                ranked.append((amount, symbol))
        result = tuple(symbol for _, symbol in sorted(ranked, reverse=True)[:size])
        if len(result) != size:
            raise RuntimeError("AKShare returned too few eligible liquid ETF candidates")
        return result

    def query_history(
        self,
        instruments: tuple[str, ...],
        *,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> tuple[Bar, ...]:
        _validate_instruments(instruments)
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError("AKShare history query window is invalid")
        if interval not in {"1d", "1m", "5m", "15m", "30m", "60m"}:
            raise ValueError("AKShare ETF history interval is unsupported")
        result: list[Bar] = []
        for symbol in instruments:
            if interval == "1d":
                if (
                    self._history_ready
                    and self._connection is not ProviderConnectionStatus.CONNECTED
                ):
                    def sina_operation(
                        symbol: str = symbol,
                    ) -> Iterable[Mapping[str, object]]:
                        return self._client.etf_daily_sina_rows(symbol)

                    rows = self._request(sina_operation)
                    result.extend(
                        bar
                        for bar in self._bars_sina(symbol, rows)
                        if start <= bar.timestamp < end
                    )
                    continue
                self._require_connected()
                def daily_operation(
                    symbol: str = symbol,
                ) -> Iterable[Mapping[str, object]]:
                    return self._client.etf_daily_rows(
                        symbol, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
                    )

                rows = self._request(daily_operation)
            else:
                self._require_connected()
                def minute_operation(
                    symbol: str = symbol,
                ) -> Iterable[Mapping[str, object]]:
                    return self._client.etf_minute_rows(
                        symbol,
                        start.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S"),
                        end.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S"),
                        interval.removesuffix("m"),
                    )

                rows = self._request(minute_operation)
            result.extend(self._bars(symbol, rows, interval))
        result.sort(key=lambda value: (value.timestamp, value.exchange, value.symbol))
        issues = BarQualityGate().validate(result)
        if issues:
            raise ValueError(
                "AKShare ETF history failed quality gate: "
                + ",".join(sorted({issue.code for issue in issues}))
            )
        return tuple(result)

    def query_stock_history(
        self,
        instruments: tuple[str, ...],
        *,
        start: datetime,
        end: datetime,
        adjustment: str = "",
    ) -> tuple[Bar, ...]:
        if adjustment not in {"", "qfq", "hfq"}:
            raise ValueError("AKShare stock adjustment must be empty, qfq or hfq")
        return self._query_non_etf_daily(
            instruments,
            start=start,
            end=end,
            kind="stock",
            adjustment=adjustment,
        )

    def query_index_history(
        self,
        instruments: tuple[str, ...],
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[Bar, ...]:
        return self._query_non_etf_daily(
            instruments, start=start, end=end, kind="index", adjustment=""
        )

    def _query_non_etf_daily(
        self,
        instruments: tuple[str, ...],
        *,
        start: datetime,
        end: datetime,
        kind: str,
        adjustment: str,
    ) -> tuple[Bar, ...]:
        if not self._history_ready:
            raise RuntimeError("AKShare historical endpoint has not passed its probe")
        if not instruments or any(len(value) != 6 or not value.isdigit() for value in instruments):
            raise ValueError("AKShare historical symbols must be six digits")
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError("AKShare history query window is invalid")
        result: list[Bar] = []
        issues: list[QualityIssue] = []
        for symbol in instruments:
            if kind == "stock":
                def stock_operation(
                    symbol: str = symbol,
                ) -> Iterable[Mapping[str, object]]:
                    return self._client.stock_daily_tx_rows(
                        symbol,
                        start.strftime("%Y%m%d"),
                        end.strftime("%Y%m%d"),
                        adjustment,
                    )

                rows = self._request(stock_operation)
            elif kind == "index":
                def index_operation(
                    symbol: str = symbol,
                ) -> Iterable[Mapping[str, object]]:
                    return self._client.index_daily_sina_rows(symbol)

                rows = self._request(index_operation)
            else:
                raise ValueError("AKShare historical instrument kind is unsupported")
            symbol_bars = [
                bar
                for bar in self._bars_english(symbol, rows, kind=kind)
                if start <= bar.timestamp < end
            ]
            symbol_issues = BarQualityGate().validate(symbol_bars)
            if kind == "stock" and symbol_bars:
                listed_during_window = symbol_bars[0].timestamp >= start + timedelta(days=30)
                symbol_issues = tuple(
                    issue
                    for issue in symbol_issues
                    if not (
                        issue.code == "ABNORMAL_JUMP"
                        and issue.row is not None
                        and (
                            (listed_during_window and issue.row < 5)
                            or (
                                issue.row > 0
                                and (
                                    symbol_bars[issue.row].trading_day
                                    - symbol_bars[issue.row - 1].trading_day
                                ).days
                                > 90
                            )
                        )
                    )
                )
            issues.extend(symbol_issues)
            result.extend(symbol_bars)
        result.sort(key=lambda value: (value.timestamp, value.exchange, value.symbol))
        if issues:
            raise ValueError(
                f"AKShare {kind} history failed quality gate: "
                + ",".join(sorted({issue.code for issue in issues}))
            )
        return tuple(result)

    def get_status(self) -> ProviderStatus:
        detail = ""
        if self._circuit_opened_at is not None:
            detail = "circuit open after repeated upstream failures"
        elif self._connection is ProviderConnectionStatus.DISCONNECTED:
            detail = (
                "history ready; snapshot polling endpoint not connected"
                if self._history_ready
                else "AKShare public endpoint has not been probed"
            )
        return ProviderStatus(
            AKSHARE_PROVIDER,
            self._connection,
            tuple(sorted(self._subscriptions)),
            self._last_event_at,
            self._last_heartbeat_at,
            detail,
        )

    def heartbeat(self) -> datetime:
        self._require_connected()
        now = self._aware_now()
        if self._last_poll_monotonic is None or (
            self._monotonic() - self._last_poll_monotonic
            > self._policy.maximum_quote_age.total_seconds()
        ):
            self._connection = ProviderConnectionStatus.DEGRADED
            raise RuntimeError("AKShare provider heartbeat is stale")
        self._last_heartbeat_at = now
        return now

    def on_tick(self, handler: TickHandler) -> None:
        self._tick_handlers.append(handler)

    def on_bar(self, handler: BarHandler) -> None:
        self._bar_handlers.append(handler)

    def _poll_rows(self) -> tuple[Mapping[str, object], ...]:
        with self._poll_lock:
            now = self._monotonic()
            with self._lock:
                if (
                    self._last_poll_monotonic is not None
                    and now - self._last_poll_monotonic
                    < self._policy.snapshot_poll_interval.total_seconds()
                ):
                    return self._last_rows
            rows = self._request(self._client.etf_spot_rows)
            self._validate_probe_rows(rows)
            with self._lock:
                self._last_rows = rows
                self._last_poll_monotonic = self._monotonic()
            return rows

    def _request(
        self, operation: Callable[[], Iterable[Mapping[str, object]]]
    ) -> tuple[Mapping[str, object], ...]:
        with self._request_lock:
            return self._request_locked(operation)

    def _request_locked(
        self, operation: Callable[[], Iterable[Mapping[str, object]]]
    ) -> tuple[Mapping[str, object], ...]:
        if self._timed_out_request_active:
            raise RuntimeError(
                "AKShare timed-out request is still active; recreate isolated provider"
            )
        now = self._monotonic()
        if self._circuit_opened_at is not None:
            elapsed = now - self._circuit_opened_at
            if elapsed < self._policy.circuit_cooldown.total_seconds():
                raise RuntimeError("AKShare provider circuit is open")
            self._circuit_opened_at = None
            self._failures = 0
        error: Exception | None = None
        for attempt in range(self._policy.maximum_attempts):
            if self._last_request_monotonic is not None:
                delay = self._policy.minimum_request_interval.total_seconds() - (
                    self._monotonic() - self._last_request_monotonic
                )
                if delay > 0:
                    self._sleep(delay)
            output: queue.Queue[
                tuple[tuple[Mapping[str, object], ...] | None, Exception | None]
            ] = queue.Queue(maxsize=1)

            def invoke(
                output: queue.Queue[
                    tuple[tuple[Mapping[str, object], ...] | None, Exception | None]
                ] = output,
            ) -> None:
                try:
                    output.put((tuple(operation()), None))
                except Exception as value:
                    output.put((None, value))

            worker = Thread(target=invoke, name="akshare-request", daemon=True)
            worker.start()
            try:
                rows, failure = output.get(
                    timeout=self._policy.request_timeout.total_seconds()
                )
                self._last_request_monotonic = self._monotonic()
                if failure is not None:
                    raise failure
                assert rows is not None
                self._failures = 0
                return rows
            except queue.Empty:
                error = TimeoutError("AKShare upstream request timed out")
                self._timed_out_request_active = True
                self._connection = ProviderConnectionStatus.DEGRADED
                break
            except Exception as value:
                error = value
            self._last_request_monotonic = self._monotonic()
            if attempt + 1 < self._policy.maximum_attempts:
                self._sleep(self._policy.initial_backoff.total_seconds() * (2**attempt))
        self._failures += 1
        if self._failures >= self._policy.circuit_failure_threshold:
            self._circuit_opened_at = self._monotonic()
            self._connection = ProviderConnectionStatus.DEGRADED
        assert error is not None
        raise RuntimeError("AKShare upstream request failed") from error

    def _validate_probe_rows(
        self, rows: tuple[Mapping[str, object], ...]
    ) -> None:
        received_at = self._aware_now()
        for row in rows:
            try:
                self._snapshot(row, received_at)
                _decimal(row.get("成交量"), field="成交量", allow_zero=True)
                _decimal(row.get("成交额"), field="成交额", allow_zero=True)
            except ValueError:
                continue
            return
        raise ValueError("AKShare ETF probe has no eligible fresh canonical row")

    def _snapshot(self, row: Mapping[str, object], received_at: datetime) -> MarketSnapshot:
        symbol = str(row.get("代码", "")).strip()
        if not _is_etf_symbol(symbol):
            raise ValueError("AKShare ETF snapshot contains an invalid symbol")
        timestamp = _timestamp(row.get("更新时间"))
        if received_at - timestamp > self._policy.maximum_quote_age:
            raise ValueError(f"AKShare snapshot is stale for {symbol}")
        return MarketSnapshot(
            AKSHARE_PROVIDER,
            symbol,
            _exchange(symbol),
            _decimal(row.get("最新价"), field="最新价"),
            _optional_decimal(row.get("买一")),
            _optional_decimal(row.get("卖一")),
            timestamp,
            received_at,
        )

    def _snapshot_decimal(self, snapshot: MarketSnapshot, field: str) -> Decimal:
        row = next(
            value
            for value in self._last_rows
            if str(value.get("代码", "")).strip() == snapshot.instrument
        )
        name = "成交量" if field == "volume" else "成交额"
        return _decimal(row.get(name), field=name, allow_zero=True)

    def _bars(
        self,
        symbol: str,
        rows: tuple[Mapping[str, object], ...],
        interval: str,
    ) -> tuple[Bar, ...]:
        result: list[Bar] = []
        for row in rows:
            if interval == "1d":
                trading_day = _date(row.get("日期"))
                timestamp = datetime.combine(
                    trading_day, datetime_time(15, 0), tzinfo=SHANGHAI
                )
            else:
                timestamp = _timestamp(row.get("时间"))
                trading_day = timestamp.astimezone(SHANGHAI).date()
            result.append(
                Bar(
                    symbol,
                    _exchange(symbol),
                    timestamp,
                    trading_day,
                    _decimal(row.get("开盘"), field="开盘"),
                    _decimal(row.get("最高"), field="最高"),
                    _decimal(row.get("最低"), field="最低"),
                    _decimal(row.get("收盘"), field="收盘"),
                    _decimal(row.get("成交量"), field="成交量", allow_zero=True),
                )
            )
        return tuple(result)

    def _bars_sina(
        self, symbol: str, rows: tuple[Mapping[str, object], ...]
    ) -> tuple[Bar, ...]:
        normalized = tuple(
            {
                "日期": row.get("date"),
                "开盘": row.get("open"),
                "最高": row.get("high"),
                "最低": row.get("low"),
                "收盘": row.get("close"),
                "成交量": row.get("volume"),
            }
            for row in rows
        )
        return self._bars(symbol, normalized, "1d")

    def _bars_english(
        self,
        symbol: str,
        rows: tuple[Mapping[str, object], ...],
        *,
        kind: str,
    ) -> tuple[Bar, ...]:
        exchange = _stock_exchange(symbol) if kind == "stock" else "INDEX"
        result: list[Bar] = []
        for row in rows:
            trading_day = _date(row.get("date"))
            result.append(
                Bar(
                    symbol,
                    exchange,
                    datetime.combine(trading_day, datetime_time(15), SHANGHAI),
                    trading_day,
                    _decimal(row.get("open"), field="open"),
                    _decimal(row.get("high"), field="high"),
                    _decimal(row.get("low"), field="low"),
                    _decimal(row.get("close"), field="close"),
                    _decimal(row.get("volume"), field="volume", allow_zero=True),
                )
            )
        return tuple(result)

    def _require_connected(self) -> None:
        if self._connection is not ProviderConnectionStatus.CONNECTED:
            raise RuntimeError("AKShare provider is not connected and healthy")

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("AKShare provider clock must be timezone-aware")
        return value


def _validate_instruments(instruments: tuple[str, ...]) -> None:
    if not instruments or any(not _is_etf_symbol(value) for value in instruments):
        raise ValueError("AKShare phase-one instruments must be six-digit exchange ETFs")


def _is_etf_symbol(symbol: str) -> bool:
    return len(symbol) == 6 and symbol.isdigit() and symbol.startswith(("1", "5"))


def _exchange(symbol: str) -> str:
    return "SSE" if symbol.startswith("5") else "SZSE"


def _stock_exchange(symbol: str) -> str:
    if symbol.startswith("6"):
        return "SSE"
    if symbol.startswith(("0", "3")):
        return "SZSE"
    if symbol.startswith(("4", "8")):
        return "BSE"
    raise ValueError("AKShare A-share symbol has an unknown venue prefix")


def _decimal(value: object, *, field: str, allow_zero: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"AKShare {field} is not numeric") from error
    if not result.is_finite() or result < 0 or (result == 0 and not allow_zero):
        raise ValueError(f"AKShare {field} is invalid")
    return result


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() and result > 0 else None


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value
    text = str(value).strip()
    try:
        result = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError("AKShare timestamp is invalid") from error
    return result.replace(tzinfo=SHANGHAI) if result.tzinfo is None else result


def _date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError as error:
        raise ValueError("AKShare trading date is invalid") from error
