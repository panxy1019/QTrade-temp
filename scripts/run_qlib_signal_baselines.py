"""Seal current factor signals and indicative targets; no simulated fills or orders."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_versions import file_hash, verify_version
from qtrade.research.forward_prediction import expected_codes, next_execution
from qtrade.research.signal_baselines import allocation, signal_family
from qtrade.research.signal_publication import verify_signal_archive
from scripts.atomic_json import write_json_immutable
from scripts.build_qlib_daily_version import move_durable, sync_directory

REPO = Path(__file__).resolve().parents[1]


def run(root: Path, output: Path, revision: str) -> dict[str, Any]:
    if os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
        raise ValueError('LIVE_TRADING_MUST_REMAIN_DISABLED')
    import re

    if re.fullmatch('[a-f0-9]{40}', revision) is None:
        raise ValueError('SIGNAL_FULL_REVISION_REQUIRED')
    version = verify_version(root)
    end = version['actual_end']
    calendar = Calendar(REPO/'configs/observer_calendar_2026.json')
    now = datetime.now(UTC)
    execution = next_execution(end, calendar)
    if calendar.expected(now, 'daily').date().isoformat() != end or execution <= now:
        raise ValueError('SIGNAL_CURRENT_INPUT_AND_FUTURE_EXECUTION_REQUIRED')
    qlib = import_module('qlib')
    qlib.init(provider_uri=str(root/'provider'), region='cn', kernels=2)
    D = import_module('qlib.data').D
    days = [d for d in (root/'provider/calendars/day.txt').read_text().splitlines()
            if d <= end][-21:]
    codes = expected_codes((root/'provider/instruments/workflow_supported.txt').read_text(
        ).splitlines(), version['current_symbols'], end)
    frame = D.features(codes, ['$close'], start_time=days[0], end_time=end)
    histories = {code: [{'date': str(day.date()), 'close': float(row.iloc[0])}
        for day, row in frame.xs(code, level='instrument').iterrows()] for code in codes}
    family = signal_family(histories, days, as_of=end, security_type='STOCK')
    bindings = {'data_version_id': version['version_id'], 'code_revision': revision,
        'observation_universe_version': version['bindings']['observation_universe_version'],
        'signal_contract': 'COMMON_21_SESSION_GRID_V1', 'risk_degree': '0.95', 'topk': 50}
    identity = signed(bindings)['evidence_sha256']
    target = output/identity
    if target.exists():
        return dict(verify_signal_archive(target)['manifest'])
    stage = output/(identity+'.'+now.strftime('%Y%m%dT%H%M%S%f')+'.partial')
    stage.mkdir(parents=True, exist_ok=False)
    frame.to_parquet(stage/'close-input.parquet')
    generated = datetime.now(UTC)
    if generated >= execution:
        raise ValueError('SIGNAL_EXECUTION_WINDOW_EXPIRED_DURING_COMPUTE')
    targets = {}
    for name, scores in family['signals'].items():
        method = 'EQUAL_WEIGHT_ALL' if name == 'equal_weight_universe' else 'TOPK_EQUAL_WEIGHT'
        targets[name] = {'allocation_method': method,
            'positions': allocation(scores, method=method),
            'holdings_reconciled': False, 'turnover_constraints_applied': False,
            'execution_constraints_applied': False, 'fills': [], 'forward_return': None}
    write_json_immutable(stage/'signals.json', signed({**AUTHORITY, **family,
                                                      'bindings': bindings}))
    write_json_immutable(stage/'indicative-targets.json', signed({**AUTHORITY, 'targets': targets,
        'generated_at': generated.isoformat(), 'expected_execution_at': execution.isoformat(),
        'state': 'INDICATIVE_RESEARCH_TARGETS_NOT_EXECUTION_ADMITTED'}))
    for name in ('scripts/run_qlib_signal_baselines.py', 'src/qtrade/research/signal_baselines.py',
                 'src/qtrade/research/factors.py',
                 'src/qtrade/research/signal_publication.py'):
        destination = stage/'source'/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((REPO/name).read_bytes())
    manifest = signed({**AUTHORITY, 'schema_version': 'qtrade.signal-baselines.v1',
        'signal_family_id': identity, 'bindings': bindings, 'data_end': end,
        'generated_at': generated.isoformat(), 'expected_execution_at': execution.isoformat(),
        'signal_count': len(family['signals']), 'qualified_instruments': len(family['eligible']),
        'excluded_instruments': family['rejected'], 'unavailable': family['unavailable'],
        'forward_return_observed': False, 'portfolio_state_reconciled': False,
        'state': 'SIGNALS_AND_INDICATIVE_TARGETS_SEALED',
        'limitations': ['CURRENT_POOL_SURVIVORSHIP_BIAS', 'FULL_PIT_NOT_CERTIFIED',
            'VENDOR_ADJUSTMENTS_NOT_INDEPENDENTLY_RECONCILED',
            'TARGETS_AWAIT_HOLDINGS_TURNOVER_AND_EXECUTION_CONSTRAINTS',
            'NO_ETF_PROVIDER_ADMISSION', 'NO_HISTORICAL_STRATEGY_COMPARISON_IN_THIS_ARTIFACT'],
        'files': {p.relative_to(stage).as_posix(): file_hash(p)
                  for p in stage.rglob('*') if p.is_file()}})
    write_json_immutable(stage/'manifest.json', manifest)
    for p in stage.rglob('*'):
        if p.is_file():
            with p.open('rb') as handle:
                os.fsync(handle.fileno())
    for p in sorted((p for p in stage.rglob('*') if p.is_dir()), key=lambda p: len(p.parts),
                    reverse=True):
        sync_directory(p)
    sync_directory(stage)
    move_durable(stage, target)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-version-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    args = parser.parse_args()
    result = run(args.data_version_root, args.output, args.source_revision)
    print(json.dumps({k: result[k] for k in ('signal_family_id', 'state', 'signal_count',
                                           'qualified_instruments', 'unavailable')}))
