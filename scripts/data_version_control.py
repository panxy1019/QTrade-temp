"""Owner-free catalog read or serialized, explicit research-only data rollback."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qtrade.research.data_version_control import apply, command, read_catalog
from qtrade.research.data_versions import DataVersionCatalog


def write_receipt(path: Path, value: dict[str, Any]) -> None:
    from scripts.atomic_json import write_json_immutable
    try:
        write_json_immutable(path, value)
    except FileExistsError:
        if path.is_symlink() or json.loads(path.read_bytes()) != value:
            raise ValueError('DATA_ROLLBACK_OUTPUT_RECEIPT_CONFLICT') from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('catalog', 'rollback'))
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--archive-root', type=Path, required=True)
    parser.add_argument('--request', type=Path)
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
        raise RuntimeError('LIVE_TRADING_MUST_REMAIN_DISABLED')
    if args.action == 'catalog':
        print(json.dumps(read_catalog(args.database, args.archive_root, limit=args.limit)))
        return
    if args.request is None or args.request.stat().st_size > 4096:
        raise ValueError('DATA_ROLLBACK_BOUNDED_REQUEST_REQUIRED')
    request = command(json.loads(args.request.read_bytes()))
    # The caller validates the signed queue envelope before writing this local command.
    # No alternate owner: current build subprocess must exit before rollback can claim SQLite.
    from scripts.run_qlib_data_service import exclusive
    with exclusive(args.database.parent/'qlib-provider-build.lock'):
        catalog = DataVersionCatalog(args.database, args.archive_root)
        result = apply(catalog, request, now=datetime.now(UTC))
    if args.output:
        write_receipt(args.output, result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
