"""Verified source-close ETF diagnostic; not adjusted returns or execution admission."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from qtrade.instruments.observed import SCHEMA, instrument_id
from qtrade.market_data.sina_incremental import PRICE_BASIS, completed_klines
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.qlib_publication import verify_signed
from qtrade.research.signal_baselines import signal_family


def etf_targets(master: dict[str, Any], now: datetime) -> list[str]:
    verify_signed(master)
    if master.get('schema_version') != SCHEMA or any(
            master.get(k) != v for k, v in AUTHORITY.items()):
        raise ValueError('ETF_MASTER_AUTHORITY_INVALID')
    if datetime.fromisoformat(master['observed_at']) > now:
        raise ValueError('ETF_MASTER_FROM_FUTURE')
    rows = [r for r in master['securities'] if r['in_target'] and r['security_type'] == 'ETF']
    if not 1 <= len(rows) <= 50 or len({r['symbol'] for r in rows}) != len(rows):
        raise ValueError('ETF_TARGET_COUNT_INVALID')
    for row in rows:
        if (row['instrument_id'] != instrument_id(row['symbol'], 'ETF')
                or row['status'] != 'ACTIVE_OBSERVED' or row['issues']):
            raise ValueError('ETF_OBSERVED_IDENTITY_INVALID')
    return sorted(r['symbol'] for r in rows)


def sessions(calendar: Calendar, as_of: date) -> list[str]:
    if not calendar.is_open(as_of):
        raise ValueError('ETF_AS_OF_NOT_SESSION')
    result = [str(as_of)]
    cursor = as_of
    while len(result) < 21:
        cursor = calendar.previous(cursor-timedelta(days=1))
        result.append(str(cursor))
    return list(reversed(result))


def admit(captures: list[dict[str, Any]], symbols: list[str], calendar: Calendar,
          as_of: date, now: datetime) -> dict[str, Any]:
    if as_of != calendar.expected(now, 'daily').date():
        raise ValueError('ETF_CURRENT_COMPLETED_SESSION_REQUIRED')
    window = sessions(calendar, as_of)
    by_symbol = {}
    for capture in captures:
        request = capture['request']
        code = request.get('symbol', '')
        symbol = code[2:]
        if (capture['source_id'] != 'sina_klines' or symbol not in symbols
                or code != ('sh' if symbol.startswith('5') else 'sz')+symbol
                or request != {'symbol': code, 'scale': '240', 'ma': 'no', 'datalen': '63'}
                or symbol in by_symbol):
            raise ValueError('ETF_CAPTURE_SCOPE_MISMATCH')
        by_symbol[symbol] = capture
    if set(by_symbol) != set(symbols):
        raise ValueError('ETF_CAPTURE_TARGETS_MISMATCH')
    histories: dict[str, list[dict[str, Any]]] = {}
    excluded = []
    for symbol, capture in sorted(by_symbol.items()):
        try:
            received = datetime.fromisoformat(capture['received_at'])
            if (received.tzinfo is None or received > now or capture['status_code'] != 200
                    or capture['transport_error'] is not None or not capture['body_complete']):
                raise ValueError('ETF_SOURCE_RESPONSE_INCOMPLETE')
            rows = []
            for day in window:
                parsed = completed_klines(capture['body'], symbol=symbol, security_type='ETF',
                    frequency='day', requested_day=date.fromisoformat(day), observed_at=received,
                    calendar=calendar)
                rows.append({'date': day, 'close': str(parsed[0].bar.close)})
            histories[symbol] = rows
        except (ValueError, KeyError, TypeError):
            excluded.append({'symbol': symbol, 'reason': 'INCOMPLETE_OR_INVALID_21_SESSION_SOURCE'})
    family = (signal_family(histories, window, as_of=str(as_of), security_type='ETF')
              if histories else None)
    scores = family['signals']['etf_positive_trend20'] if family else []
    return signed({**AUTHORITY, 'schema_version': 'qtrade.etf-trend-diagnostic.v1',
                   'strategy': 'etf_positive_trend20_native_close_diagnostic', 'as_of': str(as_of),
                   'state': 'CLOSE_DIAGNOSTIC_READY' if histories else 'INPUT_UNAVAILABLE',
                   'price_basis': PRICE_BASIS, 'return_adjustment_certified': False,
                   'qualified_close_symbols': len(histories), 'required_symbols': len(symbols),
                   'sessions': window, 'scores': scores, 'close_histories': histories,
                   'excluded': excluded, 'execution_admitted': False, 'fills': [],
                   'forward_return': None, 'full_bar_qualification': False,
                   'limitations': ['VENDOR_NATIVE_PRICE_NOT_CERTIFIED_UNADJUSTED_OR_ADJUSTED',
                       'DISTRIBUTIONS_AND_SPLITS_NOT_RECONCILED', 'CURRENT_POOL_SURVIVORSHIP_BIAS',
                       'CLOSE_ONLY_NOT_AMOUNT_VWAP_OR_EXECUTION_ADMISSION',
                       'NOT_COMPARABLE_TO_ADJUSTED_STOCK_MODEL_OR_HISTORICAL_RETURN_SERIES']})

