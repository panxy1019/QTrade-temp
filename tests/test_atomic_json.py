import os
import stat
from pathlib import Path

from scripts.atomic_json import write_json_atomic


def test_atomic_json_replaces_complete_snapshot_and_cleans_temporary_file(
    tmp_path: Path,
) -> None:
    target = tmp_path / "report.json"
    target.write_text('{"generation": 1}\n', encoding="utf-8")

    write_json_atomic(target, {"generation": 2, "status": "verified"})

    assert target.read_text(encoding="utf-8") == (
        '{\n  "generation": 2,\n  "status": "verified"\n}\n'
    )
    assert list(tmp_path.glob(".report.json.*.tmp")) == []


def test_atomic_json_syncs_file_then_published_directory(
    tmp_path: Path, monkeypatch,
) -> None:
    synced_types: list[str] = []
    real_fsync = os.fsync

    def track_fsync(file_descriptor: int) -> None:
        mode = os.fstat(file_descriptor).st_mode
        synced_types.append("directory" if stat.S_ISDIR(mode) else "file")
        real_fsync(file_descriptor)

    monkeypatch.setattr(os, "fsync", track_fsync)

    write_json_atomic(tmp_path / "report.json", {"status": "verified"})

    assert synced_types == ["file", "directory"]
