"""Pure bounded current-market transport validation; no collector or filesystem writer."""
from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, DecimalException, localcontext
from hashlib import sha256
from typing import Any, Literal, cast

from qtrade.instruments.observed import instrument_id
from qtrade.market_data.akshare import SHANGHAI
from qtrade.market_data.sina_incremental import (
    PRICE_BASIS,
    _ConstructedKlineRows,
    completed_kline_rows,
)
from qtrade.market_data.sina_minute import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed

INDEX_LIMIT = 4_000_000
SHARD_LIMIT = 1_000_000
TOTAL_LIMIT = 192_000_000
MAX_TARGETS = 2000
LIMITATIONS = ['CURRENT_OBSERVED_UNIVERSE_NOT_HISTORICAL_PIT',
    'BOUNDED_RECENT_INCREMENTAL_WINDOW_NOT_COMPLETE_HISTORY',
    'VENDOR_NATIVE_PRICE_ADJUSTMENT_NOT_CERTIFIED', 'NO_EXECUTION_OR_PAPER_ADMISSION',
    'MISSING_BARS_NOT_FILLED_OR_INFERRED_AS_SUSPENSION']
BAR_FIELDS = {'symbol', 'exchange', 'frequency', 'timestamp', 'trading_day', 'open', 'high',
    'low', 'close', 'volume', 'amount', 'vwap', 'amount_basis', 'price_basis',
    'historical_available_at'}


def encoded(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode()


def identity(value: Any, length: int = 64) -> None:
    if not isinstance(value, str) or re.fullmatch(f'[a-f0-9]{{{length}}}', value) is None:
        raise ValueError('UNIFIED_MARKET_IDENTITY_INVALID')


def _document_bytes(value: dict[str, Any], fields: set[str], maximum: int) -> tuple[bytes, str]:
    if set(value) != fields | set(AUTHORITY) | {'evidence_sha256'}:
        raise ValueError('UNIFIED_MARKET_DOCUMENT_INVALID')
    digest = value['evidence_sha256']
    raw = encoded(value)
    if (len(raw) > maximum
            or any(type(value.get(k)) is not type(v) or value.get(k) != v
                   for k, v in AUTHORITY.items())
            or signed({k: v for k, v in value.items() if k != 'evidence_sha256'}) != value):
        raise ValueError('UNIFIED_MARKET_DOCUMENT_INVALID')
    return raw, digest


def document(value: dict[str, Any], fields: set[str], maximum: int) -> None:
    _document_bytes(value, fields, maximum)


def sessions(calendar: Calendar, end: date, count: int) -> list[str]:
    days: list[str] = []
    cursor = end
    while cursor >= calendar.start and len(days) < count:
        if calendar.is_open(cursor):
            days.append(str(cursor))
        cursor -= timedelta(days=1)
    if not days:
        raise ValueError('UNIFIED_MARKET_CALENDAR_WINDOW_EMPTY')
    return sorted(days)


def grid(calendar: Calendar, frequency: str, days: list[str], now: datetime) -> list[str]:
    expected = calendar.expected(now, 'daily' if frequency == 'day' else 'minute')
    times = (time(15),) if frequency == 'day' else SINA_FIVE_MINUTE_SESSION_TIMES
    return [stamp.isoformat() for day in days for clock in times
            if (stamp := datetime.combine(date.fromisoformat(day), clock, SHANGHAI)) <= expected]


def _validate_bar(row: dict[str, Any], symbol: str, kind: str, frequency: str,
                 calendar: Calendar, now: datetime, *,
                 _validator: _ConstructedKlineRows | None = None,
                 _exchange: str | None = None) -> None:
    if (set(row) != BAR_FIELDS or row['symbol'] != symbol or row['frequency'] != frequency
            or row['exchange'] != (_exchange if _exchange is not None else
                                   instrument_id(symbol, kind).split('.')[0])
            or row['price_basis'] != PRICE_BASIS or row['historical_available_at'] is not None
            or row['amount_basis'] not in {'VENDOR_CLAIM_UNVERIFIED',
                'SAME_VENDOR_ENDPOINT_MATCH_NOT_INDEPENDENT_RECONCILIATION'}):
        raise ValueError('UNIFIED_MARKET_BAR_CONTEXT_INVALID')
    for key in ('open', 'high', 'low', 'close', 'volume', 'amount', 'vwap'):
        value = row[key]
        if value is None and key in {'amount', 'vwap'}:
            continue
        if (not isinstance(value, str) or not 1 <= len(value) <= 80
                or not Decimal(value).is_finite()):
            raise ValueError('UNIFIED_MARKET_NUMBER_INVALID')
    day = date.fromisoformat(row['trading_day'])
    stamp = datetime.fromisoformat(row['timestamp'])
    if (str(day) != row['trading_day'] or stamp.tzinfo is None
            or stamp.astimezone(SHANGHAI).date() != day
            or frequency == 'day' and stamp != datetime.combine(day, time(15), SHANGHAI)):
        raise ValueError('UNIFIED_MARKET_BAR_CLOCK_INVALID')
    source = {'day': str(day) if frequency == 'day' else
              stamp.astimezone(SHANGHAI).replace(tzinfo=None).isoformat(sep=' '),
              **{k: row[k] for k in ('open', 'high', 'low', 'close', 'volume')}}
    if row['amount'] is not None:
        source['amount'] = row['amount']
    if _validator is None:
        parsed, = completed_kline_rows([source], symbol=symbol, security_type=kind,
            frequency=cast(Literal['day', '5min'], frequency), requested_day=day,
            observed_at=now, calendar=calendar, require_complete_session=False)
    else:
        parsed, = _validator.parse([source], day)
    if parsed.bar.timestamp != stamp or (str(parsed.vwap) if parsed.vwap is not None else None
                                         ) != row['vwap']:
        raise ValueError('UNIFIED_MARKET_BAR_VWAP_OR_COMPLETION_INVALID')


def validate_bar(row: dict[str, Any], symbol: str, kind: str, frequency: str,
                 calendar: Calendar, now: datetime) -> None:
    try:
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            _validate_bar(row, symbol, kind, frequency, calendar, now)
    except (KeyError, TypeError, AttributeError, DecimalException) as error:
        raise ValueError('UNIFIED_MARKET_BAR_INVALID') from error


def _validated_shard_bytes(value: dict[str, Any], calendar: Calendar, now: datetime
                           ) -> tuple[dict[str, Any], bytes]:
    try:
        raw, digest = _document_bytes(value, {'schema_version', 'universe_version', 'symbol',
            'security_type', 'instrument_id', 'frequency', 'price_basis', 'bars', 'source_batches'},
            SHARD_LIMIT)
        identity(value['universe_version'])
        if (value['schema_version'] != 'qtrade.unified-market-shard.v1'
                or value['instrument_id'] != instrument_id(value['symbol'], value['security_type'])
                or value['frequency'] not in {'day', '5min'} or value['price_basis'] != PRICE_BASIS
                or not isinstance(value['bars'], list) or not 1 <= len(value['bars']) <= 2000
                or not isinstance(value['source_batches'], list)
                or not 1 <= len(value['source_batches']) <= 2000):
            raise ValueError('UNIFIED_MARKET_SHARD_SCOPE_INVALID')
        for source in value['source_batches']:
            if set(source) != {'batch_id', 'manifest_sha256', 'raw_sha256', 'normalized_sha256'}:
                raise ValueError('UNIFIED_MARKET_SOURCE_BINDING_INVALID')
            identity(source['batch_id'], 32)
            for key in ('manifest_sha256', 'raw_sha256', 'normalized_sha256'):
                identity(source[key])
        validator = _ConstructedKlineRows(value['symbol'], value['security_type'],
            value['frequency'], now, calendar)
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)) as context:
            for row in value['bars']:
                context.clear_flags()  # Match each standalone bar's fresh Decimal context.
                try:
                    _validate_bar(row, value['symbol'], value['security_type'], value['frequency'],
                                  calendar, now, _validator=validator,
                                  _exchange=value['instrument_id'].split('.')[0])
                except (KeyError, TypeError, AttributeError, DecimalException) as error:
                    raise ValueError('UNIFIED_MARKET_BAR_INVALID') from error
        clocks = [datetime.fromisoformat(row['timestamp']) for row in value['bars']]
        if clocks != sorted(set(clocks)):
            raise ValueError('UNIFIED_MARKET_DUPLICATE_OR_UNORDERED_BAR')
        # The returned encoding was captured before validation. Bind it to the
        # same content after all checks, including mutation during validation.
        if (value['evidence_sha256'] != digest
                or signed({k: v for k, v in value.items() if k != 'evidence_sha256'}) != value):
            raise ValueError('UNIFIED_MARKET_DOCUMENT_INVALID')
        return value, raw
    except (KeyError, TypeError, AttributeError, DecimalException) as error:
        raise ValueError('UNIFIED_MARKET_SHARD_INVALID') from error


def validate_shard(value: dict[str, Any], calendar: Calendar, now: datetime) -> dict[str, Any]:
    return _validated_shard_bytes(value, calendar, now)[0]


def validate_index(value: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    try:
        now = now or datetime.now(UTC)
        document(value, {'schema_version', 'universe_version', 'master_sha256', 'published_at',
            'calendar', 'windows', 'entries', 'total_bytes', 'total_files', 'limitations',
            'implementation_sources'}, INDEX_LIMIT)
        for key in ('universe_version', 'master_sha256'):
            identity(value[key])
        published = datetime.fromisoformat(value['published_at'])
        calendar = Calendar(value['calendar'])
        if (value['schema_version'] != 'qtrade.unified-market-index.v1'
                or published.tzinfo is None or published > now
                or type(value['total_bytes']) is not int
                or not 0 <= value['total_bytes'] <= TOTAL_LIMIT
                or type(value['total_files']) is not int or not 0 <= value['total_files'] <= 8000
                or value['limitations'] != LIMITATIONS
                or set(value['windows']) != {'day', '5min'}
                or not isinstance(value['entries'], list)
                or not 2 <= len(value['entries']) <= MAX_TARGETS*2):
            raise ValueError('UNIFIED_MARKET_INDEX_SCOPE_INVALID')
        if set(value['implementation_sources']) != {'scripts/publish_unified_market.py',
                'src/qtrade/research/unified_market.py'}:
            raise ValueError('UNIFIED_MARKET_IMPLEMENTATION_BINDING_INVALID')
        for digest in value['implementation_sources'].values():
            identity(digest)
        for freq, days in value['windows'].items():
            end = calendar.expected(published, 'daily' if freq == 'day' else 'minute').date()
            if days != sessions(calendar, end, 20 if freq == 'day' else 3):
                raise ValueError('UNIFIED_MARKET_WINDOW_INVALID')
        seen: set[tuple[str, str]] = set()
        files = {}
        metadata: dict[str, tuple[str, str, str]] = {}
        for row in value['entries']:
            if set(row) != {'symbol', 'instrument_id', 'security_type', 'name', 'frequency',
                            'state', 'issues', 'shards'}:
                raise ValueError('UNIFIED_MARKET_ENTRY_FIELDS_INVALID')
            pair = (row['symbol'], row['frequency'])
            if (pair in seen or row['frequency'] not in {'day', '5min'}
                    or row['instrument_id'] != instrument_id(row['symbol'], row['security_type'])
                    or not isinstance(row['name'], str) or len(row['name']) > 100
                    or row['state'] not in {'AVAILABLE', 'NO_DATA', 'QUARANTINED'}
                    or not isinstance(row['issues'], list) or len(row['issues']) > 8
                    or any(re.fullmatch('[A-Z][A-Z0-9_]{0,99}', issue) is None
                           for issue in row['issues'])
                    or not isinstance(row['shards'], list) or len(row['shards']) > 3
                    or bool(row['shards']) != (row['state'] == 'AVAILABLE')):
                raise ValueError('UNIFIED_MARKET_ENTRY_INVALID')
            seen.add(pair)
            current = (row['instrument_id'], row['security_type'], row['name'])
            if row['symbol'] in metadata and metadata[row['symbol']] != current:
                raise ValueError('UNIFIED_MARKET_SYMBOL_METADATA_CONFLICT')
            metadata[row['symbol']] = current
            for ref in row['shards']:
                if set(ref) != {'sha256', 'evidence_sha256', 'bytes', 'start', 'end', 'count'}:
                    raise ValueError('UNIFIED_MARKET_SHARD_REFERENCE_INVALID')
                identity(ref['sha256'])
                identity(ref['evidence_sha256'])
                if (type(ref['bytes']) is not int or not 0 < ref['bytes'] <= SHARD_LIMIT
                        or type(ref['count']) is not int or not 1 <= ref['count'] <= 2000):
                    raise ValueError('UNIFIED_MARKET_SHARD_REFERENCE_INVALID')
                start, end = map(datetime.fromisoformat, (ref['start'], ref['end']))
                if (start.tzinfo is None or end.tzinfo is None or not start <= end <= published
                        or str(start.astimezone(SHANGHAI).date()) not in value['windows'][row[
                            'frequency']] or str(end.astimezone(SHANGHAI).date()) not in value[
                                'windows'][row['frequency']]):
                    raise ValueError('UNIFIED_MARKET_REFERENCE_CLOCK_INVALID')
                files[ref['sha256']] = ref['bytes']
        symbols = {key[0] for key in seen}
        if (seen != {(symbol, freq) for symbol in symbols for freq in ('day', '5min')}
                or len(files) != value['total_files']
                or sum(files.values()) != value['total_bytes']):
            raise ValueError('UNIFIED_MARKET_INDEX_COUNTS_INVALID')
        return value
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('UNIFIED_MARKET_INDEX_INVALID') from error


def verify_shard_bytes(raw: bytes, ref: dict[str, Any], index: dict[str, Any],
                        entry: dict[str, Any], now: datetime) -> dict[str, Any]:
    if len(raw) != ref['bytes'] or sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('UNIFIED_MARKET_SHARD_CHECKSUM_INVALID')
    value = validate_shard(json.loads(raw), Calendar(index['calendar']), now)
    if (value['evidence_sha256'] != ref['evidence_sha256']
            or value['universe_version'] != index['universe_version']
            or any(value[key] != entry[key] for key in ('symbol', 'frequency', 'security_type',
                                                       'instrument_id'))
            or len(value['bars']) != ref['count'] or value['bars'][0]['timestamp'] != ref['start']
            or value['bars'][-1]['timestamp'] != ref['end']):
        raise ValueError('UNIFIED_MARKET_SHARD_INDEX_MISMATCH')
    return value
