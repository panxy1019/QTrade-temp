"""Explicit human approval for a new daily-input scope; default contract is unchanged."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.workflow import digest

SCHEMA = 'qtrade.quality-scope-policy.v1'
MODE = 'DATASET_DAILY_REQUIRED_SCOPE_V1'
USES = {'DAILY_RESEARCH', 'FACTOR_EVALUATION', 'PREDICTION'}


def symbol_scope(dataset: dict[str, Any]) -> str:
    symbols = sorted(r['symbol'] for r in dataset['stocks'])
    if (not symbols or len(symbols) != len(set(symbols))
            or any(re.fullmatch('[036][0-9]{5}', s) is None for s in symbols)):
        raise ValueError('QUALITY_POLICY_DATASET_SCOPE_INVALID')
    return digest(symbols)


def validate_policy(
    value: dict[str, Any], dataset: dict[str, Any], universe: str, day: str, now: datetime,
) -> dict[str, Any]:
    if (not isinstance(value, dict)
            or signed({k: v for k, v in value.items() if k != 'evidence_sha256'}) != value
            or any(type(value.get(k)) is not type(v) or value[k] != v
                   for k, v in AUTHORITY.items())
            or value.get('schema_version') != SCHEMA or value.get('mode') != MODE
            or value.get('state') != 'APPROVED' or set(value.get('uses', [])) != USES
            or not isinstance(value.get('uses'), list) or len(value['uses']) != len(USES)
            or value.get('symbol_scope_sha256') != symbol_scope(dataset)
            or value.get('universe_version') != universe
            or value.get('paper_constraints_changed') is not False
            or value.get('model_activation_authorized') is not False):
        raise ValueError('QUALITY_SCOPE_POLICY_NOT_APPROVED_OR_BOUND')
    approved = datetime.fromisoformat(value['approved_at'])
    if (now.tzinfo is None or approved.tzinfo is None or approved > now
            or date.fromisoformat(day) < date.fromisoformat(value['effective_from_day'])
            or not isinstance(value.get('approved_by'), str) or not value['approved_by']
            or value.get('authorization_kind') != 'EXPLICIT_HUMAN_ACTION'
            or re.fullmatch('[a-f0-9]{64}', value.get('authorization_message_sha256', '')) is None
            or value['authorization_message_sha256'] == '0'*64):
        raise ValueError('QUALITY_SCOPE_POLICY_HUMAN_AUTHORIZATION_REQUIRED')
    return value


def load_policy(base: Path) -> dict[str, Any] | None:
    import json

    path = base / 'data-quality/approved-scope-policy.json'
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 64_000:
        raise ValueError('QUALITY_SCOPE_APPROVAL_FILE_INVALID')
    # Only the operator writes this file after the actual human approval; no public writer exists.
    value: dict[str, Any] = json.loads(path.read_bytes())
    return value


def request_policy(request: dict[str, Any], dataset: dict[str, Any], now: datetime
                   ) -> dict[str, Any] | None:
    keys = {'input_scope_policy', 'input_scope_policy_sha256'}
    if not keys.intersection(request):
        return None
    policy = request.get('input_scope_policy')
    if (not keys <= set(request) or not isinstance(policy, dict)
            or policy.get('evidence_sha256') != request['input_scope_policy_sha256']):
        raise ValueError('QUALITY_SCOPE_REQUEST_APPROVAL_INCOMPLETE')
    return validate_policy(policy, dataset, request['universe_version'], request['day'], now)
