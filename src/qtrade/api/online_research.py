"""Bounded online model observations and authenticated human control queue."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response

from qtrade.api.research_workflow import (
    _body,
    _queue_failure,
    actor,
    read_public,
    research_clock,
)
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.data_version_control import command as data_command
from qtrade.research.data_version_control import validate_public_catalog
from qtrade.research.model_registry import checked_document, checked_identity, control_command
from qtrade.research.rolling_control import command as rolling_command
from qtrade.research.rolling_policy import validate_policy
from qtrade.research.workflow_queue import GitQueue, seal

router = APIRouter(prefix='/research/online')
STATES = {'TRAINED', 'VALIDATING', 'VALIDATED', 'STAGED', 'ACTIVE', 'FAILED', 'RETIRED'}


@router.get('/signals')
def signal_catalog(response: Response, strategy: str | None = None,
                   offset: int = Query(0, ge=0, le=6000),
                   limit: int = Query(50, ge=1, le=100)) -> dict[str, Any]:
    from qtrade.research.signal_publication import validate_signal_publication

    response.headers['Cache-Control'] = 'no-store'
    try:
        value = validate_signal_publication(read_public('signals.json', maximum=3_000_000))
        manifest = value['manifest']
        online = listing()
        current = bool(worker_fresh(online) and online['data_version']
            and manifest['bindings']['data_version_id'] == online['data_version']['version_id']
            and (not online['data_version'].get('data_event_id') or manifest['bindings'].get(
                'data_event_id') == online['data_version']['data_event_id'])
            and manifest['bindings']['code_revision'] == online['worker']['source_revision']
            and manifest['data_end'] == research_clock()
            and datetime.now(UTC) < datetime.fromisoformat(manifest['expected_execution_at']))
        families = [{'name': name, 'score_count': len(scores),
            'allocation_method': value['targets'][name]['allocation_method'],
            'target_count': len(value['targets'][name]['positions'])}
            for name, scores in value['signals'].items()]
        rows = []
        total = 0
        if strategy:
            if strategy not in value['signals']:
                raise HTTPException(404, 'SIGNAL_FAMILY_UNAVAILABLE')
            weights = {p['instrument']: p['target_weight']
                       for p in value['targets'][strategy]['positions']}
            scores = sorted(value['signals'][strategy],
                            key=lambda r: (-r['score'], r['instrument']))
            total = len(scores)
            rows = [{**r, 'indicative_weight': weights.get(r['instrument'], '0')}
                    for r in scores[offset:offset+limit]]
        return {**AUTHORITY, 'publication_available': True, 'current_input': current,
            'manifest': manifest, 'families': families, 'strategy': strategy,
            'rows': rows, 'total': total, 'offset': offset, 'limit': limit,
            'execution_admitted': False, 'forward_return': None}
    except HTTPException:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        return {**AUTHORITY, 'publication_available': False, 'current_input': False,
                'families': [], 'rows': [], 'execution_admitted': False, 'forward_return': None}


def unavailable_catalog() -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return signed({**AUTHORITY, 'schema_version': 'qtrade.online-catalog.v1',
                   'published_at': now, 'worker': {'heartbeat_at': now, 'state': 'FAILED'},
                   'models': [], 'validations': [], 'commands': [], 'events': [],
                   'active_model': None, 'data_version': None,
                   'data_error': 'ONLINE_PUBLICATION_INVALID',
                   'auto_predict': {'enabled': False, 'state': 'UNAVAILABLE'},
                   'auto_train': {'mode': 'OFF', 'state': 'UNAVAILABLE'}})


def validate_catalog(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError('ONLINE_CATALOG_OBJECT_REQUIRED')
    checked_document(value)
    if (value.get('schema_version') != 'qtrade.online-catalog.v1'
            or any(not isinstance(value[k], list) or len(value[k]) > maximum for k, maximum in (
                ('models', 200), ('validations', 200), ('commands', 100), ('events', 200)))):
        raise ValueError('ONLINE_CATALOG_SCHEMA_INVALID')
    for key in ('published_at',):
        clock = datetime.fromisoformat(value[key])
        if clock.tzinfo is None or clock > datetime.now(UTC):
            raise ValueError('ONLINE_CATALOG_CLOCK_INVALID')
    heartbeat = datetime.fromisoformat(value['worker']['heartbeat_at'])
    if heartbeat.tzinfo is None or heartbeat > datetime.fromisoformat(value['published_at']):
        raise ValueError('ONLINE_WORKER_CLOCK_INVALID')
    seen = set()
    active = []
    for row in value['models']:
        definition = row['definition']
        checked_document(definition)
        identity = checked_identity(definition['model_id'])
        if (identity in seen or row['state'] not in STATES
                or definition['schema_version'] != 'qtrade.online-model.v1'
                or definition['feature_count'] != 158
                or definition['historical_pit_certified'] is not False
                or definition['fitted_restore_verified'] is not True):
            raise ValueError('ONLINE_MODEL_IDENTITY_OR_STATE_INVALID')
        seen.add(identity)
        if row['state'] == 'ACTIVE':
            active.append(identity)
    if active != ([] if value['active_model'] is None else [value['active_model']]):
        raise ValueError('ONLINE_ACTIVE_MODEL_INCONSISTENT')
    data = value['data_version']
    if data is not None:
        checked_identity(data['version_id'])
        if 'data_event_id' in data:
            checked_identity(data['data_event_id'])
        date.fromisoformat(data['actual_end'])
        if (data['historical_pit_certified'] is not False
                or data['external_execution_ready'] is not False
                or not 1 <= data['current_symbols'] <= data['historical_symbols'] <= 6000
                or len(set(data['blocked_symbols'])) != (
                    data['historical_symbols']-data['current_symbols'])):
            raise ValueError('ONLINE_DATA_SCOPE_INVALID')
    validation_ids = set()
    for proof in value['validations']:
        if proof['model_bundle_sha256'] not in seen or not 1 <= proof['score_count'] <= 6000:
            raise ValueError('ONLINE_VALIDATION_SCOPE_INVALID')
        identity = checked_identity(proof['validation_id'])
        generated = datetime.fromisoformat(proof['contract']['generated_at'])
        execute = datetime.fromisoformat(proof['contract']['expected_execution_at'])
        if (identity in validation_ids or generated.tzinfo is None or execute.tzinfo is None
                or not generated < execute or generated > datetime.fromisoformat(
                    value['published_at']) or proof['feature_count'] != 158
                or proof['state'] != 'FORWARD_INPUT_VALIDATED_NOT_ACTIVATED'):
            raise ValueError('ONLINE_VALIDATION_CONTRACT_INVALID')
        validation_ids.add(identity)
        checked_identity(proof['contract']['data_version_id'])
        if (data is not None and proof['contract']['data_version_id'] == data['version_id']
                and proof['contract']['data_end'] != data['actual_end']):
            raise ValueError('ONLINE_VALIDATION_DATA_END_MISMATCH')
        if proof['contract']['forward_return_observed'] is not False:
            raise ValueError('ONLINE_VALIDATION_NOT_OBSERVED_FORWARD_RETURN')
    for command in value['commands']:
        checked_document(command)
        checked_identity(command['command_sha256'])
        if command['state'] not in {'APPLIED', 'REJECTED'}:
            raise ValueError('ONLINE_COMMAND_STATE_INVALID')
    prediction = value.get('auto_predict', {})
    if prediction.get('enabled') is True:
        if (prediction.get('schema_version') != 'qtrade.auto-prediction.v1'
                or prediction.get('forward_return_observed') is not False
                or not isinstance(prediction['predictions'], list)
                or len(prediction['predictions']) > 50):
            raise ValueError('ONLINE_PREDICTION_SCHEMA_INVALID')
        identities = set()
        current = None
        for row in prediction['predictions']:
            bindings = row['bindings']
            identity = checked_identity(row['prediction_id'])
            if (identity in identities or identity != signed(bindings)['evidence_sha256']
                    or row['state'] not in {'QUEUED', 'RUNNING', 'INTERRUPTED', 'FAILED',
                                            'COMPLETE', 'SUPERSEDED'}):
                raise ValueError('ONLINE_PREDICTION_IDENTITY_INVALID')
            identities.add(identity)
            if row['state'] in {'COMPLETE', 'SUPERSEDED'}:
                contract = row['contract']
                checked_document(contract)
                generated = datetime.fromisoformat(contract['generated_at'])
                execute = datetime.fromisoformat(contract['expected_execution_at'])
                if (contract['data_version_id'] != bindings['data_version_id']
                        or contract['model_bundle_sha256'] != bindings['model_id']
                        or contract['data_end'] != bindings['data_end']
                        or contract['forward_return_observed'] is not False
                        or generated.tzinfo is None or execute.tzinfo is None
                        or not generated < execute or generated > datetime.fromisoformat(
                            value['published_at'])
                        or row['feature_count'] != 158 or not 1 <= row['score_count'] <= 6000):
                    raise ValueError('ONLINE_PREDICTION_CONTRACT_INVALID')
            if identity == prediction['current_prediction_id']:
                current = row
        if prediction['current_prediction_id'] is not None and (
                current is None or current['state'] != 'COMPLETE' or data is None
                or current['bindings']['data_version_id'] != data['version_id']
                or current['bindings']['model_id'] != value['active_model']
                or current['bindings']['code_revision'] != value['worker']['source_revision']
                or datetime.fromisoformat(current['contract']['expected_execution_at']) <= (
                    datetime.fromisoformat(value['published_at']))):
            raise ValueError('ONLINE_CURRENT_PREDICTION_INCONSISTENT')
    training = value.get('auto_train', {})
    if training.get('state') == 'CONTROL_READY':
        validate_policy(training['policy'])
        checked_identity(training['policy_id'])
        if (training.get('mode') != training['policy']['mode']
                or training.get('automatic_activation') is not False
                or not isinstance(training['receipts'], list) or len(training['receipts']) > 50
                or not isinstance(training['tasks'], list) or len(training['tasks']) > 50
                or not isinstance(training.get('validation_tasks', []), list)
                or len(training.get('validation_tasks', [])) > 50):
            raise ValueError('ONLINE_ROLLING_CONTROL_INVALID')
        for receipt in training['receipts']:
            checked_document(receipt)
    return value


def listing() -> dict[str, Any]:
    return validate_catalog(read_public('online.json', maximum=3_000_000))


def worker_fresh(value: dict[str, Any]) -> bool:
    clock = datetime.fromisoformat(value['worker']['heartbeat_at'])
    return (clock.tzinfo is not None and value['worker']['state'] in {
                'MODEL_CONTROL_READY', 'PREDICTING', 'RUNNING'}
            and 0 <= (datetime.now(UTC)-clock).total_seconds() <= 720)


def data_schedule_fresh(value: dict[str, Any]) -> bool:
    """Recheck at read time; a cached publication cannot renew a process heartbeat."""
    schedule = value.get('data_schedule', {})
    try:
        clock = datetime.fromisoformat(schedule['heartbeat_at'])
        return bool(schedule.get('fresh') is True and clock.tzinfo is not None
                    and 0 <= (datetime.now(UTC)-clock).total_seconds() <= 720)
    except (KeyError, TypeError, ValueError):
        return False


@router.get('/catalog')
def catalog(response: Response) -> dict[str, Any]:
    response.headers['Cache-Control'] = 'no-store'
    try:
        value = listing()
        fresh = worker_fresh(value)
        expected_day = research_clock()
        prediction = value.get('auto_predict', {})
        current = next((p for p in prediction.get('predictions', [])
                        if p['prediction_id'] == prediction.get('current_prediction_id')), None)
        usable = bool(fresh and current and value['data_version']
                      and value['data_version']['actual_end'] == expected_day
                      and (not value['data_version'].get('data_event_id') or
                           current['bindings']['data_event_id'] ==
                           value['data_version']['data_event_id'])
                      and datetime.fromisoformat(current['contract']['expected_execution_at']) > (
                          datetime.now(UTC)))
        return {**value, 'publication_available': True, 'worker_fresh': fresh,
                'data_schedule_fresh': data_schedule_fresh(value),
                'current_prediction_usable': usable, 'expected_complete_day': expected_day}
    except Exception:
        return {**AUTHORITY, 'publication_available': False, 'worker_fresh': False,
                'models': [], 'validations': [], 'commands': [], 'events': [],
                'active_model': None, 'data_version': None,
                'expected_complete_day': research_clock()}


@router.get('/etf-trend')
def etf_trend(response: Response) -> dict[str, Any]:
    from qtrade.research.etf_trend import validate_etf_trend_publication

    response.headers['Cache-Control'] = 'no-store'
    try:
        result = validate_etf_trend_publication(read_public('etf-trend.json', maximum=300_000))
        return {**AUTHORITY, 'publication_available': True,
                'current_input': result['as_of'] == research_clock(), 'diagnostic': result}
    except (OSError, ValueError, KeyError, TypeError):
        return {**AUTHORITY, 'publication_available': False,
                'current_input': False, 'diagnostic': None}


@router.get('/forward-books')
def forward_books(response: Response) -> dict[str, Any]:
    from qtrade.research.forward_publication import validate_forward_publication

    response.headers['Cache-Control'] = 'no-store'
    try:
        result = validate_forward_publication(read_public('forward.json', maximum=2_000_000))
        stamp = datetime.fromisoformat(result['heartbeat_at'])
        fresh = result['state'] != 'STOPPED' and 0 <= (
            datetime.now(UTC)-stamp).total_seconds() <= 720
        return {**AUTHORITY, 'publication_available': True,
                'service_fresh': fresh, 'publication': result}
    except (OSError, ValueError, KeyError, TypeError):
        return {**AUTHORITY, 'publication_available': False,
                'service_fresh': False, 'publication': None}


@router.get('/etf-forward')
def etf_forward(response: Response) -> dict[str, Any]:
    from qtrade.research.etf_shadow_publication import validate_etf_shadow_publication

    response.headers['Cache-Control'] = 'no-store'
    try:
        now = datetime.now(UTC)
        result = validate_etf_shadow_publication(
            read_public('etf-forward.json', maximum=1_000_000), now=now)
        stamp = datetime.fromisoformat(result['heartbeat_at'])
        fresh = result['state'] not in {'STOPPED', 'FAILED'} and 0 <= (
            now-stamp).total_seconds() <= 720
        current = fresh and any(
            row['target']['bindings']['as_of'] == research_clock()
            and datetime.fromisoformat(row['target']['bindings']['expected_execution_at']) > now
            for row in result['entries'])
        return {**AUTHORITY, 'publication_available': True,
                'service_fresh': fresh, 'current_target': current, 'publication': result}
    except (OSError, ValueError, KeyError, TypeError):
        return {**AUTHORITY, 'publication_available': False,
                'service_fresh': False, 'current_target': False, 'publication': None}


@router.get('/strategy-comparison')
def strategy_comparison(response: Response) -> dict[str, Any]:
    from qtrade.research.strategy_comparison import validate_comparison_publication

    response.headers['Cache-Control'] = 'no-store'
    try:
        result = validate_comparison_publication(read_public('comparison.json', maximum=3_000_000))
        return {**AUTHORITY, 'publication_available': True, 'comparison': result}
    except (OSError, ValueError, KeyError, TypeError):
        return {**AUTHORITY, 'publication_available': False, 'comparison': None}


@router.get('/etf-history')
def etf_history(response: Response) -> dict[str, Any]:
    from qtrade.research.etf_history_publication import validate_etf_history_publication

    response.headers['Cache-Control'] = 'no-store'
    try:
        result = validate_etf_history_publication(
            read_public('etf-history.json', maximum=3_000_000))
        return {**AUTHORITY, 'publication_available': True, 'comparison': result}
    except (OSError, ValueError, KeyError, TypeError):
        return {**AUTHORITY, 'publication_available': False, 'comparison': None}


@router.get('/data-versions')
def data_versions(response: Response) -> dict[str, Any]:
    response.headers['Cache-Control'] = 'no-store'
    try:
        value = listing()
        data = validate_public_catalog(value['data_catalog'])
        return {**AUTHORITY, 'publication_available': True,
                'worker_fresh': worker_fresh(value), 'catalog': data}
    except (ValueError, KeyError, TypeError, OSError):
        return {**AUTHORITY, 'publication_available': False,
                'worker_fresh': False, 'catalog': None}


@router.post('/data-rollback', status_code=202)
async def data_rollback(request: Request) -> dict[str, Any]:
    user = actor(request)
    body = await _body(request, 4096)
    try:
        if 'actor' in body:
            raise ValueError('CALLER_CANNOT_SET_ACTOR')
        command = data_command({**body, 'actor': user})
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, '数据回退需要明确版本、当前版本事件及允许的原因') from None
    try:
        value = listing()
        versions = validate_public_catalog(value['data_catalog'])
        if not worker_fresh(value):
            raise ValueError('DATA_CONTROL_WORKER_STALE')
    except (ValueError, KeyError, TypeError, OSError):
        raise HTTPException(503, '数据版本控制未就绪，请等待最新发布') from None
    prior = next((r for r in versions['receipts']
                  if r['request_id'] == command['request_id']), None)
    if prior is not None:
        if prior['command_sha256'] != signed(command)['evidence_sha256']:
            raise HTTPException(409, '请求标识已绑定另一项数据操作')
        return dict(prior)
    if (versions['head_version_id'] != command['expected_head']
            or versions['head_event_id'] != command['expected_event_id']
            or not any(v['version_id'] == command['version_id'] for v in versions['versions'])):
        raise HTTPException(409, '版本或事件已变化，请刷新后重新审阅')
    try:
        GitQueue.from_environment().append('requests', command['request_id'], seal({
            'job_id': command['request_id'], 'kind': 'QLIB_DATA_ROLLBACK',
            'command': command, 'live_trading_enabled': False,
        }, os.environ['QTRADE_RESEARCH_QUEUE_SECRET']))
    except Exception as error:
        _queue_failure('data-rollback', error)
        raise HTTPException(503, '回退未获提交确认；请使用原请求标识重试') from None
    return {**AUTHORITY, 'request_id': command['request_id'], 'state': 'QUEUED'}


@router.post('/training-policy', status_code=202)
async def training_policy(request: Request) -> dict[str, Any]:
    user = actor(request)
    body = await _body(request)
    try:
        if 'actor' in body:
            raise ValueError('CALLER_CANNOT_SET_ACTOR')
        command = rolling_command({**body, 'actor': user})
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, '训练政策只接受受控模型、会话间隔和窗口') from None
    try:
        value = listing()
        training = value['auto_train']
        if not worker_fresh(value) or training['state'] != 'CONTROL_READY':
            raise ValueError('ROLLING_CONTROL_UNAVAILABLE')
        validate_policy(training['policy'])
        checked_identity(training['policy_id'])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(503, '滚动训练控制未就绪') from None
    prior = next((r for r in training['receipts']
                  if r['request_id'] == command['request_id']), None)
    if prior is not None:
        if prior['command_sha256'] != signed(command)['evidence_sha256']:
            raise HTTPException(409, '请求标识已绑定另一项训练政策')
        return dict(prior)
    if training['policy_id'] != command['expected_policy_id']:
        raise HTTPException(409, '训练政策已变化，请重新审阅')
    try:
        GitQueue.from_environment().append('requests', command['request_id'], seal({
            'job_id': command['request_id'], 'kind': 'QLIB_ROLLING_POLICY',
            'command': command, 'live_trading_enabled': False,
        }, os.environ['QTRADE_RESEARCH_QUEUE_SECRET']))
    except Exception as error:
        _queue_failure('rolling-policy', error)
        raise HTTPException(503, '政策未获提交确认，请用原请求标识重试') from None
    return {**AUTHORITY, 'request_id': command['request_id'], 'state': 'QUEUED'}


@router.post('/actions', status_code=202)
async def action(request: Request) -> dict[str, Any]:
    user = actor(request)
    body = await _body(request)
    try:
        command = control_command({**body, 'actor': user})
        if 'actor' in body:
            raise ValueError('CALLER_CANNOT_SET_ACTOR')
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, '模型操作只接受指定模型、数据、验证结果和幂等标识') from None
    try:
        value = listing()
        if not worker_fresh(value):
            raise ValueError('WORKER_STALE')
    except Exception:
        raise HTTPException(503, '模型控制执行器未就绪，未提交操作') from None
    previous: dict[str, Any] | None = next((r for r in value['commands']
                     if r['request_id'] == command['request_id']), None)
    if previous:
        if previous['command_sha256'] != signed(command)['evidence_sha256']:
            raise HTTPException(409, '请求标识已绑定另一项模型操作')
        return previous
    data = value['data_version']
    if (data is None or data['version_id'] != command['data_version_id']
            or value['active_model'] != command['expected_active']
            or research_clock() != data['actual_end']):
        raise HTTPException(409, '当前模型或数据已改变、过期，请刷新后重新审阅')
    model = next((r for r in value['models']
                  if r['definition']['model_id'] == command['model_id']), None)
    proof = next((r for r in value['validations']
                  if r['validation_id'] == command['validation_id']), None)
    expected = 'STAGED' if command['operation'] == 'ACTIVATE' else 'RETIRED'
    if (model is None or model['state'] != expected or proof is None
            or proof['model_bundle_sha256'] != command['model_id']
            or proof['contract']['data_version_id'] != data['version_id']
            or datetime.fromisoformat(proof['contract']['expected_execution_at']) <= (
                datetime.now(UTC))):
        raise HTTPException(409, '候选状态或最新输入验证不满足本次操作条件')
    try:
        GitQueue.from_environment().append('requests', command['request_id'], seal({
            'job_id': command['request_id'], 'kind': 'QLIB_MODEL_CONTROL',
            'command': command, 'live_trading_enabled': False,
        }, os.environ['QTRADE_RESEARCH_QUEUE_SECRET']))
    except Exception as error:
        _queue_failure('model-control', error)
        raise HTTPException(503, '模型操作未获提交确认；请使用原请求标识重试') from None
    return {**AUTHORITY, 'request_id': command['request_id'], 'state': 'QUEUED'}
