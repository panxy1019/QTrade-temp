"""Read scoped task/status evidence into an independent, non-authoritative summary."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from qtrade.market_data.akshare import SHANGHAI
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_quality import (
    SCHEMA,
    checked_observation,
    classify_task,
    validate_quality,
)
from qtrade.research.workflow import digest
from scripts.atomic_json import write_json_atomic, write_json_immutable
from scripts.collect_recent_status import verified_recent

REPO = Path(__file__).resolve().parents[1]


def current_universe(base: Path) -> str:
    from qtrade.api.unified_data import validate_publication

    value = json.loads((base / 'unified-data/public/unified.json').read_bytes())
    return str(validate_publication(value)['master']['universe_version'])


def _issuer_starts(root: Path, now: datetime) -> list[dict[str, Any]]:
    path = root / 'issuer-starts.json'
    if not path.exists():
        return []
    value = json.loads(path.read_bytes())
    if (not isinstance(value, dict)
            or signed({k: v for k, v in value.items() if k != 'evidence_sha256'}) != value):
        raise ValueError('QUALITY_ISSUER_INDEX_HASH_INVALID')
    result = []
    for record in value['records']:
        identity = record['raw_sha256']
        if (len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity)
                or sha256((root / 'issuer-documents' / (identity + '.pdf')).read_bytes())
                .hexdigest() != identity
                or datetime.fromisoformat(record['observed_at']) > now
                or record['historical_available_at'] is not None):
            raise ValueError('QUALITY_ISSUER_SOURCE_INVALID')
        result.append(record)
    return result


def build(base: Path, database: Path, *, revision: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    if re.fullmatch('[a-f0-9]{40}', revision) is None:
        raise ValueError('QUALITY_FULL_SOURCE_REVISION_REQUIRED')
    root = base / 'data-quality'
    universe = current_universe(base)
    calendar = Calendar(REPO / 'configs/observer_calendar_2026.json')
    days = [calendar.previous(now.astimezone(SHANGHAI).date())]
    for _ in range(2):
        days.append(calendar.previous(days[-1] - timedelta(days=1)))
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        counts = [dict(r) for r in conn.execute(
            'SELECT trading_day,frequency,state,count(*) AS count FROM collection_tasks '
            'WHERE universe_version=? AND trading_day IN (?,?,?) GROUP BY 1,2,3',
            [universe, *[str(d) for d in days]])]
        tasks = [dict(r) for r in conn.execute(
            "SELECT * FROM collection_tasks WHERE universe_version=? AND trading_day IN (?,?,?) "
            "AND state IN ('FAILED','INTERRUPTED') ORDER BY trading_day,symbol,frequency",
            [universe, *[str(d) for d in days]])]
    observations, issues = [], []
    recent: dict[str, Any] = {'available': False, 'content_verified_symbols': 0}
    pointer = base / 'recent-status/current.json'
    if pointer.exists():
        try:
            current = json.loads(pointer.read_bytes())
            if (not isinstance(current, dict)
                    or signed({k: v for k, v in current.items()
                               if k != 'evidence_sha256'}) != current
                    or any(current.get(k) != v for k, v in AUTHORITY.items())
                    or datetime.fromisoformat(current['observed_at']) > now):
                raise ValueError('RECENT_STATUS_POINTER_HASH_INVALID')
            scope = current['scope']
            if (current['window_sha256'] != digest(scope)
                    or not set(current['status_checksums']) <= {r['symbol']
                                                               for r in scope['stocks']}):
                raise ValueError('RECENT_STATUS_SCOPE_INVALID')
            progress = current['collection_progress']
            if (current.get('schema_version') != 'qtrade.recent-status-coverage.v1'
                    or progress['scope_symbols'] != len(scope['stocks'])
                    or progress['completed_symbols'] != len(current['status_checksums'])
                    or progress['completed_symbols'] + progress['unresolved_symbols']
                    != progress['scope_symbols']):
                raise ValueError('RECENT_STATUS_PROGRESS_INVALID')
            folder = base / 'recent-status/windows' / current['window_sha256'] / 'status'
            for symbol, identity in current['status_checksums'].items():
                try:
                    record = verified_recent(folder / (symbol + '.json'), scope, now)
                    if record['evidence_sha256'] != identity or record['symbol'] != symbol:
                        raise ValueError('RECENT_STATUS_POINTER_BINDING_INVALID')
                    observations.append(record)
                except (OSError, KeyError, TypeError, ValueError):
                    issues.append({'symbol': symbol, 'reason': 'RECENT_STATUS_CONTENT_QUARANTINED'})
            recent = {'available': True, 'observed_at': current['observed_at'],
                'window_start': scope['start'], 'window_end': scope['end'],
                'progress': current['collection_progress'],
                'content_verified_symbols': len(observations),
                'source_revision': current['source_revision'],
                'coverage_sha256': current['evidence_sha256'],
                'historical_available_at': None, 'historical_pit_certified': False}
        except (OSError, KeyError, TypeError, ValueError):
            issues.append({'symbol': None, 'reason': 'RECENT_STATUS_POINTER_UNAVAILABLE'})
    for path in sorted((root / 'supplemental-status').glob('*.json')):
        try:
            observations.append(checked_observation(json.loads(path.read_bytes()), now))
        except (OSError, KeyError, TypeError, ValueError):
            issues.append({'symbol': None, 'reason': 'SUPPLEMENTAL_STATUS_QUARANTINED'})
    try:
        starts = _issuer_starts(root, now)
    except (OSError, KeyError, TypeError, ValueError):
        starts = []
        issues.append({'symbol': None, 'reason': 'ISSUER_START_EVIDENCE_UNAVAILABLE'})
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for record in observations:
        by_symbol.setdefault(record['symbol'], []).append(record)
    cases = [classify_task(task, by_symbol.get(task['symbol'], []), starts, now=now)
             for task in tasks]
    value = signed({**AUTHORITY, 'schema_version': SCHEMA, 'observed_at': now.isoformat(),
        'universe_version': universe,
        'source_revision': revision, 'production_admission_effect': 'NONE',
        'execution_admitted': False, 'policy_status': 'DIAGNOSTIC_ONLY_PENDING_APPROVAL',
        'task_counts': counts, 'case_counts': dict(Counter(r['classification'] for r in cases)),
        'cases': cases, 'recent_status': recent, 'source_issues': issues,
        'purpose_contract': {
            'DAILY_RESEARCH': 'QUALIFIED_DAILY_SUBSET_INDEPENDENT_OF_MINUTE_GAPS',
            'MINUTE_RESEARCH': 'REQUIRE_OWN_SYMBOL_DATE_MINUTE_COVERAGE',
            'PREDICTION': 'EXISTING_MODEL_INPUT_AND_IMMUTABLE_VERSION_VALIDATION_REQUIRED',
            'PAPER': 'EXISTING_OBSERVED_SECURITY_CONSTRAINTS_REMAIN_FAIL_CLOSED'},
        'original_failure_records_preserved': True, 'historical_pit_certified': False,
        'coverage_basis': 'QUEUE_METADATA_AND_VALIDATED_STATUS_NOT_FULL_BAR_REPLAY',
        'limits': ['RECENT_STATUS_SAMPLE_IS_990_NOT_FULL_TARGET_POOL',
                   'CURRENT_RETRIEVAL_IS_NOT_HISTORICAL_DECISION_KNOWLEDGE',
                   'ISSUER_START_PLUS_VENDOR_DAY_IS_NOT_FULL_OFFICIAL_EVENT_CHAIN',
                   'NO_NEW_PRICE_BARS_OR_EXECUTION_AUTHORITY']})
    validate_quality(value, now=now)
    version = root / 'versions' / (value['evidence_sha256'] + '.json')
    write_json_immutable(version, value)
    write_json_atomic(root / 'public/quality.json', value, compact=True)
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    args = parser.parse_args()
    value = build(args.base, args.database, revision=args.source_revision)
    print(json.dumps({'cases': value['case_counts'], 'recent': value['recent_status']}))


if __name__ == '__main__':
    main()
