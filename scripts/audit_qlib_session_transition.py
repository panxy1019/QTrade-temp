"""Verify immutable archives of an actual subsequent-session automatic cycle."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from qtrade.monitoring.data_freshness import Calendar
from qtrade.research.data_versions import verify_version
from qtrade.research.session_acceptance import assess_transition
from qtrade.research.signal_publication import verify_signal_archive
from scripts.atomic_json import write_json_immutable
from scripts.prepare_qlib_incremental import load_signed
from scripts.run_qlib_prediction import verify_prediction

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('previous-version', 'current-version', 'automatic-completion',
                 'prediction', 'signals', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    # No --now override: production evidence uses the actual wall clock.
    previous = verify_version(args.previous_version)
    current = verify_version(args.current_version)
    completion = load_signed(args.automatic_completion)
    daily_request = load_signed(args.automatic_completion.parent/'request.json')
    if (daily_request['previous_version'] != previous['version_id']
            or daily_request['day'] != current['actual_end']):
        raise ValueError('SESSION_ACCEPTANCE_AUTOMATIC_REQUEST_MISMATCH')
    result = assess_transition(previous, current, completion,
        verify_prediction(args.prediction), verify_signal_archive(args.signals)['manifest'],
        Calendar(REPO/'configs/observer_calendar_2026.json'), now=datetime.now(UTC))
    write_json_immutable(args.output, result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
