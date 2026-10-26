from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from qtrade.monitoring.data_freshness import Calendar
from qtrade.research.etf_trend import admit, sessions


@pytest.fixture
def calendar():
    return Calendar({'valid_from': '2026-01-01', 'valid_through': '2026-12-31',
                     'weekday_closures': ['2026-10-01', '2026-10-02', '2026-10-05',
                                          '2026-10-06', '2026-10-07']})


def capture(calendar, *, missing=False):
    window = sessions(calendar, date(2026, 10, 26))
    rows = [{'day': day, 'open': str(2+i*.01), 'high': str(2.1+i*.01),
             'low': str(1.9+i*.01), 'close': str(2+i*.01), 'volume': '1000'}
            for i, day in enumerate(window)]
    if missing:
        rows.pop(3)
    return {'source_id': 'sina_klines', 'request': {'symbol': 'sh510300', 'scale': '240',
            'ma': 'no', 'datalen': '63'}, 'received_at': '2026-10-26T12:00:00+00:00',
            'body': json.dumps(rows).encode(), 'status_code': 200,
            'transport_error': None, 'body_complete': True}


def test_close_only_admission_never_invents_amount_or_execution(calendar):
    value = admit([capture(calendar)], ['510300'], calendar, date(2026, 10, 26),
                  datetime(2026, 10, 26, 13, tzinfo=UTC))
    assert value['qualified_close_symbols'] == 1 and len(value['scores']) == 1
    assert value['scores'][0]['score'] == pytest.approx(.1)
    assert value['full_bar_qualification'] is False
    assert value['execution_admitted'] is False and value['forward_return'] is None
    assert value['return_adjustment_certified'] is False


def test_missing_holiday_grid_session_unavailable(calendar):
    value = admit([capture(calendar, missing=True)], ['510300'], calendar, date(2026, 10, 26),
                  datetime(2026, 10, 26, 13, tzinfo=UTC))
    assert value['state'] == 'INPUT_UNAVAILABLE' and not value['scores']
    assert value['qualified_close_symbols'] == 0


def test_wrong_capture_scope_rejected(calendar):
    row = capture(calendar)
    row['request']['symbol'] = 'sh600000'
    with pytest.raises(ValueError, match='SCOPE_MISMATCH'):
        admit([row], ['510300'], calendar, date(2026, 10, 26),
              datetime(2026, 10, 26, 13, tzinfo=UTC))


def test_future_observation_and_incomplete_body_not_admitted(calendar):
    row = capture(calendar)
    row['received_at'] = '2026-10-27T12:00:00+00:00'
    value = admit([row], ['510300'], calendar, date(2026, 10, 26),
                  datetime(2026, 10, 26, 13, tzinfo=UTC))
    assert value['state'] == 'INPUT_UNAVAILABLE'


def test_asof_during_market_not_closed(calendar):
    with pytest.raises(ValueError, match='CURRENT_COMPLETED'):
        admit([capture(calendar)], ['510300'], calendar, date(2026, 10, 26),
              datetime(2026, 10, 26, 4, tzinfo=UTC))
