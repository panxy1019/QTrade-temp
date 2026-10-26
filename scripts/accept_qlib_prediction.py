"""Real-model acceptance in an isolated TEST_ONLY ledger; never production approval."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qtrade.api.online_research import validate_catalog
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts.atomic_json import write_json_immutable
from scripts.online_model_control import OnlineModels
from scripts.online_prediction_worker import OnlinePredictor
from scripts.run_qlib_prediction import verify_prediction

REPO = Path(__file__).resolve().parents[1]


def production_state(database: Path) -> dict[str, Any]:
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True) as c:
        return {'active': c.execute('SELECT * FROM active_model').fetchall(),
                'commands': c.execute('SELECT count(*) FROM model_commands').fetchone()[0]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--qlib-python', required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--archive', type=Path, action='append', required=True)
    parser.add_argument('--test-only', action='store_true', required=True)
    args = parser.parse_args()
    if os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
        raise RuntimeError('LIVE_TRADING_MUST_REMAIN_DISABLED')
    if not 1 <= len(args.archive) <= 2:
        raise ValueError('BOUNDED_ACCEPTANCE_ARCHIVES_REQUIRED')
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')
    root = args.base/'online-research/prediction-acceptance'/stamp
    database = Path('/root/qtrade-local/prediction-acceptance')/stamp/'models.sqlite'
    production_database = Path('/root/qtrade-local/online-models.sqlite')
    before = production_state(production_database)
    online = OnlineModels(args.base, database, Path('/root/qtrade-local/qlib-versions.sqlite'))
    online.root = root/'control'
    online.root.mkdir(parents=True)
    predictor = OnlinePredictor(online, args.qlib_python, args.source_revision)
    predictor.tick()
    if predictor.state != 'WAITING_FOR_APPROVAL' or predictor.ledger.tasks():
        raise ValueError('UNAPPROVED_TEST_EXECUTION')
    data = online.data_head(verify=True)
    results: list[dict[str, Any]] = []
    try:
        for archive in args.archive:
            log_path = root/(archive.name+'.validation.log')
            with log_path.open('wb') as log:
                subprocess.run([args.qlib_python, '-m', 'scripts.validate_qlib_forward',
                    '--archive', str(archive), '--data-root', str(args.base/
                    'unified-data/qlib-versions/versions'/data['version_id']),
                    '--output', str(args.base/'online-research/forward-validation')],
                    cwd=REPO, env={**os.environ, 'PYTHONPATH': str(REPO/'src'),
                                   'OMP_NUM_THREADS': '2', 'LIVE_TRADING_ENABLED': 'false'},
                    stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900)
            validation = json.loads(log_path.read_text().strip().splitlines()[-1])
            model = online.import_candidate(archive, args.base/'online-research/forward-validation'/
                validation['validation_id'], now=datetime.now(UTC))
            command = {'request_id': signed({'stamp': stamp, 'model': model})[
                'evidence_sha256'][:32], 'operation': 'ACTIVATE', 'model_id': model,
                'expected_active': online.registry.snapshot()['active_model'],
                'data_version_id': data['version_id'], 'validation_id': validation['validation_id'],
                'actor': 'isolated-acceptance-test-only'}
            receipt = online.execute(command, now=datetime.now(UTC))
            if receipt['state'] != 'APPLIED':
                raise ValueError('ISOLATED_APPROVAL_NOT_APPLIED')
            predictor.tick()
            if predictor.child is None:
                raise ValueError('ISOLATED_PREDICTION_NOT_STARTED')
            identity = predictor.running
            deadline = time.monotonic()+660
            while predictor.child is not None and time.monotonic() < deadline:
                time.sleep(2)
                predictor.tick()
            if predictor.state != 'COMPLETE' or identity is None:
                raise ValueError('ISOLATED_PREDICTION_DID_NOT_COMPLETE')
            proof = verify_prediction(predictor.root/identity/'output'/identity)
            count = len(predictor.ledger.tasks())
            predictor.tick()
            if predictor.child is not None or len(predictor.ledger.tasks()) != count:
                raise ValueError('ISOLATED_EVENT_REPLAY_LAUNCHED_DUPLICATE')
            public = online.publication(args.source_revision, now=datetime.now(UTC))
            public['auto_predict'] = predictor.publication(now=datetime.now(UTC))
            public = signed({k: v for k, v in public.items() if k != 'evidence_sha256'})
            validate_catalog(public)
            if public['auto_predict']['current_prediction_id'] != identity:
                raise ValueError('ISOLATED_PUBLIC_CURRENT_PREDICTION_MISMATCH')
            results.append({'model_id': model, 'validation_id': validation['validation_id'],
                'prediction_id': identity, 'contract': proof['contract'],
                'score_count': proof['score_count'], 'verified_files': len(proof['files']),
                'prediction_manifest_sha256': proof['evidence_sha256']})
    finally:
        predictor.tick(stop=True)
    after = production_state(production_database)
    if before != after:
        raise ValueError('PRODUCTION_MODEL_STATE_CHANGED_DURING_ACCEPTANCE')
    evidence = signed({**AUTHORITY, 'state': 'ISOLATED_REAL_MODEL_PREDICTION_ACCEPTED',
        'observed_at': datetime.now(UTC).isoformat(), 'source_revision': args.source_revision,
        'production_approval': False, 'isolated_database': str(database),
        'production_model_state_unchanged': True, 'production_before_after': before,
        'data_version_id': data['version_id'], 'results': results,
        'limitations': ['TEST_ONLY approval in separate ledger; production is unchanged',
                        'No forward return, full PIT or trading readiness certified']})
    write_json_immutable(root/'acceptance.json', evidence)
    print(json.dumps({'proof': str(root/'acceptance.json'), 'state': evidence['state']}))


if __name__ == '__main__':
    main()
