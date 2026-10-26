"""Fixed fit budget adapter; Qlib owns the actual LightGBM training algorithm."""

from importlib import import_module
from typing import Any

NativeLGBModel = import_module('qlib.contrib.model.gbdt').LGBModel


class LGBModel(NativeLGBModel):  # type: ignore[misc,valid-type]
    def fit(self, dataset: Any, reweighter: Any = None, **kwargs: Any) -> Any:
        # TrainerR calls fit(dataset, reweighter=...) without task-level fit kwargs.
        # Keep the existing reviewed budget instead of silently using Qlib's default.
        if kwargs:
            raise ValueError('ROLLING_FIT_OVERRIDE_FORBIDDEN')
        return super().fit(dataset, num_boost_round=300, early_stopping_rounds=30,
                           verbose_eval=50, reweighter=reweighter)
