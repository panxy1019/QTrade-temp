"""Bounded per-security recent bars from immutable public shards, never the private lake."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response

from qtrade.api.unified_source import (
    current_revision,
    download,
    download_shard,
    validate_revision,
)
from qtrade.market_data.sina_incremental import PRICE_BASIS
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.unified_market import (
    INDEX_LIMIT,
    grid,
    validate_index,
    verify_shard_bytes,
)

router = APIRouter(prefix='/unified')


@lru_cache(maxsize=1)
def _index(revision: str) -> dict[str, Any]:
    raw, actual = download('unified-market.json', INDEX_LIMIT, publication_revision=revision)
    if actual != revision:
        raise ValueError('UNIFIED_MARKET_SOURCE_REVISION_MISMATCH')
    return validate_index(json.loads(raw))


@lru_cache(maxsize=128)
def _object(digest: str, revision: str) -> bytes:
    return download_shard(digest, revision)


@router.get('/bars')
def bars(response: Response, symbol: str = Query(pattern=r'^\d{6}$'),
         frequency: Literal['day', '5min'] = 'day', start: date | None = None,
         end: date | None = None, version: str | None = Query(None, pattern=r'^[a-f0-9]{64}$'),
         limit: int = Query(500, ge=1, le=2000),
         publication_revision: str | None = Query(None, pattern=r'^[a-f0-9]{40}$')
         ) -> dict[str, Any]:
    response.headers['Cache-Control'] = 'no-store'
    if start is not None and end is not None and start > end:
        raise HTTPException(422, 'UNIFIED_MARKET_RANGE_INVALID')
    now = datetime.now(UTC)
    try:
        revision = (current_revision() if publication_revision is None
                    else validate_revision(publication_revision))
        index = validate_index(_index(revision), now)
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(503, 'UNIFIED_MARKET_PUBLICATION_UNAVAILABLE') from None
    identity = index['evidence_sha256']
    if version is not None and version != identity:
        raise HTTPException(409, 'UNIFIED_MARKET_VERSION_CHANGED_REFRESH_REQUIRED')
    entry = next((row for row in index['entries']
                  if row['symbol'] == symbol and row['frequency'] == frequency), None)
    if entry is None:
        raise HTTPException(404, 'UNIFIED_MARKET_TARGET_UNAVAILABLE')
    window = index['windows'][frequency]
    lower, upper = start or date.fromisoformat(window[0]), end or date.fromisoformat(window[-1])
    if lower > upper:
        raise HTTPException(422, 'UNIFIED_MARKET_RANGE_INVALID')
    outside = lower < date.fromisoformat(window[0]) or upper > date.fromisoformat(window[-1])
    rows = []
    try:
        for ref in entry['shards']:
            shard = verify_shard_bytes(_object(ref['sha256'], revision), ref, index, entry, now)
            rows.extend(shard['bars'])
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(503, 'UNIFIED_MARKET_SHARD_UNAVAILABLE') from None
    rows.sort(key=lambda row: datetime.fromisoformat(row['timestamp']))
    if len({datetime.fromisoformat(row['timestamp']) for row in rows}) != len(rows):
        raise HTTPException(503, 'UNIFIED_MARKET_CONFLICTING_SHARDS')
    available = [row for row in rows if lower <= date.fromisoformat(row['trading_day']) <= upper]
    try:
        calendar = Calendar(index['calendar'])
        expected = calendar.expected(now, 'daily' if frequency == 'day' else 'minute')
        wanted = grid(calendar, frequency, [day for day in window
            if lower <= date.fromisoformat(day) <= upper], now)
    except (ValueError, KeyError, TypeError):
        raise HTTPException(503, 'UNIFIED_MARKET_CURRENT_CALENDAR_UNAVAILABLE') from None
    present = {datetime.fromisoformat(row['timestamp']) for row in available}
    missing = [stamp for stamp in wanted if datetime.fromisoformat(stamp) not in present]
    state = 'QUARANTINED' if entry['state'] == 'QUARANTINED' else (
        'OUTSIDE_WINDOW' if outside and not available else 'NO_DATA' if not available
        else 'PARTIAL' if missing or outside else 'AVAILABLE')
    age = (now-datetime.fromisoformat(index['published_at'])).total_seconds()
    freshness = ('STALE_PUBLICATION' if age > 180 else 'QUARANTINED'
        if state == 'QUARANTINED' else 'NO_DATA' if not rows else 'FRESH'
        if rows and datetime.fromisoformat(rows[-1]['timestamp']) >= expected else 'PARTIAL')
    selected = available[-limit:]
    return {**AUTHORITY, 'schema_version': 'qtrade.unified-market-series.v1',
        'publication_available': True, 'state': state, 'symbol': symbol,
        'instrument_id': entry['instrument_id'], 'security_type': entry['security_type'],
        'frequency': frequency, 'universe_version': index['universe_version'],
        'index_id': identity, 'version': identity, 'publication_revision': revision,
        'manifest_sha256': signed(entry)['evidence_sha256'] if entry['shards'] else None,
        'published_at': index['published_at'], 'checked_at': now.isoformat(),
        'window_start': window[0], 'window_end': window[-1],
        'available_start': rows[0]['timestamp'] if rows else None,
        'available_end': rows[-1]['timestamp'] if rows else None,
        'actual_end': rows[-1]['timestamp'] if rows else None,
        'expected_end': expected.isoformat(), 'price_basis': PRICE_BASIS,
        'freshness': freshness,
        'gaps': {'missing_timestamps': missing, 'requested_outside_window': outside},
        'limitations': index['limitations']+entry['issues'], 'bars': selected,
        'count': len(selected), 'total_count': len(available), 'truncated': len(available) > limit}
