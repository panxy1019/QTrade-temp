"""Capture at most 50 ETF daily responses once; seal replayable close-only diagnostics."""
from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_versions import file_hash
from qtrade.research.etf_trend import admit, etf_targets
from qtrade.research.qlib_publication import verify_signed
from scripts.atomic_json import write_json_immutable
from scripts.collect_unified_data import fetch
from scripts.unified_store import UnifiedBatchStore

REPO = Path(__file__).resolve().parents[1]


def run(master_path: Path, output: Path, revision: str, *, collect: bool,
        source: Callable[[str, dict[str, str]], dict[str, Any]] | None = None) -> dict[str, Any]:
    if os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
        raise ValueError('LIVE_TRADING_MUST_REMAIN_DISABLED')
    if not re.fullmatch('[a-f0-9]{40}', revision):
        raise ValueError('ETF_FULL_SOURCE_REVISION_REQUIRED')
    now = datetime.now(UTC)
    calendar = Calendar(REPO/'configs/observer_calendar_2026.json')
    master = json.loads(master_path.read_text())
    symbols = etf_targets(master, now)
    as_of = calendar.expected(now, 'daily').date()
    bindings = {'master_sha256': master['evidence_sha256'], 'as_of': str(as_of),
                'code_revision': revision, 'calendar_sha256': signed(calendar.document)[
                    'evidence_sha256'], 'contract': 'ETF_CLOSE_DIAGNOSTIC_V1'}
    identity = signed(bindings)['evidence_sha256']
    root = output/identity
    root.mkdir(parents=True, exist_ok=True)
    receipt_path = root/'receipt.json'
    store = UnifiedBatchStore(root/'capture-store')
    if not receipt_path.exists():
        if not collect:
            raise ValueError('ETF_RECEIPT_MISSING_EXPLICIT_COLLECT_REQUIRED')
        # A persisted attempt prevents interrupted executions from issuing another burst.
        write_json_immutable(root/'collection-attempt.json', signed({**bindings,
                             'started_at': now.isoformat(), 'maximum_queries': len(symbols)}))
        captures = [(source or fetch)('sina_klines', {
            'symbol': ('sh' if symbol.startswith('5') else 'sz')+symbol,
            'scale': '240', 'ma': 'no', 'datalen': '63'}) for symbol in symbols]
        raw = store.capture(captures)
        write_json_immutable(receipt_path, signed({**AUTHORITY, 'bindings': bindings, 'raw': raw}))
    receipt = json.loads(receipt_path.read_text())
    verify_signed(receipt)
    if receipt['bindings'] != bindings or any(receipt.get(k) != v for k, v in AUTHORITY.items()):
        raise ValueError('ETF_RECEIPT_BINDING_MISMATCH')
    captures = store.read_captures(receipt['raw'])
    value = admit(captures, symbols, calendar, as_of, datetime.now(UTC))
    result_path = root/'diagnostic.json'
    value = signed({k: v for k, v in {**value, 'bindings': bindings,
                    'receipt_sha256': file_hash(receipt_path)}.items() if k != 'evidence_sha256'})
    if result_path.exists():
        if json.loads(result_path.read_text()) != value:
            raise ValueError('ETF_IMMUTABLE_DIAGNOSTIC_CONFLICT')
    else:
        write_json_immutable(result_path, value)
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--master', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--collect', action='store_true')
    args = parser.parse_args()
    value = run(args.master, args.output, args.source_revision, collect=args.collect)
    print(json.dumps({k: value[k] for k in ('state', 'as_of', 'qualified_close_symbols',
                                          'required_symbols', 'execution_admitted')}))
