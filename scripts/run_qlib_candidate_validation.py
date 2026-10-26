"""Validate a newly trained candidate against one frozen later input version."""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

from qtrade.research.data_versions import verify_version
from scripts.atomic_json import write_json_atomic, write_json_immutable
from scripts.qlib_restore_bundle import verify_bundle
from scripts.validate_qlib_forward import validate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('archive', 'data-root', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--model-id', required=True)
    parser.add_argument('--data-version-id', required=True)
    args = parser.parse_args()
    if os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
        raise ValueError('LIVE_TRADING_MUST_REMAIN_DISABLED')
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        if (verify_bundle(args.archive/'online-restore', args.archive/'provider')[
                'evidence_sha256'] != args.model_id
                or verify_version(args.data_root)['version_id'] != args.data_version_id):
            raise ValueError('CANDIDATE_VALIDATION_CONTEXT_CHANGED')
        write_json_atomic(args.output/'status.json', {'state': 'VALIDATING'})
        proof = validate(args.archive, args.data_root, args.output/'validations')
        write_json_immutable(args.output/'result.json', proof)
        write_json_atomic(args.output/'status.json', {'state': 'COMPLETE',
            'validation_id': proof['validation_id'], 'updated_at': datetime.now(UTC).isoformat()})
    except Exception:
        write_json_atomic(args.output/'status.json', {'state': 'FAILED',
            'updated_at': datetime.now(UTC).isoformat()})
        raise


if __name__ == '__main__':
    main()
