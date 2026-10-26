import json
import os
from datetime import timedelta

import pytest
from test_forward_prediction import inputs, snapshot  # noqa: F401
from test_model_registry import NOW
from test_prediction_registry import binding, ledger  # noqa: F401

from qtrade.monitoring.data_freshness import signed
from qtrade.research.data_versions import file_hash
from scripts import run_qlib_prediction as runner


def resign(value):
    return signed({k: v for k, v in value.items() if k != 'evidence_sha256'})


@pytest.fixture
def artifact(ledger, snapshot, tmp_path, monkeypatch):  # noqa: F811
    source, calculation = snapshot
    b = binding(ledger)
    b.update(data_version_id='e'*64, observation_universe_version='d'*64)
    request = ledger.claim(b, now=NOW)
    request_file = tmp_path/'approved-request.json'
    request_file.write_text(json.dumps(request))
    calculation.update(model_bundle_sha256='a'*64, model_run_id='fixture',
                       max_reference_score_difference=0, limitations=['non-PIT'])
    calculation['contract']['generated_at'] = (NOW+timedelta(minutes=1)).isoformat()
    calculation['contract'] = resign(calculation['contract'])
    calculation = resign(calculation)
    (source/'manifest.json').write_text(json.dumps(calculation))
    calls = []

    def compute(archive, data_root, output):
        import shutil
        calls.append(output)
        shutil.copytree(source, output/calculation['validation_id'])
        return calculation
    monkeypatch.setattr(runner, 'validate', compute)
    if os.name == 'nt':
        def write(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))
        monkeypatch.setattr(runner, 'write_json_immutable', write)
        monkeypatch.setattr(runner, 'sync_directory', lambda p: None)
        monkeypatch.setattr(runner, 'move_durable', lambda a, b: a.rename(b))
        # Linux production fsync on read-only descriptors is verified there.
        monkeypatch.setattr(runner.os, 'fsync', lambda fd: None)
    output = tmp_path/'daily'
    value = runner.run(request_file, tmp_path, tmp_path, output, revision='c'*40)
    return output/request['prediction_id'], value, request_file, calls


def test_daily_result_seals_fresh_approval_and_full_subordinate_inventory(artifact):
    root, value, request, calls = artifact
    assert runner.verify_prediction(root) == value
    again = runner.run(request, root, root, root.parent, revision='c'*40)
    assert again == value and len(calls) == 1
    assert value['contract']['forward_return_observed'] is False


@pytest.mark.parametrize('change', ['score', 'extra', 'approval', 'preapproval', 'count'])
def test_artifact_tamper_and_old_validation_relabelling_are_rejected(artifact, change):
    root, value, request_file, _ = artifact
    if change == 'score':
        next((root/'calculation').rglob('scores.parquet')).write_bytes(b'changed')
    elif change == 'extra':
        (root/'unbound.json').write_text('{}')
    elif change in {'approval', 'preapproval'}:
        request = json.loads((root/'request.json').read_text())
        if change == 'approval':
            request['approval']['model_id'] = 'f'*64
            request['approval'] = resign(request['approval'])
        else:
            request['claimed_at'] = (NOW+timedelta(hours=1)).isoformat()
        request = resign(request)
        (root/'request.json').write_text(json.dumps(request))
        value['files']['request.json'] = file_hash(root/'request.json')
        value['request_sha256'] = request['evidence_sha256']
    else:
        value['score_count'] += 1
    (root/'manifest.json').write_text(json.dumps(resign(value)))
    with pytest.raises(ValueError):
        runner.verify_prediction(root)
