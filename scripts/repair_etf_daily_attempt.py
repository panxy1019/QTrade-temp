"""Plan a read-only ETF repair, or explicitly apply an approved immutable plan."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from qtrade.research.etf_attempt_repair import apply, plan, read


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--local-root', type=Path, required=True)
    parser.add_argument('--day', required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--source-manifest', type=Path)
    parser.add_argument('--apply-plan', type=Path)
    parser.add_argument('--approved-plan-sha256')
    parser.add_argument('--deadline-seconds', type=int, default=900)
    args = parser.parse_args()
    if args.apply_plan:
        if not args.approved_plan_sha256:
            parser.error('--apply-plan requires --approved-plan-sha256')
        reviewed = read(args.apply_plan)
        if reviewed['day'] != args.day or reviewed['repair_revision'] != args.source_revision:
            parser.error('The selected day and source revision must match the reviewed plan')
        value = apply(args.base, args.local_root, reviewed, args.approved_plan_sha256,
                      deadline_seconds=args.deadline_seconds, source_manifest=args.source_manifest)
    else:
        if args.approved_plan_sha256:
            parser.error('--approved-plan-sha256 requires --apply-plan')
        value = plan(args.base, args.local_root, args.day, args.source_revision,
                     source_manifest=args.source_manifest)
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
