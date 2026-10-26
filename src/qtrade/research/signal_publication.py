"""Verify immutable indicative signals and expose a bounded observation view."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.data_versions import file_hash
from qtrade.research.model_registry import checked_document, checked_identity
from qtrade.research.signal_baselines import STRATEGIES, allocation


def verify_signal_archive(root: Path) -> dict[str, Any]:
    manifest = json.loads((root/'manifest.json').read_bytes())
    checked_document(manifest)
    identity = checked_identity(manifest['signal_family_id'])
    if (identity != signed(manifest['bindings'])['evidence_sha256']
            or manifest['schema_version'] != 'qtrade.signal-baselines.v1'
            or manifest['forward_return_observed'] is not False
            or manifest['portfolio_state_reconciled'] is not False):
        raise ValueError('SIGNAL_ARCHIVE_IDENTITY_INVALID')
    files = manifest['files']
    if (not isinstance(files, dict) or not 3 <= len(files) <= 20
            or not {'signals.json', 'indicative-targets.json', 'close-input.parquet'} <= set(files)
            or set(files) != {p.relative_to(root).as_posix() for p in root.rglob('*')
                             if p.is_file() and p != root/'manifest.json'}):
        raise ValueError('SIGNAL_ARCHIVE_INVENTORY_INVALID')
    for name, digest in files.items():
        p = root/name
        if (p.is_symlink() or not p.resolve().is_relative_to(root.resolve())
                or p.stat().st_size > 10_000_000 or file_hash(p) != digest):
            raise ValueError('SIGNAL_ARCHIVE_CONTENT_CHANGED')
    signals = json.loads((root/'signals.json').read_bytes())
    targets = json.loads((root/'indicative-targets.json').read_bytes())
    checked_document(signals)
    checked_document(targets)
    if (signals['bindings'] != manifest['bindings'] or signals['as_of'] != manifest['data_end']
            or signals['future_prices_consumed'] is not False
            or targets['generated_at'] != manifest['generated_at']
            or targets['expected_execution_at'] != manifest['expected_execution_at']):
        raise ValueError('SIGNAL_ARCHIVE_CONTEXT_CHANGED')
    result = signed({**AUTHORITY, 'schema_version': 'qtrade.signal-publication.v1',
        'manifest': manifest, 'signals': signals['signals'], 'targets': targets['targets']})
    return validate_signal_publication(result)


def validate_signal_publication(value: dict[str, Any]) -> dict[str, Any]:
    checked_document(value)
    if value['schema_version'] != 'qtrade.signal-publication.v1':
        raise ValueError('SIGNAL_PUBLICATION_SCHEMA_INVALID')
    m = value['manifest']
    checked_document(m)
    if (m['signal_family_id'] != signed(m['bindings'])['evidence_sha256']
            or m['schema_version'] != 'qtrade.signal-baselines.v1'):
        raise ValueError('SIGNAL_MANIFEST_IDENTITY_INVALID')
    checked_identity(m['bindings']['data_version_id'])
    generated = datetime.fromisoformat(m['generated_at'])
    execute = datetime.fromisoformat(m['expected_execution_at'])
    if (generated.tzinfo is None or execute.tzinfo is None or generated >= execute
            or generated > datetime.now(UTC)
            or m['forward_return_observed'] is not False
            or m['portfolio_state_reconciled'] is not False
            or not 1 <= m['qualified_instruments'] <= 6000
            or m['signal_count'] != len(value['signals'])
            or not set(value['signals']) <= set(STRATEGIES)
            or set(value['signals']) != set(value['targets'])):
        raise ValueError('SIGNAL_PUBLICATION_SCOPE_INVALID')
    for name, scores in value['signals'].items():
        instruments = {row['instrument'] for row in scores}
        if (len(scores) > m['qualified_instruments'] or len(instruments) != len(scores)
                or any(not math.isfinite(row['score']) for row in scores)):
            raise ValueError('SIGNAL_PUBLICATION_SCORES_INVALID')
        target = value['targets'][name]
        positions = target['positions']
        weights = [Decimal(p['target_weight']) for p in positions]
        if (len(positions) != len({p['instrument'] for p in positions})
                or any(p['instrument'] not in instruments for p in positions)
                or any(not w.is_finite() or w <= 0 for w in weights)
                or sum(weights) > Decimal('0.950000000000001')
                or any(target[key] is not False for key in ('holdings_reconciled',
                    'turnover_constraints_applied', 'execution_constraints_applied'))
                or target['fills'] != [] or target['forward_return'] is not None):
            raise ValueError('SIGNAL_PUBLICATION_NOT_EXECUTION_ADMITTED')
        if positions != allocation(scores, method=target['allocation_method']):
            raise ValueError('SIGNAL_INDICATIVE_ALLOCATION_MISMATCH')
    return value
