"""Bounded raw BaoStock minute source audit; never grants catalog admission."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import Any
from uuid import uuid4

from qtrade.instruments.observed import instrument_id
from qtrade.market_data.baostock_five_minute import (
    BAOSTOCK_FIVE_MINUTE_FIELDS,
    fetch_baostock_five_minute_history,
)
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts.atomic_json import write_json_immutable
from scripts.vendor_session import baostock_session


class RecordedResult:
    """Replay preserved API rows through the existing production parser."""

    def __init__(self, raw: dict[str, Any]):
        self.fields = raw["fields"]
        self.error_code = raw["vendor_error_code"]
        self.error_msg = ""
        self.rows = iter(raw["rows"])
        self.row: list[str] = []

    def next(self) -> bool:
        try:
            self.row = next(self.rows)
            return True
        except StopIteration:
            return False

    def get_row_data(self) -> list[str]:
        return self.row


class RecordedApi:
    def __init__(self, raw: dict[str, Any]):
        self.raw = raw

    def query_history_k_data_plus(self, *args: Any, **kwargs: Any) -> RecordedResult:
        return RecordedResult(self.raw)


def probe(
    api: Any, output: Path, *, symbol: str, security_type: str, start: date, end: date,
    now: datetime, revision: str,
) -> dict[str, Any]:
    identity = instrument_id(symbol, security_type)
    if (now.tzinfo is None or not 0 <= (end - start).days < 31 or end >= now.date()
            or re.fullmatch(r"[a-f0-9]{40}", revision) is None):
        raise ValueError("HISTORY_PROBE_BOUND_OR_CLOCK_INVALID")
    request = {"code": ("sh." if identity.startswith("SSE.") else "sz.") + symbol,
               "fields": ",".join(BAOSTOCK_FIVE_MINUTE_FIELDS), "start_date": str(start),
               "end_date": str(end), "frequency": "5", "adjustflag": "3"}
    folder = output / uuid4().hex
    write_json_immutable(folder / "request.json", signed({
        **AUTHORITY, "request": request, "code_revision": revision,
        "started_at": now.isoformat(), "state": "REQUESTED",
    }))
    raw: dict[str, Any] = {
        "request": request, "fields": [], "rows": [], "vendor_error_code": None,
        "body_complete": False, "transport_error": None,
        "capture_basis": "UNMODIFIED_DECODED_BAOSTOCK_API_ROWS_NOT_TCP_WIRE_BYTES",
    }
    started = datetime.now(UTC)
    try:
        result = api.query_history_k_data_plus(request["code"], request["fields"], **{
            k: request[k] for k in ("start_date", "end_date", "frequency", "adjustflag")
        })
        raw["fields"] = list(result.fields)
        while result.error_code == "0" and result.next():
            row = list(result.get_row_data())
            if (len(raw["rows"]) >= 1488 or len(row) > 20 or any(
                    not isinstance(v, str) or len(v) > 100 for v in row)):
                raise ValueError("HISTORY_PROBE_RESPONSE_BOUND_EXCEEDED")
            raw["rows"].append(row)
        raw["vendor_error_code"] = result.error_code
        raw["body_complete"] = result.error_code == "0"
    except Exception as error:
        raw["transport_error"] = type(error).__name__
    raw["received_at"] = datetime.now(UTC).isoformat()
    captured = signed(raw)
    # Even vendor errors, truncated observations and malformed rows survive parsing.
    write_json_immutable(folder / "raw.json", captured)
    receipt: dict[str, Any] = {
        **AUTHORITY, "schema_version": "qtrade.history-source-probe.v1",
        "instrument_id": identity, "code_revision": revision, "request": request,
        "raw_sha256": captured["evidence_sha256"], "attempt_id": folder.name,
        "received_at": raw["received_at"], "downloaded_rows": len(raw["rows"]),
        "elapsed_seconds": (datetime.now(UTC) - started).total_seconds(),
        "admitted_bar_count": 0, "historical_pit_certified": False,
        "ten_year_history_certified": False, "independent_source_reconciled": False,
        "price_basis": "BAOSTOCK_RAW_ADJUSTFLAG_3_VENDOR_CLAIM_UNCERTIFIED",
        "historical_available_at": None, "state": "FAILED", "error_class": None,
    }
    try:
        if not raw["body_complete"] or raw["transport_error"]:
            raise ValueError("HISTORY_PROBE_DOWNLOAD_INCOMPLETE")
        if not raw["rows"]:
            raise ValueError("HISTORY_PROBE_EMPTY_RESPONSE")
        bars = fetch_baostock_five_minute_history(
            RecordedApi(raw), symbol=symbol, start_date=str(start), end_date=str(end),
            adjustflag="3",
        )
        if any(b.trading_day.weekday() >= 5 for b in bars):
            raise ValueError("HISTORY_PROBE_WEEKEND_OBSERVATION")
        amount_checks = 0
        tick = Decimal("0.001") if security_type == "ETF" else Decimal("0.01")
        for row in raw["rows"]:
            opening, high, low, close, volume, amount = map(Decimal, row[3:9])
            if not amount.is_finite() or amount < 0:
                raise ValueError("HISTORY_PROBE_INVALID_AMOUNT")
            if all(v == 0 for v in (opening, high, low, close, volume)):
                if amount != 0:
                    raise ValueError("HISTORY_PROBE_PLACEHOLDER_AMOUNT")
                continue
            if volume == 0:
                if amount != 0:
                    raise ValueError("HISTORY_PROBE_ZERO_VOLUME_AMOUNT")
            elif not low - tick <= amount / volume <= high + tick:
                raise ValueError("HISTORY_PROBE_AMOUNT_OR_UNIT_MISMATCH")
            amount_checks += 1
        sessions = Counter(str(b.trading_day) for b in bars)
        receipt.update(
            state="SOURCE_WINDOW_OBSERVED_NOT_ADMITTED", observed_bars=len(bars),
            observed_start=str(bars[0].trading_day), observed_end=str(bars[-1].trading_day),
            observed_sessions=len(sessions),
            full_grid_sessions=sum(n == 48 for n in sessions.values()),
            partial_sessions={d: n for d, n in sessions.items() if n != 48},
            amount_checked_rows=amount_checks,
            missing_sessions_reason="UNKNOWN_CALENDAR_AND_SECURITY_STATE_NOT_RECONCILED",
        )
    except Exception as error:
        receipt["error_class"] = type(error).__name__
        message = str(error)
        receipt["error_code"] = message if re.fullmatch(r"[A-Z_]{1,100}", message) else None
    sealed = signed(receipt)
    write_json_immutable(folder / "receipt.json", sealed)
    return sealed


def probe_session(api: Any, output: Path, **scope: Any) -> dict[str, Any]:
    """Keep scheduling/login failures durable too; a yielded lease is no download."""
    now = scope['now']
    instrument_id(scope['symbol'], scope['security_type'])
    if (now.tzinfo is None or not 0 <= (scope['end']-scope['start']).days < 31
            or scope['end'] >= now.date()
            or re.fullmatch(r'[a-f0-9]{40}', scope['revision']) is None):
        raise ValueError('HISTORY_PROBE_BOUND_OR_CLOCK_INVALID')
    folder = output / 'sessions' / uuid4().hex
    request = signed({**AUTHORITY, 'symbol': scope['symbol'],
                      'security_type': scope['security_type'], 'start': str(scope['start']),
                      'end': str(scope['end']), 'code_revision': scope['revision'],
                      'started_at': now.isoformat()})
    write_json_immutable(folder / 'request.json', request)
    result: dict[str, Any] = {
        **AUTHORITY, 'schema_version': 'qtrade.history-probe-session.v1',
        'request_sha256': request['evidence_sha256'], 'state': 'LOGIN_FAILED',
        'query_attempted': False, 'admitted_bar_count': 0, 'probe_receipt_sha256': None,
        'error_class': None, 'next_attempt_not_before': None,
    }
    from datetime import timedelta

    try:
        with baostock_session(blocking=False):
            if api.login().error_code != '0':
                raise RuntimeError('VENDOR_LOGIN_FAILED')
            try:
                result['query_attempted'] = True
                receipt = probe(api, output, **scope)
                result.update(state='PROBE_RECORDED',
                              probe_receipt_sha256=receipt['evidence_sha256'],
                              probe_state=receipt['state'], attempt_id=receipt['attempt_id'])
            finally:
                api.logout()
    except BlockingIOError:
        result['state'] = 'YIELDED_TO_SHARED_VENDOR_SESSION'
    except Exception as error:
        result['state'] = 'FAILED_AFTER_QUERY' if result['query_attempted'] else 'LOGIN_FAILED'
        result['error_class'] = type(error).__name__
    if result['state'] in {'LOGIN_FAILED', 'FAILED_AFTER_QUERY'}:
        result['next_attempt_not_before'] = (now + timedelta(minutes=30)).isoformat()
    result['finished_at'] = datetime.now(UTC).isoformat()
    sealed = signed(result)
    write_json_immutable(folder / 'receipt.json', sealed)
    return sealed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--security-type", choices=("STOCK", "ETF"), required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args()
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("LIVE_TRADING_MUST_REMAIN_DISABLED")
    if os.environ.get("QTRADE_BAOSTOCK_FREE_DATA_ACK", "").lower() != "true":
        raise RuntimeError("BAOSTOCK_RESEARCH_ACK_REQUIRED")
    args.output.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(args.output).free < 100 * 1024**3:
        raise RuntimeError("PERSISTENT_STORAGE_RESERVE_100_GIB")
    bs = import_module("baostock")
    socket.setdefaulttimeout(15)
    result = probe_session(bs, args.output, symbol=args.symbol, security_type=args.security_type,
                           start=args.start, end=args.end, now=datetime.now(UTC),
                           revision=args.source_revision)
    if result['state'] == 'YIELDED_TO_SHARED_VENDOR_SESSION':
        print("HISTORY_PROBE_YIELDED_TO_SHARED_VENDOR_SESSION")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
