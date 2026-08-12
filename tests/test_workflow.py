from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from qtrade.data import Bar, DataManifest, QualityStatus
from qtrade.domain import Instrument
from qtrade.workflows import BaselineWorkflow


def test_reproducible_baseline_workflow_writes_versioned_report(tmp_path):
    start = date(2026, 1, 5)
    bars = [
        Bar(
            "510300",
            "SSE",
            datetime(2026, 1, 5, tzinfo=UTC) + timedelta(days=index),
            start + timedelta(days=index),
            Decimal("10") + index,
            Decimal("11") + index,
            Decimal("9") + index,
            Decimal("10") + index,
            Decimal("1000"),
        )
        for index in range(8)
    ]
    manifest = DataManifest(
        "workflow-v1",
        "fixture",
        "1",
        datetime(2026, 1, 1, tzinfo=UTC),
        "pending",
        QualityStatus.PENDING,
    )
    result = BaselineWorkflow(tmp_path).run(
        bars=bars,
        manifest=manifest,
        instrument=Instrument("510300", "SSE", "ETF", 100, Decimal("0.001")),
        git_commit="test-commit",
        experiment_id="exp-workflow",
    )
    assert result.data_version == "workflow-v1"
    assert result.experiment_id == "exp-workflow"
    assert result.final_position == 100
    assert result.report_path.is_file()
    text = result.report_path.read_text(encoding="utf-8")
    assert "test-commit" in text and "workflow-v1" in text
