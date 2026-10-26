"""Bounded operator acceptance runner; no schedule enablement or model activation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from qtrade.research.data_versions import verify_version
from qtrade.research.rolling_policy import plan, validate_policy
from qtrade.research.workflow import build_config, default_spec
from scripts.run_qlib_baseline import REPO, run


def run_candidate(data_root: Path, output: Path, tracking: Path, policy: dict[str, Any],
                  revision: str) -> dict[str, Any]:
    if os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
        raise ValueError('LIVE_TRADING_MUST_REMAIN_DISABLED')
    import shutil

    if shutil.disk_usage(data_root).free < 100*1024**3:
        raise ValueError('DISK_RESERVE_BELOW_100_GIB')
    policy = validate_policy(policy)
    version = verify_version(data_root)
    calendar = (data_root/'provider/calendars/day.txt').read_text().splitlines()
    planned = plan(policy, calendar, version['actual_end'], data_version_id=version['version_id'],
                   source_revision=revision)
    spec = default_spec(policy['model'])
    spec['seed'] = policy['seed']
    config = build_config(spec, json.loads((REPO/'configs/qlib_daily_baseline.json').read_text()))
    # The fixed historical request contract stays unchanged. Rolling has its own plan.
    config.pop('workflow_spec')
    config['name'] = 'rolling-alpha158-'+policy['model']
    config['segments'] = planned['segments']
    config['limitations'] += ['ROLLING_HISTORICAL_TEST_NOT_UNSEEN_FORWARD_EVIDENCE',
                              'MATURE_LABELS_ONLY_TWO_SESSIONS_PURGED']
    return run(data_root, output, tracking, revision, config_override=config,
               pit_evidence_root=data_root/'pit-sources', restore_proof=True, rolling_plan=planned)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-version-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--tracking', required=True, type=Path)
    parser.add_argument('--policy', required=True, type=Path)
    parser.add_argument('--source-revision', required=True)
    args = parser.parse_args()
    result = run_candidate(args.data_version_root, args.output, args.tracking,
                           json.loads(args.policy.read_text()), args.source_revision)
    print(json.dumps({'run_id': result['run_id'], 'metrics': result['metrics'],
                      'model_activated': False}))


if __name__ == '__main__':
    main()
