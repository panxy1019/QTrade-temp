"""Run the PAPER control plane locally; real trading is unavailable."""

import argparse
from decimal import Decimal
from pathlib import Path

from qtrade.api import context_from_runtime, create_app
from qtrade.domain import RuntimeMode
from qtrade.execution import FeeSchedule
from qtrade.risk import RiskLimits
from qtrade.runtime import build_paper_runtime


def build_app(*, historical_manifest: Path | None = None):
    bundle = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("local-unconfigured", Decimal("0"), Decimal("0")),
        journal_path=Path("runtime/control-plane.db"),
    )
    return create_app(
        context_from_runtime(bundle, historical_manifest_path=historical_manifest)
    )

if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--historical-manifest",
        type=Path,
        help="checksum-signed manifest.json produced by akshare_history_ingest.py",
    )
    args = parser.parse_args()
    uvicorn.run(
        build_app(historical_manifest=args.historical_manifest),
        host="127.0.0.1",
        port=8000,
    )
else:
    app = build_app()
