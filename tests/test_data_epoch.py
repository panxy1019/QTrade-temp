import json

import pytest
from test_data_version_control import setup

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.data_epoch import current_epoch


def test_epoch_matches_actual_head_and_ignores_no_newer_event(tmp_path):
    database, _, catalog, request, _ = setup(tmp_path)
    assert current_epoch(database, request['expected_head']) == request['expected_event_id']
    with pytest.raises(ValueError):
        current_epoch(database, request['version_id'])
    # An inconsistent newer event must not disappear behind a version WHERE filter.
    event = signed({**AUTHORITY, 'kind': 'QLIB_DATA_ROLLBACK',
                    'version_id': request['version_id']})
    with catalog.connect() as c:
        c.execute('INSERT INTO data_events(event_id,version_id,kind,payload) VALUES(?,?,?,?)',
                  (event['evidence_sha256'], request['version_id'], event['kind'],
                   json.dumps(event)))
    with pytest.raises(ValueError, match='EPOCH_INVALID'):
        current_epoch(database, request['expected_head'])
