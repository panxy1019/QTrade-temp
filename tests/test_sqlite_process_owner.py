import sqlite3
import sys
from pathlib import Path
from subprocess import run

from qtrade.persistence import SQLiteOperationalJournal


def test_second_process_cannot_open_owned_operational_journal(tmp_path):
    database = tmp_path / "owned.db"
    SQLiteOperationalJournal(database)
    script = (
        "from pathlib import Path; "
        "from qtrade.persistence import SQLiteOperationalJournal; "
        f"SQLiteOperationalJournal(Path({str(database)!r}))"
    )
    result = run(
        [sys.executable, "-c", script],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "already owned by process" in result.stderr


def test_stale_process_owner_is_reclaimed(tmp_path):
    database = tmp_path / "stale.db"
    SQLiteOperationalJournal(database)
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE process_owner SET process_id=-1, owner_token='stale' WHERE singleton=1"
        )
        connection.commit()
    finally:
        connection.close()
    SQLiteOperationalJournal(database)


def test_new_runtime_instance_fences_prior_same_process_writer(tmp_path):
    database = tmp_path / "fenced.db"
    first = SQLiteOperationalJournal(database)
    second = SQLiteOperationalJournal(database)
    second.save_state("active", {"value": 2})
    try:
        first.save_state("stale", {"value": 1})
    except RuntimeError as exc:
        assert "fenced by a newer runtime" in str(exc)
    else:
        raise AssertionError("stale runtime writer must be fenced")
