from __future__ import annotations

import json
import shutil
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_qlib_strategy_contract import admitted as admitted
from test_qlib_workflow import client as client

from qtrade.api import research_workflow as api
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.strategy_contract import bound_default_spec, freeze_strategy, ready_dataset
from qtrade.research.workflow import SCHEMA, build_config, digest
from qtrade.strategies.registry import StrategyRegistry
from scripts.qlib_research_contracts import ResearchContracts
from scripts.run_qlib_baseline import bound_training_definition

REVISION = 'a'*40


@pytest.fixture
def inputs(admitted, tmp_path):
    old, database, _ = admitted
    base = tmp_path/'cluster'
    root = base/'unified-data/qlib-versions/versions'/old.name
    shutil.copytree(old, root)
    contracts = ResearchContracts(base, database, REVISION,
                                  StrategyRegistry(tmp_path/'strategies.sqlite'))
    binding = ready_dataset(root, database)
    spec = bound_default_spec(binding)
    definition = freeze_strategy(spec, REVISION)
    listing = {**AUTHORITY, 'schema_version': SCHEMA,
        'worker': {'heartbeat_at': datetime.now(UTC).isoformat(), 'source_revision': REVISION},
        'experiments': [], **contracts.publication()}
    return contracts, root, spec, definition, listing


def test_bound_preview_and_submission_freeze_exact_worker_version(client, inputs, monkeypatch):
    http, password = client
    _, _, spec, definition, listing = inputs
    monkeypatch.setattr(api, 'read_public', lambda *_: listing)
    captured = []
    class Queue:
        def append(self, *args):
            captured.append(args)
    monkeypatch.setattr(api.GitQueue, 'from_environment', lambda: Queue())
    assert http.post('/research/specification', json={'spec': spec}).status_code == 401
    http.post('/research/login', json={'user': 'research-test', 'password': password})
    response = http.post('/research/specification', json={'spec': spec})
    assert response.status_code == 200, response.text
    assert response.json()['strategy_definition'] == definition
    assert response.json()['expected_strategy_version'] == definition['strategy_version']
    assert response.headers['cache-control'] == 'no-store'
    body = {'spec': spec, 'request_id': 'b'*32,
            'expected_strategy_version': definition['strategy_version']}
    response = http.post('/research/jobs', json=body)
    assert response.status_code == 202, response.text
    envelope = captured[-1][2]
    assert envelope['expected_strategy_version'] == definition['strategy_version']
    assert envelope['spec_sha256'] == digest(spec)
    assert envelope['spec']['data_binding'] == spec['data_binding']
    body['spec'] = {**spec, 'topk': 40}
    assert http.post('/research/jobs', json=body).status_code == 409
    assert len(captured) == 1


def test_api_rejects_unknown_data_stale_or_different_builder(client, inputs, monkeypatch):
    http, password = client
    _, _, spec, _, listing = inputs
    http.post('/research/login', json={'user': 'research-test', 'password': password})
    monkeypatch.setattr(api, 'read_public', lambda *_: listing)
    forged = deepcopy(spec)
    binding = forged['data_binding']
    binding['manifest_sha256'] = 'f'*64
    forged['data_binding'] = signed({k: v for k, v in binding.items()
                                     if k != 'evidence_sha256'})
    assert http.post('/research/specification', json={'spec': forged}).status_code == 422
    listing['worker']['source_revision'] = 'd'*40
    assert http.post('/research/specification', json={'spec': spec}).status_code == 503
    listing['worker']['source_revision'] = REVISION
    listing['worker']['heartbeat_at'] = '2020-01-01T00:00:00+00:00'
    assert http.post('/research/specification', json={'spec': spec}).status_code == 503


def test_catalog_registration_and_replay_have_one_lifecycle_and_exact_job(inputs):
    contracts, _, spec, definition, listing = inputs
    assert len(listing['datasets']) == 1 and len(listing['strategy_versions']) == 2
    assert listing['default_spec']['data_binding'] == spec['data_binding']
    request = {'spec': spec, 'job_id': 'b'*32, 'actor': 'research-test',
               'expected_strategy_version': definition['strategy_version']}
    assert contracts.verify(request, register=True) == definition
    assert contracts.verify(request, register=True) == definition
    row, = contracts.publication()['strategies']
    assert row['state'] == 'EXPERIMENT'
    assert row['job_ids'] == ['b'*32] and row['contract'] == definition
    assert row['contract']['model_activation'] is False
    assert row['contract']['paper_admission'] is False


def test_execution_rechecks_files_even_when_catalog_descriptor_was_cached(inputs):
    contracts, root, spec, definition, _ = inputs
    request = {'spec': spec, 'expected_strategy_version': definition['strategy_version']}
    with (root/'dataset.json').open('ab') as handle:
        handle.write(b'\n')
    # The documented immutable-descriptor cache is not execution authorization.
    assert contracts.publication()['datasets']
    with pytest.raises(ValueError, match='IDENTITY'):
        contracts.verify(request)
    assert contracts.publication()['strategies'] == []


def test_runner_independently_gates_bound_identity_restore_and_data(inputs):
    contracts, root, spec, definition, _ = inputs
    config = build_config(spec, json.loads(Path('configs/qlib_daily_baseline.json').read_bytes()))
    kwargs = {'restore_proof': True, 'pit_evidence_root': root/'pit-sources'}
    assert bound_training_definition(root, config, REVISION, contracts.database,
        definition['strategy_version'], **kwargs) == definition
    with pytest.raises(ValueError, match='EXPECTED_STRATEGY'):
        bound_training_definition(root, config, 'c'*40, contracts.database,
                                  definition['strategy_version'], **kwargs)
    with pytest.raises(ValueError, match='RESTORE_AND_SOURCE'):
        bound_training_definition(root, config, REVISION, contracts.database,
            definition['strategy_version'], restore_proof=False,
            pit_evidence_root=root/'pit-sources')
    with pytest.raises(ValueError, match='IDENTITY'):
        contracts.root('../../other')
