from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class TimeSplit:
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    test_start: date
    test_end: date

    def __post_init__(self) -> None:
        valid = (
            self.train_start
            <= self.train_end
            < self.validation_start
            <= self.validation_end
            < self.test_start
            <= self.test_end
        )
        if not valid:
            raise ValueError("experiment splits must be chronological and non-overlapping")


@dataclass(frozen=True, slots=True)
class ExperimentRecord:
    experiment_id: str
    git_commit: str
    data_version: str
    universe: tuple[str, ...]
    factor_set: tuple[str, ...]
    label: str
    split: TimeSplit
    model: str
    hyperparameters: dict[str, Any]
    seed: int
    transaction_cost: Decimal
    metrics: dict[str, Decimal]
    artifacts: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.experiment_id or not self.git_commit or not self.data_version:
            raise ValueError("experiment identity, commit, and data version are required")
        if self.transaction_cost < 0:
            raise ValueError("transaction cost cannot be negative")
