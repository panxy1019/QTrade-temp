from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from importlib import import_module
from threading import Lock
from typing import Protocol, cast
from uuid import uuid4

from qtrade.data import Bar
from qtrade.market_data.models import CanonicalTick, MarketSnapshot, ProviderCapabilities
from qtrade.market_data.provider import BarHandler, TickHandler

RQDATA_PROVIDER = "RQData"
RQDATA_PROTOCOL_VERSION = "1"
RQDATA_SCHEMA_VERSION = "canonical-tick-v1"
SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")


class RqDataLiveClient(Protocol):
    def subscribe(self, channels: str | list[str]) -> object: ...

    def unsubscribe(self, channels: str | list[str]) -> object: ...

    def listen(self, handler: Callable[[object], None] | None = None) -> object: ...

    def close(self) -> None: ...


class ListenerHandle(Protocol):
    def is_alive(self) -> bool: ...


class RqDataSdk(Protocol):
    __version__: str
    LiveMarketDataClient: Callable[[], RqDataLiveClient]

    def init(self, username: str, password: str) -> object: ...

    def reset(self, flush_cache: bool = False) -> object: ...

    def current_snapshot(self, order_book_ids: list[str], market: str = "cn") -> object: ...

    def get_price(
        self,
        order_book_ids: str,
        start_date: datetime,
        end_date: datetime,
        frequency: str,
        fields: list[str],
        adjust_type: str,
        expect_df: bool,
        market: str,
    ) -> object: ...


class DataFrameLike(Protocol):
    def reset_index(self) -> DataFrameLike: ...

    def to_dict(self, *, orient: str) -> list[dict[str, object]]: ...


def _load_rqdata_sdk() -> RqDataSdk:
    try:
        return cast(RqDataSdk, import_module("rqdatac"))
    except ImportError as error:
        raise RuntimeError(
            "RQData SDK is not installed; install the pinned qtrade rqdata extra"
        ) from error


class NativeRqDataBridgeClient:
    """Licensed RQData SDK bridge with market-data-only capabilities.

    Credentials are read only from the bridge process environment. The SDK is
    imported lazily, and connection is not claimed until both the RQData login
    and websocket realtime-token exchange have succeeded.
    """

    def __init__(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        session_nonce_factory: Callable[[], str] = lambda: uuid4().hex,
        sdk_loader: Callable[[], RqDataSdk] = _load_rqdata_sdk,
    ) -> None:
        values = os.environ if environment is None else environment
        self._username = values.get("QTRADE_RQDATA_USERNAME", "").strip()
        self._token = values.get("QTRADE_RQDATA_TOKEN", "").strip()
        self._clock = clock
        self._session_nonce_factory = session_nonce_factory
        self._sdk_loader = sdk_loader
        self._sdk: RqDataSdk | None = None
        self._live: RqDataLiveClient | None = None
        self._session_nonce: str | None = None
        self._sdk_version: str | None = None
        self._tick_handler: TickHandler | None = None
        self._bar_handler: BarHandler | None = None
        self._listener: ListenerHandle | None = None
        self._listener_error: RuntimeError | None = None
        self._rq_to_instrument: dict[str, str] = {}
        self._sequences: dict[str, int] = {}
        self._ingest_ordinal = 0
        self._lock = Lock()

    def connect(self) -> None:
        with self._lock:
            if self._live is not None:
                return
        missing = [
            name
            for name, value in (
                ("QTRADE_RQDATA_USERNAME", self._username),
                ("QTRADE_RQDATA_TOKEN", self._token),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "RQData bridge is not configured; missing " + ",".join(missing)
            )
        sdk = self._sdk_loader()
        try:
            sdk.init(username=self._username, password=self._token)
            live = sdk.LiveMarketDataClient()
        except Exception:
            with suppress(Exception):
                sdk.reset()
            raise RuntimeError(
                "RQData authorization or realtime entitlement probe failed"
            ) from None
        nonce = self._session_nonce_factory().strip()
        version = str(sdk.__version__).strip()
        if not nonce or not version:
            live.close()
            sdk.reset()
            raise ValueError("RQData session and SDK identity are required")
        with self._lock:
            self._sdk = sdk
            self._live = live
            self._session_nonce = nonce
            self._sdk_version = version
            self._listener_error = None
            self._sequences.clear()
            self._ingest_ordinal = 0

    def handshake(self) -> ProviderCapabilities:
        self._require_healthy_connected()
        assert self._session_nonce is not None
        assert self._sdk_version is not None
        return ProviderCapabilities(
            provider=RQDATA_PROVIDER,
            protocol_version=RQDATA_PROTOCOL_VERSION,
            sdk_version=self._sdk_version,
            schema_version=RQDATA_SCHEMA_VERSION,
            session_nonce=self._session_nonce,
            historical=True,
            realtime=True,
            ticks=True,
            minute_bars=True,
            etf=True,
            stocks=True,
            indices=True,
            corporate_actions=False,
            external_order_transmission=False,
            account_access=False,
            snapshot_polling=True,
            production_grade=False,
        )

    def disconnect(self) -> None:
        with self._lock:
            live = self._live
            sdk = self._sdk
            self._live = None
            self._sdk = None
            self._session_nonce = None
            self._sdk_version = None
            self._listener = None
            self._listener_error = None
            self._rq_to_instrument.clear()
            self._sequences.clear()
            self._ingest_ordinal = 0
        if live is not None:
            with suppress(Exception):
                live.close()
        if sdk is not None:
            with suppress(Exception):
                sdk.reset()

    def subscribe(self, instruments: tuple[str, ...]) -> None:
        live = self._require_healthy_connected()
        pairs = _instrument_pairs(instruments)
        channels = [
            channel
            for rq_id, _ in pairs
            for channel in (f"tick_{rq_id}", f"bar_{rq_id}")
        ]
        with self._lock:
            previous = dict(self._rq_to_instrument)
            for rq_id, instrument in pairs:
                existing = self._rq_to_instrument.get(rq_id)
                if existing is not None and existing != instrument:
                    raise ValueError("RQData subscription aliases the same instrument twice")
                self._rq_to_instrument[rq_id] = instrument
        try:
            live.subscribe(channels)
        except Exception:
            with self._lock:
                self._rq_to_instrument = previous
            raise RuntimeError("RQData realtime subscription failed") from None
        with self._lock:
            start_listener = self._listener is None
        if start_listener:
            try:
                raw_listener = live.listen(handler=self._on_message)
                if not hasattr(raw_listener, "is_alive"):
                    raise TypeError("RQData listener did not return a thread handle")
                listener = cast(ListenerHandle, raw_listener)
            except Exception:
                with self._lock:
                    self._listener_error = RuntimeError(
                        "RQData realtime listener failed to start"
                    )
                with suppress(Exception):
                    live.close()
                raise RuntimeError("RQData realtime listener failed to start") from None
            with self._lock:
                self._listener = listener

    def unsubscribe(self, instruments: tuple[str, ...]) -> None:
        live = self._require_healthy_connected()
        pairs = _instrument_pairs(instruments)
        channels = [
            channel
            for rq_id, _ in pairs
            for channel in (f"tick_{rq_id}", f"bar_{rq_id}")
        ]
        try:
            live.unsubscribe(channels)
        except Exception:
            raise RuntimeError("RQData realtime unsubscribe failed") from None
        with self._lock:
            for rq_id, _ in pairs:
                self._rq_to_instrument.pop(rq_id, None)
                self._sequences.pop(rq_id, None)

    def snapshots(self, instruments: tuple[str, ...]) -> Iterable[MarketSnapshot]:
        self._require_healthy_connected()
        sdk = self._require_sdk()
        pairs = _instrument_pairs(instruments)
        received_at = self._aware_now()
        try:
            source = sdk.current_snapshot([rq_id for rq_id, _ in pairs], market="cn")
        except Exception:
            raise RuntimeError("RQData snapshot request failed") from None
        rows = _object_rows(source)
        by_rq_id = {_text(_read(row, "order_book_id")): row for row in rows}
        result: list[MarketSnapshot] = []
        for rq_id, instrument in pairs:
            row = by_rq_id.get(rq_id)
            if row is None:
                raise ValueError(f"RQData snapshot omitted instrument {instrument}")
            result.append(_snapshot(row, instrument, rq_id, received_at))
        return tuple(result)

    def history(
        self,
        instruments: tuple[str, ...],
        start: datetime,
        end: datetime,
        interval: str,
    ) -> Iterable[Bar]:
        self._require_healthy_connected()
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError("RQData history query window is invalid")
        if interval not in {"1d", "1m"}:
            raise ValueError("RQData bridge supports only raw 1d and 1m history")
        sdk = self._require_sdk()
        result: list[Bar] = []
        for rq_id, instrument in _instrument_pairs(instruments):
            try:
                frame = sdk.get_price(
                    rq_id,
                    start_date=_sdk_datetime(start),
                    end_date=_sdk_datetime(end),
                    frequency=interval,
                    fields=["open", "high", "low", "close", "volume"],
                    adjust_type="none",
                    expect_df=True,
                    market="cn",
                )
            except Exception:
                raise RuntimeError(
                    f"RQData history request failed for {instrument}"
                ) from None
            for row in _frame_rows(frame):
                bar = _bar(row, instrument, rq_id, interval)
                if start <= bar.timestamp < end:
                    result.append(bar)
        result.sort(key=lambda value: (value.timestamp, value.exchange, value.symbol))
        return tuple(result)

    def heartbeat(self) -> datetime:
        self._require_healthy_connected()
        # A bounded snapshot call is an upstream round trip, unlike checking a
        # local websocket object or returning the local clock alone.
        self.snapshots(("510300",))
        return self._aware_now()

    def set_tick_handler(self, handler: TickHandler) -> None:
        self._tick_handler = handler

    def set_bar_handler(self, handler: BarHandler) -> None:
        self._bar_handler = handler

    def _on_message(self, value: object) -> None:
        try:
            if not isinstance(value, Mapping) or value.get("action") != "feed":
                return
            row = cast(Mapping[str, object], value)
            channel = _text(row.get("channel"))
            rq_id = _text(row.get("order_book_id"))
            with self._lock:
                instrument = self._rq_to_instrument.get(rq_id)
                tick_handler = self._tick_handler
                bar_handler = self._bar_handler
            if instrument is None:
                raise ValueError("RQData feed contains an unsubscribed instrument")
            if channel.startswith("tick_"):
                tick = self._tick(row, instrument, rq_id)
                if tick_handler is not None:
                    tick_handler(tick)
            elif channel.startswith("bar_"):
                bar = _bar(row, instrument, rq_id, "1m")
                if bar_handler is not None:
                    bar_handler(bar)
        except Exception:
            with self._lock:
                self._listener_error = RuntimeError(
                    "RQData live callback failed closed; disconnect and reconcile"
                )
                live = self._live
            if live is not None:
                with suppress(Exception):
                    live.close()

    def _tick(
        self, row: Mapping[str, object], instrument: str, rq_id: str
    ) -> CanonicalTick:
        received_at = self._aware_now()
        exchange_at = _timestamp(row.get("datetime"))
        with self._lock:
            sequence = self._sequences.get(rq_id, 0)
            self._sequences[rq_id] = sequence + 1
            ingest_ordinal = self._ingest_ordinal
            self._ingest_ordinal += 1
            nonce = self._session_nonce
            version = self._sdk_version
        assert nonce is not None
        assert version is not None
        return CanonicalTick(
            event_id=(
                f"rqdata:{nonce}:{rq_id}:{exchange_at.isoformat()}:{sequence}"
            ),
            provider=RQDATA_PROVIDER,
            instrument=instrument,
            exchange=_exchange(rq_id),
            timestamp_exchange=exchange_at,
            timestamp_received=received_at,
            sequence=sequence,
            price=_decimal(row.get("last"), field="last"),
            volume=_decimal(row.get("volume"), field="volume", allow_zero=True),
            turnover=_decimal(
                row.get("total_turnover"), field="total_turnover", allow_zero=True
            ),
            bid_price=_first_decimal(row.get("bid")),
            ask_price=_first_decimal(row.get("ask")),
            bid_volume=_first_decimal(row.get("bid_vol"), allow_zero=True),
            ask_volume=_first_decimal(row.get("ask_vol"), allow_zero=True),
            data_version=f"rqdata-live-{version}-v1",
            ingestion_time=received_at,
            reconnect_id="sdk-managed",
            ingest_ordinal=ingest_ordinal,
            session_nonce=nonce,
        )

    def _require_healthy_connected(self) -> RqDataLiveClient:
        with self._lock:
            live = self._live
            error = self._listener_error
            listener = self._listener
        if live is None:
            raise RuntimeError("RQData bridge is not connected")
        if error is not None:
            raise error
        if listener is not None and not listener.is_alive():
            raise RuntimeError(
                "RQData realtime listener stopped; disconnect and reconcile"
            )
        return live

    def _require_sdk(self) -> RqDataSdk:
        with self._lock:
            sdk = self._sdk
        if sdk is None:
            raise RuntimeError("RQData bridge is not connected")
        return sdk

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("RQData bridge clock must be timezone-aware")
        return value


def _instrument_pairs(instruments: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    if not instruments or any(not item.strip() for item in instruments):
        raise ValueError("RQData instruments are required")
    pairs = tuple(
        (_rq_order_book_id(item), item.strip().upper()) for item in instruments
    )
    if len({rq_id for rq_id, _ in pairs}) != len(pairs):
        raise ValueError("RQData instruments contain duplicate aliases")
    return pairs


def _rq_order_book_id(instrument: str) -> str:
    value = instrument.strip().upper()
    suffixes = {
        ".XSHG": ".XSHG",
        ".SSE": ".XSHG",
        ".SH": ".XSHG",
        ".XSHE": ".XSHE",
        ".SZSE": ".XSHE",
        ".SZ": ".XSHE",
    }
    for suffix, normalized in suffixes.items():
        if value.endswith(suffix):
            code = value[: -len(suffix)]
            if len(code) == 6 and code.isdigit():
                return code + normalized
            break
    if len(value) != 6 or not value.isdigit():
        raise ValueError("RQData instruments must be six-digit exchange securities")
    if value.startswith(("5", "6", "9")):
        return value + ".XSHG"
    if value.startswith(("0", "1", "2", "3")):
        return value + ".XSHE"
    raise ValueError("RQData plain instrument has an unsupported exchange prefix")


def _exchange(rq_id: str) -> str:
    if rq_id.endswith(".XSHG"):
        return "SSE"
    if rq_id.endswith(".XSHE"):
        return "SZSE"
    raise ValueError("RQData exchange suffix is unsupported")


def _snapshot(
    row: object, instrument: str, rq_id: str, received_at: datetime
) -> MarketSnapshot:
    return MarketSnapshot(
        provider=RQDATA_PROVIDER,
        instrument=instrument,
        exchange=_exchange(rq_id),
        price=_decimal(_read(row, "last"), field="last"),
        bid_price=_first_decimal(_read(row, "bids", "bid")),
        ask_price=_first_decimal(_read(row, "asks", "ask")),
        timestamp_exchange=_timestamp(_read(row, "datetime")),
        timestamp_received=received_at,
    )


def _bar(
    row: Mapping[str, object], instrument: str, rq_id: str, interval: str
) -> Bar:
    timestamp = _timestamp(row.get("datetime", row.get("date")))
    if interval == "1d":
        timestamp = datetime.combine(timestamp.astimezone(SHANGHAI).date(), time(15), SHANGHAI)
    trading_day_value = row.get("trading_date")
    trading_day = (
        timestamp.astimezone(SHANGHAI).date()
        if trading_day_value is None
        else _date(trading_day_value)
    )
    open_price = _decimal(row.get("open"), field="open")
    high = _decimal(row.get("high"), field="high")
    low = _decimal(row.get("low"), field="low")
    close = _decimal(row.get("close"), field="close")
    if high < max(open_price, close) or low > min(open_price, close) or low > high:
        raise ValueError("RQData bar OHLC relationship is invalid")
    return Bar(
        symbol=instrument,
        exchange=_exchange(rq_id),
        timestamp=timestamp,
        trading_day=trading_day,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=_decimal(row.get("volume"), field="volume", allow_zero=True),
    )


def _frame_rows(value: object) -> tuple[Mapping[str, object], ...]:
    if isinstance(value, (list, tuple)):
        if any(not isinstance(row, Mapping) for row in value):
            raise ValueError("RQData history rows are malformed")
        return tuple(cast(Mapping[str, object], row) for row in value)
    try:
        rows = cast(DataFrameLike, value).reset_index().to_dict(orient="records")
    except Exception:
        raise ValueError("RQData history result is not a tabular frame") from None
    if any(not isinstance(row, Mapping) for row in rows):
        raise ValueError("RQData history rows are malformed")
    return tuple(cast(Mapping[str, object], row) for row in rows)


def _object_rows(value: object) -> tuple[object, ...]:
    if isinstance(value, Mapping):
        return (value,)
    if isinstance(value, (str, bytes)):
        raise ValueError("RQData snapshot result is malformed")
    try:
        return tuple(cast(Iterable[object], value))
    except TypeError:
        return (value,)


def _read(value: object, *names: str) -> object:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    else:
        for name in names:
            if hasattr(value, name):
                return getattr(value, name)
    return None


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value
    if hasattr(value, "to_pydatetime"):
        converted = value.to_pydatetime()
        if isinstance(converted, datetime):
            return converted.replace(tzinfo=SHANGHAI) if converted.tzinfo is None else converted
    digits = _text(value).replace("-", "").replace(":", "").replace(" ", "")
    if digits.isdigit() and len(digits) in {8, 14, 17, 20}:
        if len(digits) == 8:
            parsed = datetime.strptime(digits, "%Y%m%d")
        else:
            base = datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
            fraction = digits[14:]
            parsed = base.replace(microsecond=int(fraction.ljust(6, "0")))
        return parsed.replace(tzinfo=SHANGHAI)
    try:
        parsed = datetime.fromisoformat(_text(value))
    except ValueError:
        raise ValueError("RQData timestamp is invalid") from None
    return parsed.replace(tzinfo=SHANGHAI) if parsed.tzinfo is None else parsed


def _date(value: object) -> date:
    if isinstance(value, datetime):
        return value.astimezone(SHANGHAI).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    try:
        return date.fromisoformat(text) if "-" in text else datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        raise ValueError("RQData trading date is invalid") from None


def _decimal(value: object, *, field: str, allow_zero: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"RQData {field} is not numeric") from None
    if not result.is_finite() or result < 0 or (not allow_zero and result == 0):
        raise ValueError(f"RQData {field} is invalid")
    return result


def _first_decimal(value: object, *, allow_zero: bool = False) -> Decimal | None:
    if value is None or isinstance(value, (str, bytes)):
        return None
    try:
        first = next(iter(cast(Iterable[object], value)))
    except (StopIteration, TypeError):
        return None
    try:
        result = _decimal(first, field="level1", allow_zero=allow_zero)
    except ValueError:
        return None
    return result


def _sdk_datetime(value: datetime) -> datetime:
    return value.astimezone(SHANGHAI).replace(tzinfo=None)


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
