"""Read-only admission epoch identity; rollback cannot revive old signals."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from qtrade.research.model_registry import checked_document, checked_identity


def current_epoch(database: Path, version_id: str) -> str:
    checked_identity(version_id)
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True)) as c:
        row = c.execute('SELECT e.event_id,e.payload,h.version_id FROM data_events e '
                        "CROSS JOIN data_heads h WHERE h.frequency='day' "
                        'ORDER BY e.sequence DESC LIMIT 1').fetchone()
    if row is None:
        raise ValueError('CURRENT_DATA_EPOCH_MISSING')
    event = json.loads(row[1])
    checked_document(event)
    if (row[2] != version_id or event['evidence_sha256'] != row[0]
            or event['version_id'] != version_id
            or event['kind'] not in {'QLIB_DATA_READY', 'QLIB_DATA_ROLLBACK'}):
        raise ValueError('CURRENT_DATA_EPOCH_INVALID')
    return checked_identity(row[0])
