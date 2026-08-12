from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from qtrade.data import DataManifest, QualityStatus


@dataclass(frozen=True, slots=True)
class ExperimentCatalogEntry:
    experiment_id: str
    spec_hash: str
    git_commit: str
    data_version: str
    artifact_path: str


class SQLiteMetadataCatalog:
    """Immutable local lineage catalog for datasets and experiments."""

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS datasets (
                    data_version TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    ingestion_time TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    quality_status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id TEXT PRIMARY KEY,
                    spec_hash TEXT NOT NULL,
                    git_commit TEXT NOT NULL,
                    data_version TEXT NOT NULL REFERENCES datasets(data_version),
                    artifact_path TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def register_dataset(self, manifest: DataManifest) -> None:
        if manifest.quality_status is not QualityStatus.PASSED:
            raise ValueError("catalog accepts only PASSED datasets")
        payload = (
            manifest.source,
            manifest.schema_version,
            manifest.ingestion_time.isoformat(),
            manifest.checksum,
            manifest.quality_status.value,
        )
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT source,schema_version,ingestion_time,checksum,quality_status "
                "FROM datasets WHERE data_version=?",
                (manifest.data_version,),
            ).fetchone()
            if existing is not None:
                if existing != payload:
                    raise ValueError("data_version conflicts with registered manifest")
                return
            connection.execute(
                "INSERT INTO datasets VALUES(?,?,?,?,?,?)",
                (manifest.data_version, *payload),
            )

    def register_experiment(self, entry: ExperimentCatalogEntry) -> None:
        if not all(
            (
                entry.experiment_id,
                entry.spec_hash,
                entry.git_commit,
                entry.data_version,
                entry.artifact_path,
            )
        ):
            raise ValueError("experiment catalog fields are required")
        payload = (
            entry.spec_hash,
            entry.git_commit,
            entry.data_version,
            entry.artifact_path,
        )
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT spec_hash,git_commit,data_version,artifact_path "
                "FROM experiments WHERE experiment_id=?",
                (entry.experiment_id,),
            ).fetchone()
            if existing is not None:
                if existing != payload:
                    raise ValueError("experiment_id conflicts with registered lineage")
                return
            try:
                connection.execute(
                    "INSERT INTO experiments VALUES(?,?,?,?,?)",
                    (entry.experiment_id, *payload),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("experiment references an unknown data_version") from exc

    def dataset_versions(self) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data_version FROM datasets ORDER BY data_version"
            ).fetchall()
        return tuple(row[0] for row in rows)

    def experiments(self) -> tuple[ExperimentCatalogEntry, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT experiment_id,spec_hash,git_commit,data_version,artifact_path "
                "FROM experiments ORDER BY experiment_id"
            ).fetchall()
        return tuple(ExperimentCatalogEntry(*row) for row in rows)
