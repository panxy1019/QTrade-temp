"""Run the synthetic reproducibility workflow without external market data."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from subprocess import check_output

from qtrade.data import Bar, DataManifest, QualityStatus
from qtrade.domain import Instrument
from qtrade.workflows import BaselineWorkflow


def main() -> None:
    start = date(2026, 1, 5)
    bars = [
        Bar(
            "510300",
            "SSE",
            datetime.combine(start + timedelta(days=index), datetime.min.time(), UTC),
            start + timedelta(days=index),
            Decimal(10 + index) / 10,
            Decimal(11 + index) / 10,
            Decimal(9 + index) / 10,
            Decimal(10 + index) / 10,
            Decimal("100000"),
        )
        for index in range(8)
    ]
    manifest = DataManifest(
        "synthetic-v1",
        "generated-fixture",
        "1",
        datetime.now(UTC),
        "pending",
        QualityStatus.PENDING,
    )
    commit = check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    result = BaselineWorkflow(Path("runtime/sample")).run(
        bars=bars,
        manifest=manifest,
        instrument=Instrument("510300", "SSE", "ETF", 100, Decimal("0.001")),
        git_commit=commit,
        experiment_id="synthetic-buy-hold-v1",
    )
    print(result)


if __name__ == "__main__":
    main()
