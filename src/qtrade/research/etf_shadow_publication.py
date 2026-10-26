"""Bounded data-only ETF SHADOW publication, separate from stock execution books."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import InvalidOperation
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.etf_research_contract import (
    BLOCKERS,
    EtfAllocationPolicy,
    changed_etf_contract_axes,
    etf_contract,
    validate_etf_shadow_observation,
    validate_etf_target,
)
from qtrade.research.etf_trend import LIMITATIONS as CLOSE_LIMITATIONS
from qtrade.research.model_registry import checked_document, checked_identity

SCHEMA = 'qtrade.etf-shadow-service.v1'
STRATEGY = 'etf_positive_trend20_native_close_rotation_v1'
MAXIMUM_BYTES = 1_000_000
LIMITATIONS = list(dict.fromkeys([
    *CLOSE_LIMITATIONS, *BLOCKERS, 'FULL_PIT_NOT_CERTIFIED', 'HISTORICAL_AVAILABLE_AT_UNKNOWN',
    'TARGET_WEIGHTS_ARE_NOT_HOLDINGS', 'SHADOW_HAS_NO_EXECUTION_OR_PORTFOLIO_RETURN',
]))


def _exact(value: Any, fields: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('ETF_SHADOW_PUBLIC_FIELDS_INVALID')


def _clock(value: Any, now: datetime) -> datetime:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValueError('ETF_SHADOW_PUBLIC_CLOCK_INVALID')
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp > now:
        raise ValueError('ETF_SHADOW_PUBLIC_CLOCK_INVALID')
    return stamp


def validate_etf_shadow_publication(value: dict[str, Any], *,
                                    now: datetime | None = None) -> dict[str, Any]:
    """Validate observed bytes and prospective bindings without granting execution authority."""
    stamp = now or datetime.now(UTC)
    if stamp.tzinfo is None:
        raise ValueError('ETF_SHADOW_PUBLIC_AWARE_CLOCK_REQUIRED')
    try:
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False,
                          separators=(',', ':')).encode()) > MAXIMUM_BYTES:
            raise ValueError('ETF_SHADOW_PUBLICATION_TOO_LARGE')
        _exact(value, set(AUTHORITY) | {
            'evidence_sha256', 'schema_version', 'security_type', 'mode', 'strategy_id',
            'contract_id', 'contract', 'generated_at', 'heartbeat_at', 'source_revision', 'state',
            'entries', 'errors', 'limitations', 'execution_admitted', 'paper_admission',
            'orders', 'fills', 'holdings', 'cash', 'net_asset_value', 'forward_return',
        })
        checked_document(value)
        if (any(type(value[k]) is not type(expected) for k, expected in AUTHORITY.items())
                or value['schema_version'] != SCHEMA or value['security_type'] != 'ETF'
                or value['mode'] != 'SHADOW' or value['strategy_id'] != STRATEGY
                or value['state'] not in {'RUNNING', 'DEGRADED', 'STOPPED'}
                or value['execution_admitted'] is not False
                or any(value[key] != [] for key in ('orders', 'fills', 'holdings'))
                or any(value[key] is not None for key in ('cash', 'net_asset_value',
                                                          'forward_return'))):
            raise ValueError('ETF_SHADOW_PUBLIC_AUTHORITY_INVALID')
        _exact(value['paper_admission'], {'state', 'reasons'})
        if value['paper_admission'] != {'state': 'BLOCKED', 'reasons': BLOCKERS}:
            raise ValueError('ETF_SHADOW_PUBLIC_PAPER_ADMISSION_INVALID')
        if (not isinstance(value['source_revision'], str)
                or re.fullmatch('[a-f0-9]{40}', value['source_revision']) is None):
            raise ValueError('ETF_SHADOW_PUBLIC_REVISION_INVALID')
        generated = _clock(value['generated_at'], stamp)
        heartbeat = _clock(value['heartbeat_at'], stamp)
        if generated > heartbeat:
            raise ValueError('ETF_SHADOW_PUBLIC_HEARTBEAT_PRECEDES_PUBLICATION')
        # Reuse the canonical ETF behavior validator rather than reinterpreting
        # lot, fee, adjustment or model policies from stock publication contracts.
        changed_etf_contract_axes(value['contract'], etf_contract(EtfAllocationPolicy()))
        checked_identity(value['contract_id'])
        if signed(value['contract'])['evidence_sha256'] != value['contract_id']:
            raise ValueError('ETF_SHADOW_PUBLIC_CONTRACT_ID_INVALID')
        errors = value['errors']
        if (not isinstance(errors, list) or len(errors) > 32 or any(
                not isinstance(code, str) or re.fullmatch('[A-Z][A-Z0-9_]{0,119}', code) is None
                for code in errors) or value['state'] == 'RUNNING' and errors
                or value['state'] == 'DEGRADED' and not errors):
            raise ValueError('ETF_SHADOW_PUBLIC_ERROR_STATE_INVALID')
        limitations = value['limitations']
        if (not isinstance(limitations, list) or not 1 <= len(limitations) <= 32
                or any(not isinstance(reason, str) or not 1 <= len(reason) <= 500
                       for reason in limitations)
                or not set(LIMITATIONS) <= set(limitations)):
            raise ValueError('ETF_SHADOW_PUBLIC_LIMITATIONS_INVALID')
        entries = value['entries']
        if not isinstance(entries, list) or len(entries) > 30:
            raise ValueError('ETF_SHADOW_PUBLIC_ENTRIES_BOUND_INVALID')
        seen: set[str] = set()
        for entry in entries:
            _exact(entry, {'target', 'observation'})
            target = validate_etf_target(entry['target'], now=generated)
            if (target['target_id'] in seen or target['strategy_id'] != value['strategy_id']
                    or target['contract'] != value['contract']
                    or target['bindings']['contract_id'] != value['contract_id']):
                raise ValueError('ETF_SHADOW_PUBLIC_TARGET_CONTEXT_INVALID')
            seen.add(target['target_id'])
            if entry['observation'] is not None:
                validate_etf_shadow_observation(entry['observation'], target, now=generated)
        return value
    except (KeyError, TypeError, AttributeError, OverflowError, RecursionError,
            InvalidOperation) as exc:
        raise ValueError('ETF_SHADOW_PUBLICATION_INVALID') from exc
