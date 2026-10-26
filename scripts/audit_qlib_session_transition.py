"""Verify immutable archives of an actual subsequent-session automatic cycle."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from qtrade.monitoring.data_freshness import Calendar
from qtrade.research.data_versions import verify_version
from qtrade.research.session_acceptance import assess_transition
from qtrade.research.signal_publication import verify_signal_archive
from scripts.atomic_json import write_json_immutable
from scripts.prepare_qlib_incremental import load_signed
from scripts.qlib_public_delivery import verify_public_delivery
from scripts.run_qlib_prediction import verify_prediction

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('previous-version', 'current-version', 'automatic-completion',
                 'prediction', 'signals', 'database', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--verify-production', action='store_true')
    parser.add_argument('--expected-production-revision')
    args = parser.parse_args()
    if args.verify_production != (args.expected_production_revision is not None):
        parser.error('--verify-production and --expected-production-revision are required together')
    # No --now override: production evidence uses the actual wall clock.
    previous = verify_version(args.previous_version)
    current = verify_version(args.current_version)
    completion = load_signed(args.automatic_completion)
    daily_request = load_signed(args.automatic_completion.parent/'request.json')
    if (daily_request['previous_version'] != previous['version_id']
            or daily_request['day'] != current['actual_end']):
        raise ValueError('SESSION_ACCEPTANCE_AUTOMATIC_REQUEST_MISMATCH')
    prediction = verify_prediction(args.prediction)
    with closing(sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro', uri=True)) as c:
        row = c.execute('SELECT payload FROM data_events WHERE event_id=?',
                        (prediction['bindings']['data_event_id'],)).fetchone()
    if row is None:
        raise ValueError('SESSION_ACCEPTANCE_ADMISSION_EVENT_MISSING')
    signals = verify_signal_archive(args.signals)['manifest']
    result = assess_transition(previous, current, completion,
        prediction, signals, json.loads(row[0]),
        Calendar(REPO/'configs/observer_calendar_2026.json'), now=datetime.now(UTC))
    if args.verify_production:
        delivery = verify_public_delivery(result, current, prediction, signals,
                                           args.expected_production_revision)
        from qtrade.monitoring.data_freshness import signed

        result = signed({**{key: value for key, value in result.items()
                            if key != 'evidence_sha256'},
                         'production_publication_verified': True, 'public_delivery': delivery})
    write_json_immutable(args.output, result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
