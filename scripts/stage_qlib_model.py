"""Operator-side import of a checksum-verified candidate; never automatic activation."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from scripts.online_model_control import OnlineModels


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ('base', 'database', 'data-database', 'archive', 'validation'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
        raise RuntimeError('LIVE_TRADING_MUST_REMAIN_DISABLED')
    runtime = OnlineModels(args.base, args.database, args.data_database)
    model = runtime.import_candidate(args.archive, args.validation, now=datetime.now(UTC))
    print(json.dumps({'model_id': model, 'state': 'STAGED', 'model_activated': False}))


if __name__ == '__main__':
    main()
