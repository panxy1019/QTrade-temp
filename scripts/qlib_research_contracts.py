"""Version-bound Qlib research adapter; no model activation or execution authority."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qtrade.research.data_versions import file_hash
from qtrade.research.strategy_contract import (
    bound_default_spec,
    freeze_strategy,
    ready_dataset,
    register_strategy,
    strategy_choices,
    supported_universe,
    verify_bound_spec,
)
from qtrade.research.workflow import data_audit
from qtrade.strategies.registry import StrategyRegistry


class ResearchContracts:
    def __init__(self, base: Path, database: Path, revision: str,
                 registry: StrategyRegistry, workflow_database: Path | None = None):
        self.base, self.database, self.revision, self.registry = base, database, revision, registry
        self.workflow_database = workflow_database
        self.cache: dict[str, tuple[str, dict[str, Any]]] = {}

    def root(self, identity: str) -> Path:
        import re
        if not isinstance(identity, str) or re.fullmatch('[a-f0-9]{64}', identity) is None:
            raise ValueError('RESEARCH_DATA_IDENTITY_INVALID')
        return self.base/'unified-data/qlib-versions/versions'/identity

    def verify(self, request: dict[str, Any], *, register: bool = False) -> dict[str, Any]:
        spec = request['spec']
        root = self.root(spec['data_binding']['data_version_id'])
        spec = verify_bound_spec(spec, root, self.database)
        definition = freeze_strategy(spec, self.revision)
        if request.get('expected_strategy_version') != definition['strategy_version']:
            raise ValueError('RESEARCH_EXPECTED_STRATEGY_VERSION_CHANGED')
        if register:
            register_strategy(self.registry, definition, job_id=request['job_id'],
                actor=request['actor'], universe=supported_universe(root), now=datetime.now(UTC))
        return definition

    def publication(self) -> dict[str, Any]:
        """Cache full integrity checks of immutable archives, not execution admission.

        Every job still verifies all files independently. A publication reports
        the descriptors already verified in this process; it does not certify
        that every archive file was reread at each five-second heartbeat.
        """
        datasets, choices, errors = [], [], []
        if self.database.is_file():
            with closing(sqlite3.connect(self.database.resolve().as_uri()+'?mode=ro',
                                         uri=True, timeout=3)) as connection:
                rows = connection.execute('SELECT version_id,manifest_sha256,'
                    'actual_end,admitted_at '
                    'FROM data_versions ORDER BY admitted_at DESC LIMIT 10').fetchall()
            for row in rows:
                identity = row[0]
                try:
                    root = self.root(identity)
                    # New/changed admission metadata or manifest bytes invalidate
                    # the cache. Actual execution never relies on this cache.
                    with closing(sqlite3.connect(self.database.resolve().as_uri()+'?mode=ro',
                                                 uri=True, timeout=3)) as connection:
                        events = connection.execute('SELECT event_id,payload FROM data_events '
                            "WHERE version_id=? AND kind='QLIB_DATA_READY' "
                            'ORDER BY sequence LIMIT 2',
                            (identity,)).fetchall()
                    key = json.dumps([row, events, file_hash(root/'version.json')], sort_keys=True)
                    if identity not in self.cache or self.cache[identity][0] != key:
                        binding = ready_dataset(root, self.database)
                        self.cache[identity] = (key, binding)
                    binding = self.cache[identity][1]
                    document = json.loads((root/'dataset.json').read_bytes())
                    datasets.append({'id': binding['dataset_id'],
                        'symbols': document['accepted_symbols'],
                        'rows': document.get('rows', 0), 'start': binding['start'],
                        'end': binding['actual_end'], 'status': 'IMMUTABLE_READY_RESEARCH_VERSION',
                        'data_binding': binding, 'audit': data_audit()})
                    choices.extend(strategy_choices(binding, self.revision))
                except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
                    errors.append({'data_version_id': identity,
                                   'error': 'READY_RESEARCH_DESCRIPTOR_VERIFICATION_FAILED'})
        registered = []
        for record in self.registry.list(family='QLIB_ALPHA158')[-100:]:
            contract = self.registry.research_contract(record.spec.strategy_id)
            if contract is None:
                continue
            if all(row['strategy_version'] != contract['strategy_version'] for row in choices):
                choices.append(contract)
            lineage = self.registry.lineage(record.spec.strategy_id)
            jobs = self.registry.research_jobs(record.spec.strategy_id)
            artifacts = []
            if self.workflow_database is not None and self.workflow_database.is_file():
                with closing(sqlite3.connect(self.workflow_database.resolve().as_uri()+'?mode=ro',
                                             uri=True, timeout=3)) as connection:
                    for job in jobs:
                        row = connection.execute('SELECT state,result FROM jobs WHERE id=?',
                                                 (job['job_id'],)).fetchone()
                        if row and row[0] == 'COMPLETE' and row[1]:
                            job_result = json.loads(row[1])
                            if job_result.get('model_artifact_id'):
                                artifacts.append({'job_id': job['job_id'],
                                    'model_id': job_result['model_artifact_id'],
                                    'run_id': job_result['run_id'],
                                    'data_version_id': contract['spec']['data_binding'][
                                        'data_version_id'], 'source_revision': job_result[
                                        'source_revision']})
            registered.append({'strategy_id': record.spec.strategy_id, 'name': record.spec.name,
                'family': record.spec.family, 'version': record.spec.version,
                'state': record.status.value, 'contract': contract,
                'job_ids': [j['job_id'] for j in self.registry.research_jobs(
                    record.spec.strategy_id)],
                'model_artifacts': artifacts,
                'lineage': ({**asdict(lineage), 'created_at': lineage.created_at.isoformat()}
                            if lineage else None)})
        result: dict[str, Any] = {'datasets': datasets,
                  'strategy_versions': choices, 'strategies': registered,
                  'dataset_errors': errors}
        if datasets:
            result.update(default_spec=bound_default_spec(datasets[0]['data_binding']),
                default_linear_spec=bound_default_spec(datasets[0]['data_binding'], 'linear'))
        return result
