from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from qtrade.research.experiment import TimeSplit


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    train_sessions: int
    validation_sessions: int
    test_sessions: int
    step_sessions: int
    expanding_train: bool = False

    def __post_init__(self) -> None:
        values = (
            self.train_sessions,
            self.validation_sessions,
            self.test_sessions,
            self.step_sessions,
        )
        if any(value <= 0 for value in values):
            raise ValueError("walk-forward session counts must be positive")


def build_walk_forward_splits(
    sessions: list[date], config: WalkForwardConfig
) -> tuple[TimeSplit, ...]:
    if sessions != sorted(set(sessions)):
        raise ValueError("walk-forward sessions must be sorted and unique")
    required = config.train_sessions + config.validation_sessions + config.test_sessions
    splits: list[TimeSplit] = []
    window_start = 0
    while window_start + required <= len(sessions):
        train_start_index = 0 if config.expanding_train else window_start
        train_end_index = window_start + config.train_sessions - 1
        validation_start_index = train_end_index + 1
        validation_end_index = validation_start_index + config.validation_sessions - 1
        test_start_index = validation_end_index + 1
        test_end_index = test_start_index + config.test_sessions - 1
        splits.append(
            TimeSplit(
                sessions[train_start_index],
                sessions[train_end_index],
                sessions[validation_start_index],
                sessions[validation_end_index],
                sessions[test_start_index],
                sessions[test_end_index],
            )
        )
        window_start += config.step_sessions
    if not splits:
        raise ValueError("insufficient sessions for one walk-forward split")
    return tuple(splits)
