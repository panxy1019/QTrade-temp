"""Optional Qlib-runtime processors; imported only by the pinned research worker."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from qtrade.research.feature_precision import float64_features

_Processor = import_module('qlib.data.dataset.processor').Processor


class Float64Features(_Processor):  # type: ignore[misc,valid-type]
    """Promote already-computed feature values; no learned state or new factor."""

    def __call__(self, df: Any) -> Any:
        return float64_features(df)
