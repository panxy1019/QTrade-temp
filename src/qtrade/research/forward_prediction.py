"""Clock, lineage and coverage gates shared by frozen-model forward inference."""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from qtrade.market_data.akshare import SHANGHAI
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_versions import file_hash


def verify_validation(root: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads((root/'manifest.json').read_bytes())
    for document in (value, value['contract']):
        if (signed({k: v for k, v in document.items() if k != 'evidence_sha256'}) != document
                or any(document.get(k) != v for k, v in AUTHORITY.items())):
            raise ValueError('FORWARD_VALIDATION_HASH_OR_AUTHORITY_INVALID')
    bindings = value['bindings']
    if (value.get('schema_version') != 'qtrade.qlib-forward-validation.v1'
            or value['state'] != 'FORWARD_INPUT_VALIDATED_NOT_ACTIVATED'
            or value['model_activated'] is not False or value['auto_predict_enabled'] is not False
            or value['validation_id'] != signed(bindings)['evidence_sha256']
            or bindings['model'] != value['contract']['model_bundle_sha256']
            or bindings['data'] != value['contract']['data_version_id']
            or value['fitted_processors_unchanged'] is not True
            or value['fit_methods_forbidden'] is not True
            or value['contract']['forward_return_observed'] is not False
            or not 1 <= value['score_count'] <= 6000 or value['feature_count'] != 158):
        raise ValueError('FORWARD_VALIDATION_BINDING_INVALID')
    files = value['files']
    if not {'request.json', 'scores.parquet', 'features.parquet'} <= set(files):
        raise ValueError('FORWARD_VALIDATION_FILES_MISSING')
    for name, digest in files.items():
        path = root/name
        if (path.is_symlink() or not path.resolve().is_relative_to(root.resolve())
                or not path.is_file() or file_hash(path) != digest):
            raise ValueError('FORWARD_VALIDATION_FILE_CHANGED')
    if ({p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()
         and p != root/'manifest.json'} != set(files)
            or any(files.get('source/'+name) != digest
                   for name, digest in bindings['code'].items())):
        raise ValueError('FORWARD_VALIDATION_UNBOUND_FILE_OR_CODE')
    return value


def next_execution(end: str, calendar: Calendar) -> datetime:
    day = date.fromisoformat(end) + timedelta(days=1)
    while not calendar.is_open(day):
        day += timedelta(days=1)
    return datetime.combine(day, time(9, 30), SHANGHAI)


def inference_contract(model: dict[str, Any], trained_data: dict[str, Any],
                       current_data: dict[str, Any], version: dict[str, Any],
                       calendar: Calendar, *, now: datetime) -> dict[str, Any]:
    if now.tzinfo is None:
        raise ValueError('FORWARD_GENERATION_TIMEZONE_REQUIRED')
    end = version['actual_end']
    ancestors = set(current_data.get('ancestor_dataset_sha256', []))
    ancestors.add(current_data['parent_dataset_sha256'])
    if (trained_data['evidence_sha256'] not in ancestors
            or current_data['end'] != end or end <= model['reference_end']
            or model['reference_end'] != trained_data['end']
            or model['feature_dtypes'] != ['float64']*158):
        raise ValueError('FORWARD_TRAINED_DATA_LINEAGE_OR_PRECISION_CHANGED')
    if calendar.expected(now, 'daily').date().isoformat() != end:
        raise ValueError('FORWARD_DATA_NOT_LATEST_COMPLETE_SESSION')
    execution = next_execution(end, calendar)
    if execution <= now:
        raise ValueError('FORWARD_EXPECTED_EXECUTION_ALREADY_PASSED')
    return signed({
        **AUTHORITY, 'data_version_id': version['version_id'], 'data_end': end,
        'model_bundle_sha256': model['evidence_sha256'],
        'model_reference_end': model['reference_end'],
        'training_data_sha256': model['data_sha256'],
        'observation_universe_version': current_data['incremental']['observation_universe_version'],
        'current_input_symbols': current_data['incremental']['current_symbols'],
        'blocked_input_symbols': current_data['incremental']['blocked_symbols'],
        'generated_at': now.isoformat(), 'expected_execution_at': execution.isoformat(),
        'expected_execution_basis': 'NEXT_VERIFIED_EXCHANGE_SESSION_OPEN_RESEARCH_ONLY',
        'forward_return_observed': False, 'historical_pit_certified': False,
    })


def expected_codes(intervals: list[str], current: list[str], day: str) -> list[str]:
    eligible = set()
    for line in intervals:
        code, start, end = line.split('\t')
        if not re.fullmatch(r'(SH|SZ)\d{6}', code):
            raise ValueError('FORWARD_UNIVERSE_CODE_INVALID')
        if start <= day <= end and code[2:] in current:
            eligible.add(code)
    if not eligible:
        raise ValueError('FORWARD_NO_QUALIFIED_SUPPORTED_STOCKS')
    return sorted(eligible)


def rank_scores(rows: list[dict[str, Any]], expected: list[str]) -> list[dict[str, Any]]:
    if (len(rows) != len(expected) or len({r['instrument'] for r in rows}) != len(rows)
            or {r['instrument'] for r in rows} != set(expected)
            or any(type(r['score']) not in (int, float) or not math.isfinite(r['score'])
                   for r in rows)):
        raise ValueError('FORWARD_SCORE_COVERAGE_OR_VALUES_INVALID')
    ordered = sorted(rows, key=lambda r: (-r['score'], r['instrument']))
    return [{'instrument': r['instrument'], 'score': r['score'], 'rank': index+1}
            for index, r in enumerate(ordered)]
