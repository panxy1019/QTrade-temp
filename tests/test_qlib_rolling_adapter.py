from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import qlib_rolling_adapter as adapter


def test_native_trainer_reload_initializes_handler_without_refitting(monkeypatch):
    class Handler:
        IT_LS = 'load_state'
        fit = Mock()
        fit_process_data = Mock()

    data = SimpleNamespace(setup_data=Mock())
    model = object()
    recorder = SimpleNamespace(id='native-recorder', load_object=lambda key: (
        model if key == 'params.pkl' else data))
    trainer = SimpleNamespace(train=Mock(return_value=[recorder]), end_train=Mock())
    monkeypatch.setattr(adapter, 'import_module', lambda name: SimpleNamespace(
        TrainerR=lambda **kw: trainer, DataHandlerLP=Handler))
    monkeypatch.setattr('scripts.qlib_restore_bundle.processor_hash', lambda ds: 'unchanged')

    def setup(**kwargs):
        assert kwargs == {'handler_kwargs': {'init_type': Handler.IT_LS}}
        with pytest.raises(RuntimeError, match='REFIT_FORBIDDEN'):
            Handler.fit()
        with pytest.raises(RuntimeError, match='REFIT_FORBIDDEN'):
            Handler.fit_process_data()

    data.setup_data.side_effect = setup
    assert adapter.train({'allowed': 'task'}, experiment='test', name='run') == (
        model, data, 'native-recorder')
    trainer.train.assert_called_once_with([{'allowed': 'task'}])
    trainer.end_train.assert_called_once_with([recorder])
    data.setup_data.assert_called_once()
