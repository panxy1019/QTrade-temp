"""Verified source-close ETF diagnostic; not adjusted returns or execution admission."""
from __future__ import annotations

import json
import math
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from qtrade.instruments.observed import SCHEMA, instrument_id
from qtrade.market_data.sina_incremental import PRICE_BASIS, completed_klines
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_versions import file_hash
from qtrade.research.qlib_publication import verify_signed
from qtrade.research.signal_baselines import signal_family

LIMITATIONS = ['VENDOR_NATIVE_PRICE_NOT_CERTIFIED_UNADJUSTED_OR_ADJUSTED',
               'DISTRIBUTIONS_AND_SPLITS_NOT_RECONCILED', 'CURRENT_POOL_SURVIVORSHIP_BIAS',
               'CLOSE_ONLY_NOT_AMOUNT_VWAP_OR_EXECUTION_ADMISSION',
               'NOT_COMPARABLE_TO_ADJUSTED_STOCK_MODEL_OR_HISTORICAL_RETURN_SERIES']


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
                   'limitations': LIMITATIONS})


def validate_etf_trend_publication(value: dict[str, Any], *,
                                   calendar: Calendar | None = None) -> dict[str, Any]:
    """Validate the bounded public close diagnostic independently of current freshness."""
    try:
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False,
                          separators=(',', ':')).encode()) > 300_000:
            raise ValueError('ETF_PUBLICATION_TOO_LARGE')
        verify_signed(value)
        if (any(value.get(k) != v or type(value.get(k)) is not type(v)
                for k, v in AUTHORITY.items())
                or value['schema_version'] != 'qtrade.etf-trend-diagnostic.v1'
                or value['strategy'] != 'etf_positive_trend20_native_close_diagnostic'
                or value['price_basis'] != PRICE_BASIS
                or any(value[k] is not False for k in ('return_adjustment_certified',
                    'execution_admitted', 'full_bar_qualification'))
                or value['fills'] != [] or value['forward_return'] is not None):
            raise ValueError('ETF_PUBLICATION_AUTHORITY_INVALID')
        bindings = value['bindings']
        if (set(bindings) != {'master_sha256', 'as_of', 'code_revision', 'calendar_sha256',
                             'contract'} or bindings['as_of'] != value['as_of']
                or bindings['contract'] != 'ETF_CLOSE_DIAGNOSTIC_V1'
                or re.fullmatch('[a-f0-9]{40}', bindings['code_revision']) is None
                or any(re.fullmatch('[a-f0-9]{64}', digest) is None for digest in (
                    bindings['master_sha256'], bindings['calendar_sha256'],
                    value['receipt_sha256']))):
            raise ValueError('ETF_PUBLICATION_BINDINGS_INVALID')
        calendar = calendar or Calendar(Path(__file__).resolve().parents[3]/
                                         'configs/observer_calendar_2026.json')
        as_of = date.fromisoformat(value['as_of'])
        if (str(as_of) != value['as_of'] or as_of > datetime.now(UTC).date()
                or signed(calendar.document)['evidence_sha256'] != bindings['calendar_sha256']
                or value['sessions'] != sessions(calendar, as_of)):
            raise ValueError('ETF_PUBLICATION_SESSIONS_INVALID')
        histories, excluded, scores = value['close_histories'], value['excluded'], value['scores']
        required, qualified = value['required_symbols'], value['qualified_close_symbols']
        if (type(required) is not int or not 1 <= required <= 50 or type(qualified) is not int
                or not 0 <= qualified <= required or not isinstance(histories, dict)
                or qualified != len(histories) or not isinstance(excluded, list)
                or qualified+len(excluded) != required or not isinstance(scores, list)
                or len(scores) > qualified or value['state'] != (
                    'CLOSE_DIAGNOSTIC_READY' if qualified else 'INPUT_UNAVAILABLE')):
            raise ValueError('ETF_PUBLICATION_COVERAGE_INVALID')
        rejected = [row['symbol'] for row in excluded]
        if (len(set(rejected)) != len(rejected) or set(rejected) & set(histories)
                or any(re.fullmatch('[15][0-9]{5}', symbol) is None
                       for symbol in [*histories, *rejected])
                or any(row['reason'] != 'INCOMPLETE_OR_INVALID_21_SESSION_SOURCE'
                       for row in excluded)):
            raise ValueError('ETF_PUBLICATION_SYMBOLS_INVALID')
        expected_scores = []
        for symbol, rows in sorted(histories.items()):
            if (not isinstance(rows, list) or len(rows) != 21
                    or [row['date'] for row in rows] != value['sessions']
                    or any(set(row) != {'date', 'close'} for row in rows)):
                raise ValueError('ETF_PUBLICATION_CLOSE_GRID_INVALID')
            prices = []
            for row in rows:
                if not isinstance(row['close'], str) or not 1 <= len(row['close']) <= 80:
                    raise ValueError('ETF_PUBLICATION_CLOSE_INVALID')
                price = Decimal(row['close'])
                if not price.is_finite() or price <= 0:
                    raise ValueError('ETF_PUBLICATION_CLOSE_INVALID')
                prices.append(price)
            score = float(prices[-1]/prices[0]-1)
            if not math.isfinite(score):
                raise ValueError('ETF_PUBLICATION_SCORE_INVALID')
            if score > 0:
                expected_scores.append({'instrument': symbol, 'score': score})
        if (any(set(row) != {'instrument', 'score'}
                or type(row['score']) not in (int, float) or not math.isfinite(row['score'])
                for row in scores) or scores != expected_scores):
            raise ValueError('ETF_PUBLICATION_SCORE_MISMATCH')
        if (not isinstance(value['limitations'], list) or len(value['limitations']) > 30
                or not set(LIMITATIONS) <= set(value['limitations'])
                or any(not isinstance(reason, str) or len(reason) > 500
                       for reason in value['limitations'])):
            raise ValueError('ETF_PUBLICATION_LIMITATIONS_INVALID')
        return value
    except (KeyError, TypeError, AttributeError, OverflowError, InvalidOperation) as exc:
        raise ValueError('ETF_PUBLICATION_INVALID') from exc


def verify_etf_archive(root: Path, calendar: Calendar) -> dict[str, Any]:
    """Replay frozen raw captures at their own observation clock, including old archives."""
    from scripts.unified_store import UnifiedBatchStore

    diagnostic_path, receipt_path = root/'diagnostic.json', root/'receipt.json'
    if (diagnostic_path.is_symlink() or receipt_path.is_symlink()
            or diagnostic_path.stat().st_size > 300_000
            or receipt_path.stat().st_size > 300_000):
        raise ValueError('ETF_ARCHIVE_INPUT_BOUND_EXCEEDED')
    value = validate_etf_trend_publication(json.loads(diagnostic_path.read_bytes()),
                                          calendar=calendar)
    receipt = json.loads(receipt_path.read_bytes())
    verify_signed(receipt)
    bindings = value['bindings']
    if (root.name != signed(bindings)['evidence_sha256'] or receipt['bindings'] != bindings
            or file_hash(receipt_path) != value['receipt_sha256']
            or any(receipt.get(k) != v or type(receipt.get(k)) is not type(v)
                   for k, v in AUTHORITY.items())
            or type(receipt['raw']['captures']) is not int
            or not 1 <= receipt['raw']['captures'] <= 50):
        raise ValueError('ETF_ARCHIVE_RECEIPT_INVALID')
    captures = UnifiedBatchStore(root/'capture-store').read_captures(receipt['raw'])
    clocks = [datetime.fromisoformat(capture['received_at']) for capture in captures]
    if any(clock.tzinfo is None or clock > datetime.now(UTC) for clock in clocks):
        raise ValueError('ETF_ARCHIVE_OBSERVATION_CLOCK_INVALID')
    symbols = sorted(capture['request']['symbol'][2:] for capture in captures)
    expected = admit(captures, symbols, calendar, date.fromisoformat(value['as_of']), max(clocks))
    expected = signed({**{k: v for k, v in expected.items() if k != 'evidence_sha256'},
                       'bindings': bindings, 'receipt_sha256': file_hash(receipt_path)})
    if expected != value:
        raise ValueError('ETF_ARCHIVE_RAW_REPLAY_MISMATCH')
    return value

