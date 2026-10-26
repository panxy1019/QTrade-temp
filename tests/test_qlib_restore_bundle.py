import json
from copy import deepcopy
from hashlib import sha256
from unittest.mock import Mock

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts.qlib_restore_bundle import BUNDLE_FILES, FrozenRecorder, verify_bundle


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / 'bundle'
    provider = tmp_path / 'provider'
    root.mkdir()
    (provider / 'calendars').mkdir(parents=True)
    (provider / 'calendars/day.txt').write_text('2026-10-23\n')
    for name in BUNDLE_FILES:
        (root / name).write_bytes(b'not-unpickled-before-manifest-validation')
    value = signed({
        **AUTHORITY, 'schema_version': 'qtrade.qlib-restore-bundle.v1',
        'model_activated': False, 'historical_pit_certified': False,
        'files': {name: sha256((root/name).read_bytes()).hexdigest() for name in BUNDLE_FILES},
        'provider_files': {'calendars/day.txt': sha256(
            (provider/'calendars/day.txt').read_bytes()).hexdigest()},
        'feature_order': [f'F{i}' for i in range(158)],
    })
    (root / 'manifest.json').write_text(json.dumps(value))
    return root, provider, value


def test_verifier_checks_full_provider_and_preserves_local_artifacts(bundle):
    root, provider, value = bundle
    assert verify_bundle(root, provider) == value
    saved = (root / 'manifest.json').read_bytes()
    (provider / 'new-unbound-feature.day.bin').write_bytes(b'not bound')
    with pytest.raises(ValueError, match='UNBOUND'):
        verify_bundle(root, provider)
    assert (root / 'manifest.json').read_bytes() == saved


@pytest.mark.parametrize('change', ['hash', 'authority', 'model', 'dataset', 'provider',
                                   'path', 'features', 'activation', 'missing'])
def test_drift_fails_before_any_pickle_load(bundle, change, monkeypatch):
    root, provider, value = bundle
    loader = Mock(side_effect=AssertionError('must not deserialize'))
    monkeypatch.setattr('scripts.qlib_restore_bundle.pickle.load', loader)
    value = deepcopy(value)
    value.pop('evidence_sha256')
    if change in {'model', 'dataset'}:
        (root / ('params.pkl' if change == 'model' else 'dataset')).write_bytes(b'changed')
    elif change == 'provider':
        (provider / 'calendars/day.txt').write_text('2026-10-26\n')
    elif change == 'authority':
        value['live_trading_enabled'] = True
    elif change == 'activation':
        value['model_activated'] = True
    elif change == 'path':
        value['provider_files']['../bundle/params.pkl'] = value['files']['params.pkl']
    elif change == 'features':
        value['feature_order'][1] = value['feature_order'][0]
    elif change == 'missing':
        value['files'].pop('dataset')
    value = signed(value)
    if change == 'hash':
        value['feature_order'][0] = 'changed'
    (root / 'manifest.json').write_text(json.dumps(value))
    with pytest.raises(ValueError):
        verify_bundle(root, provider)
    loader.assert_not_called()


def test_recorder_cannot_load_unapproved_paths(bundle):
    root, _, _ = bundle
    with pytest.raises(ValueError, match='ALLOWLISTED'):
        FrozenRecorder(root).load_object('../outside')
