from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from qtrade.research.experiment import ExperimentRecord


class ExperimentArtifactStore:
    """Immutable local experiment registry with a canonical specification hash."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def save(self, record: ExperimentRecord) -> tuple[Path, str]:
        self._root.mkdir(parents=True, exist_ok=True)
        target = self._root / f"{record.experiment_id}.json"
        if target.exists():
            raise FileExistsError("experiment record is immutable")
        payload = asdict(record)
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=self._json_default,
        )
        digest = sha256(canonical.encode("utf-8")).hexdigest()
        envelope = {"spec_hash": digest, "record": json.loads(canonical)}
        target.write_text(json.dumps(envelope, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return target, digest

    @staticmethod
    def _json_default(value: object) -> str:
        if isinstance(value, (date, Decimal)):
            return str(value)
        raise TypeError(f"unsupported artifact type: {type(value).__name__}")
