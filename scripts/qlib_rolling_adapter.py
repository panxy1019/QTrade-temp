"""Native RollingGen/TrainerR compatibility with an independently checked plan."""

from __future__ import annotations

from copy import deepcopy
from importlib import import_module
from importlib.metadata import version
from typing import Any
from unittest.mock import patch

from qtrade.research.rolling_policy import verify_plan


def generate(task: dict[str, Any], plan: dict[str, Any], calendar: list[str]) -> dict[str, Any]:
    if version('pyqlib') != '0.9.7':
        raise ValueError('ROLLING_QLIB_VERSION_NOT_AUDITED')
    verify_plan(plan, calendar)
    task = deepcopy(task)
    task['dataset']['kwargs']['segments'] = deepcopy(plan['segments'])
    handler = task['dataset']['kwargs']['handler']['kwargs']
    handler.update(fit_start_time=plan['segments']['train'][0],
                   fit_end_time=plan['segments']['train'][1], end_time=plan['data_end'])
    if task['model']['class'] == 'LGBModel':
        task['model']['module_path'] = 'qtrade.research.qlib_training'
        task['model']['kwargs']['num_threads'] = 2
    RollingGen = import_module('qlib.workflow.task.gen').RollingGen
    # One mature window per admitted event; recurring cadence is a separate policy.
    generated = RollingGen(step=plan['policy']['test_sessions'], rtype='sliding',
                           ds_extra_mod_func=None).generate(task)
    if len(generated) != 1:
        raise ValueError('ROLLING_UNEXPECTED_NATIVE_TASK_COUNT')
    result = generated[0]
    actual = {key: [str(day.date()) for day in pair]
              for key, pair in result['dataset']['kwargs']['segments'].items()}
    if actual != plan['segments']:
        raise ValueError('ROLLING_NATIVE_WINDOW_CHANGED')
    result['dataset']['kwargs']['segments'] = actual
    return dict(result)


def train(task: dict[str, Any], *, experiment: str, name: str) -> tuple[Any, Any, str]:
    TrainerR = import_module('qlib.model.trainer').TrainerR
    trainer = TrainerR(experiment_name=experiment, default_rec_name=name)
    recorders = trainer.train([task])
    trainer.end_train(recorders)
    if len(recorders) != 1:
        raise ValueError('ROLLING_RECORDER_COUNT_INVALID')
    recorder = recorders[0]
    model = recorder.load_object('params.pkl')
    data = recorder.load_object('dataset')
    from scripts.qlib_restore_bundle import processor_hash

    before = processor_hash(data)
    # Reload native TrainerR's fitted DatasetH without fitting its processors again.
    Handler = import_module('qlib.data.dataset.handler').DataHandlerLP
    with (patch.object(Handler, 'fit', side_effect=RuntimeError('ROLLING_RELOAD_REFIT_FORBIDDEN')),
          patch.object(Handler, 'fit_process_data', side_effect=RuntimeError(
              'ROLLING_RELOAD_REFIT_FORBIDDEN'))):
        data.setup_data(init_type=Handler.IT_LS)
    if processor_hash(data) != before:
        raise ValueError('ROLLING_TRAINERR_RELOAD_CHANGED_FITTED_STATE')
    return model, data, str(recorder.id)
