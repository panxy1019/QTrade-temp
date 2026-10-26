"""Stable numerical precision for Qlib feature matrices, without fitting."""

from __future__ import annotations

from typing import Any


def float64_features(frame: Any) -> Any:
    columns = [c for c in frame.columns if isinstance(c, tuple) and c[0] == 'feature']
    if len(columns) != 158 or len(set(columns)) != 158:
        raise ValueError('ALPHA158_FEATURE_PRECISION_CONTRACT_INVALID')
    return frame.astype({c: 'float64' for c in columns})
