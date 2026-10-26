from threading import Event

import pytest

from scripts import collect_unified_data as collector
from scripts import publish_unified_collection as publication
from scripts import run_research_maintenance as maintenance


@pytest.mark.parametrize('fail', [False, True])
def test_unified_loop_preserves_legacy_refresh_and_reports_business_failures(
    tmp_path, monkeypatch, fail,
):
    stop = Event()
    states = []
    calls = []
    monkeypatch.setattr(maintenance, 'write_json_atomic', lambda path, value: states.append(value))
    monkeypatch.setattr(maintenance, 'refresh', lambda base: calls.append('legacy'))
    monkeypatch.setattr(publication, 'publish_collection',
                        lambda base, value: calls.append('public'))

    def run(base, database, **kwargs):
        calls.append('unified')
        assert kwargs['stop'] is stop and kwargs['seconds'] == 220
        stop.set()
        if fail:
            raise ValueError('controlled failure')
        kwargs['publish_progress']({})
        return {'state': 'PASS_FINISHED'}
    monkeypatch.setattr(collector, 'run_pass', run)
    maintenance.unified_loop(tmp_path, tmp_path / 'local.sqlite', 'a' * 40, stop)
    assert 'legacy' in calls and 'unified' in calls
    assert states[-1]['state'] == ('FAILED' if fail else 'WAITING')
    assert all(s['live_trading_enabled'] is False for s in states)
