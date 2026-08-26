import json
import sys
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path

from scripts.update_intraday_continuity import main

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = PROJECT_ROOT / "deployment" / "evidence"


def _resign(report: dict[str, object]) -> None:
    report.pop("evidence_sha256", None)
    canonical = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    report["evidence_sha256"] = sha256(canonical.encode("utf-8")).hexdigest()


def test_continuity_migration_preserves_history_and_starts_directional_chain(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = tmp_path / "continuity.json"
    source_continuity = EVIDENCE_ROOT / "intraday-continuity" / "report.json"
    previous = json.loads(source_continuity.read_text(encoding="utf-8"))
    directional_first = previous.get("directional_breadth_first_sequence")
    if isinstance(directional_first, int):
        previous["checkpoints"] = previous["checkpoints"][: directional_first - 1]
        previous["checkpoint_count"] = len(previous["checkpoints"])
        previous["all_checkpoints_monotonic"] = all(
            row["monotonic_vs_previous"] for row in previous["checkpoints"]
        )
        previous["schema_version"] = 1
        previous.pop("directional_breadth_first_sequence")
        _resign(previous)
    output.write_text(json.dumps(previous), encoding="utf-8")

    source_paths: list[Path] = []
    for name in ("intraday-observation", "intraday-breadth"):
        source = EVIDENCE_ROOT / name / "report.json"
        report = json.loads(source.read_text(encoding="utf-8"))
        completed_at = datetime.fromisoformat(str(report["completed_at"]))
        report["completed_at"] = (completed_at + timedelta(minutes=20)).isoformat()
        _resign(report)
        target = tmp_path / f"{name}.json"
        target.write_text(json.dumps(report), encoding="utf-8")
        source_paths.append(target)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "update_intraday_continuity.py",
            "--detail",
            str(source_paths[0]),
            "--breadth",
            str(source_paths[1]),
            "--output",
            str(output),
        ],
    )
    assert main() == 0

    migrated = json.loads(output.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2
    assert migrated["checkpoint_count"] == previous["checkpoint_count"] + 1
    assert migrated["directional_breadth_first_sequence"] == migrated[
        "checkpoint_count"
    ]
    assert migrated["checkpoints"][:-1] == previous["checkpoints"]
    latest = migrated["checkpoints"][-1]
    assert latest["breadth"]["directional_breadth"]["eligible_symbol_count"] == 75
    assert latest["monotonic_vs_previous"] is True
