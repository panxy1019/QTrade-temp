from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from qtrade.data import DataManifest, QualityStatus
from qtrade.persistence import ExperimentCatalogEntry, SQLiteMetadataCatalog
from qtrade.research.artifacts import ExperimentArtifactStore
from qtrade.research.experiment import ExperimentRecord, TimeSplit


@dataclass(frozen=True, slots=True)
class ExperimentRunSpec:
    experiment_id: str
    strategy_id: str
    git_commit: str
    data_version: str
    universe: tuple[str, ...]
    factor_set: tuple[str, ...]
    label: str
    split: TimeSplit
    model: str
    parameters: tuple[tuple[str, str], ...]
    cost_model_id: str
    transaction_cost: Decimal
    seed: int

    def __post_init__(self) -> None:
        required = (
            self.experiment_id,
            self.strategy_id,
            self.git_commit,
            self.data_version,
            self.label,
            self.model,
            self.cost_model_id,
        )
        if not all(value.strip() for value in required) or not self.universe:
            raise ValueError("experiment run identity and universe are required")
        if self.transaction_cost < 0 or not self.transaction_cost.is_finite():
            raise ValueError("experiment transaction cost must be finite and nonnegative")
        keys = [key for key, _ in self.parameters]
        if len(keys) != len(set(keys)) or any(not key for key in keys):
            raise ValueError("experiment parameter keys must be unique and nonempty")


@dataclass(frozen=True, slots=True)
class ExperimentOutput:
    metrics: tuple[tuple[str, Decimal], ...]
    artifacts: tuple[Path, ...]

    def __post_init__(self) -> None:
        keys = [key for key, _ in self.metrics]
        if not keys or len(keys) != len(set(keys)):
            raise ValueError("experiment output metrics must be nonempty and unique")
        if any(not value.is_finite() for _, value in self.metrics):
            raise ValueError("experiment output metrics must be finite")


@dataclass(frozen=True, slots=True)
class ExperimentRunResult:
    experiment_id: str
    strategy_id: str
    spec_hash: str
    result_hash: str
    artifact_path: Path
    metrics: tuple[tuple[str, Decimal], ...]
    completed_at: datetime


class ExperimentEvaluator(Protocol):
    def __call__(self, spec: ExperimentRunSpec) -> ExperimentOutput: ...


class ExperimentRunner:
    """Deterministic experiment orchestration; it evaluates but never promotes."""

    _REQUIRED_ARTIFACTS = frozenset(
        {"equity", "drawdown", "trades", "positions", "risk", "logs"}
    )

    def __init__(self, root: Path, *, catalog: SQLiteMetadataCatalog | None = None) -> None:
        self._root = root.resolve()
        self._store = ExperimentArtifactStore(self._root)
        self._catalog = catalog or SQLiteMetadataCatalog(self._root / "metadata.db")

    def run(
        self,
        spec: ExperimentRunSpec,
        *,
        manifest: DataManifest,
        evaluator: ExperimentEvaluator,
        completed_at: datetime | None = None,
    ) -> ExperimentRunResult:
        if manifest.quality_status is not QualityStatus.PASSED:
            raise ValueError("experiment runner requires a PASSED data manifest")
        if manifest.data_version != spec.data_version:
            raise ValueError("experiment data_version does not match manifest")
        self._catalog.register_dataset(manifest)
        output = evaluator(spec)
        artifact_manifest = self._validate_artifacts(output.artifacts)
        timestamp = completed_at or datetime.now(UTC)
        if timestamp.tzinfo is None:
            raise ValueError("experiment completion timestamp must be timezone-aware")
        record = ExperimentRecord(
            experiment_id=spec.experiment_id,
            git_commit=spec.git_commit,
            data_version=spec.data_version,
            universe=spec.universe,
            factor_set=spec.factor_set,
            label=spec.label,
            split=spec.split,
            model=spec.model,
            hyperparameters=dict(spec.parameters),
            seed=spec.seed,
            transaction_cost=spec.transaction_cost,
            metrics=dict(output.metrics),
            artifacts=tuple(f"{name}:{digest}" for name, digest in artifact_manifest),
        )
        artifact_path, record_hash = self._store.save(record)
        spec_hash = self._hash(spec)
        result_hash = self._hash(
            {
                "artifact_manifest": artifact_manifest,
                "metrics": output.metrics,
                "record_hash": record_hash,
                "strategy_id": spec.strategy_id,
            }
        )
        self._catalog.register_experiment(
            ExperimentCatalogEntry(
                spec.experiment_id,
                spec_hash,
                spec.git_commit,
                spec.data_version,
                str(artifact_path),
            )
        )
        return ExperimentRunResult(
            spec.experiment_id,
            spec.strategy_id,
            spec_hash,
            result_hash,
            artifact_path,
            output.metrics,
            timestamp,
        )

    def _validate_artifacts(self, artifacts: tuple[Path, ...]) -> tuple[tuple[str, str], ...]:
        manifest: list[tuple[str, str]] = []
        for path in artifacts:
            resolved = path.resolve()
            if not resolved.is_relative_to(self._root):
                raise ValueError("experiment artifact escapes runner root")
            if not resolved.is_file():
                raise ValueError(f"experiment artifact is missing: {path.name}")
            manifest.append((resolved.stem, sha256(resolved.read_bytes()).hexdigest()))
        names = {name for name, _ in manifest}
        missing = self._REQUIRED_ARTIFACTS - names
        if missing:
            raise ValueError(
                "experiment output is missing required artifacts: " + ",".join(sorted(missing))
            )
        return tuple(sorted(manifest))

    @staticmethod
    def _hash(value: object) -> str:
        payload = asdict(value) if is_dataclass(value) and not isinstance(value, type) else value
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return sha256(canonical.encode("utf-8")).hexdigest()
