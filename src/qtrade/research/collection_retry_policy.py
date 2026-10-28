"""Approved, expiring collection deferral; never admits prices, models or trades."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from qtrade.market_data.akshare import SHANGHAI
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.data_quality import validate_quality

SCHEMA = 'qtrade.suspension-retry-policy.v1'
MODE = 'DAY_SPECIFIC_SUSPENSION_RETRY_DEFERRAL_V1'


def validate_retry_policy(value: dict[str, Any], universe: str, now: datetime) -> None:
    if (not isinstance(value, dict)
            or signed({k: v for k, v in value.items() if k != 'evidence_sha256'}) != value
            or any(type(value.get(k)) is not type(v) or value[k] != v
                   for k, v in AUTHORITY.items())
            or value.get('schema_version') != SCHEMA or value.get('mode') != MODE
            or value.get('state') != 'APPROVED' or value.get('universe_version') != universe
            or value.get('execution_admitted') is not False
            or value.get('paper_constraints_changed') is not False
            or value.get('model_input_admission_changed') is not False
            or value.get('max_deferral_seconds') != 21_600
            or value.get('max_status_age_seconds') != 86_400
            or value.get('quality_max_age_seconds') != 1_200):
        raise ValueError('SUSPENSION_RETRY_POLICY_INVALID')
    symbols = value['symbols']
    if (not isinstance(symbols, list) or not 1 <= len(symbols) <= 50
            or len(set(symbols)) != len(symbols)
            or any(not isinstance(s, str) or re.fullmatch('[036][0-9]{5}', s) is None
                   for s in symbols)):
        raise ValueError('SUSPENSION_RETRY_SCOPE_INVALID')
    approved = datetime.fromisoformat(value['approved_at'])
    if (now.tzinfo is None or approved.tzinfo is None or approved > now
            or date.fromisoformat(value['effective_from_day']) > now.astimezone(SHANGHAI).date()
            or value.get('authorization_kind') != 'EXPLICIT_HUMAN_ACTION'
            or not isinstance(value.get('approved_by'), str) or not value['approved_by']
            or re.fullmatch('[a-f0-9]{64}', value.get('authorization_message_sha256', '')) is None
            or value['authorization_message_sha256'] == '0' * 64):
        raise ValueError('SUSPENSION_RETRY_HUMAN_APPROVAL_REQUIRED')


def retry_deferrals(
    base: Path, universe: str, frequency: str, day: str, *, now: datetime,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        'state': 'NO_APPROVED_RETRY_POLICY', 'deferred_symbols': [], 'deferrals': [],
        'frequency': frequency, 'trading_day': day, 'universe_version': universe,
        'production_admission_effect': 'NONE', 'execution_admitted': False,
    }
    path = base / 'data-quality/approved-retry-policy.json'
    if not path.exists():
        return result
    try:
        quality_path = base / 'data-quality/public/quality.json'
        if (path.is_symlink() or path.stat().st_size > 64_000
                or quality_path.is_symlink() or quality_path.stat().st_size > 1_500_000):
            raise ValueError('SUSPENSION_RETRY_SOURCE_BOUNDS_INVALID')
        policy = json.loads(path.read_bytes())
        validate_retry_policy(policy, universe, now)
        quality = validate_quality(json.loads(quality_path.read_bytes()), now=now)
        age = (now - datetime.fromisoformat(quality['observed_at'])).total_seconds()
        if quality['universe_version'] != universe or not 0 <= age <= 1_200:
            raise ValueError('SUSPENSION_RETRY_QUALITY_STALE_OR_WRONG_SCOPE')
        accepted = []
        for row in quality['cases']:
            if (row['symbol'] not in policy['symbols'] or row['frequency'] != frequency
                    or row['trading_day'] != day or row['classification'] != 'SUSPENDED'
                    or row['original_task_state'] != 'FAILED'
                    or row['completed_end'] is not None or row['proposed_recheck_at'] is None):
                continue
            observed = datetime.fromisoformat(row['source_observed_at'])
            recheck = datetime.fromisoformat(row['proposed_recheck_at'])
            if (0 <= (now - observed).total_seconds() <= 86_400
                    and 0 < (recheck - now).total_seconds() <= 21_600):
                accepted.append({
                    'symbol': row['symbol'], 'frequency': frequency, 'trading_day': day,
                    'universe_version': universe, 'desired_end': row['desired_end'],
                    'task_sha256': row['task_snapshot_sha256'],
                    'expires_at': min(recheck, observed + timedelta(hours=24),
                        datetime.fromisoformat(quality['observed_at'])
                        + timedelta(seconds=1_200)).isoformat(),
                    'quality_observed_at': quality['observed_at'],
                    'source_observed_at': row['source_observed_at'],
                    'quality_sha256': quality['evidence_sha256'],
                    'policy_sha256': policy['evidence_sha256']})
        result.update(state='APPROVED_EXACT_DAY_RETRY_DEFERRAL',
                      deferred_symbols=sorted(r['symbol'] for r in accepted), deferrals=accepted,
                      policy_sha256=policy['evidence_sha256'],
                      quality_sha256=quality['evidence_sha256'])
    except (OSError, ValueError, TypeError, KeyError):
        # Optional deferral failure resumes the original retry, never stops ingestion.
        result['state'] = 'INVALID_RETRY_EVIDENCE_KEEP_EXISTING_RETRY'
    return result
