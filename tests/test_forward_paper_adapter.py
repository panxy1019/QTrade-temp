import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from test_collect_unified_data import CALENDAR, NOW, batch, fake_source
from test_daily_prediction_artifact import artifact  # noqa: F401
from test_forward_prediction import inputs  # noqa: F401
from test_forward_prediction import snapshot as raw_snapshot  # noqa: F401
from test_prediction_registry import ledger  # noqa: F401
from test_signal_publication import archive  # noqa: F401
from test_unified_store import store  # noqa: F401

from qtrade.domain import RuntimeMode
from qtrade.execution import FeeSchedule
from qtrade.monitoring.data_freshness import signed
from qtrade.research.data_versions import file_hash
from qtrade.research.forward_paper import ForwardPaperBook
from scripts.collect_unified_data import collect_batch
from scripts.qlib_forward_paper_adapter import (
    observed_session,
    prediction_target,
    public_book_summary,
    signal_targets,
)


@pytest.fixture
def snapshot(raw_snapshot):  # noqa: F811
    root, manifest = raw_snapshot
    pq.write_table(
        pa.Table.from_pylist([{"instrument": "SH600036", "score": 0.3, "rank": 1}]),
        root / "scores.parquet",
    )
    manifest["files"]["scores.parquet"] = file_hash(root / "scores.parquet")
    manifest = signed({k: v for k, v in manifest.items() if k != "evidence_sha256"})
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root, manifest


def test_verified_raw_replay_to_shadow_keeps_constraints_unknown(store):  # noqa: F811
    source, _ = fake_source()
    manifest = collect_batch(store, batch(), CALENDAR, source=source)
    session, evidence = observed_session(
        store.root,
        [manifest["batch_id"]],
        day=date(2026, 10, 26),
        instruments=["SH600036", "SZ000001", "SZ000002"],
        calendar=CALENDAR,
        now=NOW,
    )
    assert evidence["raw_replay_verified"] is True
    assert evidence["missing"] == ["SZ000002"]
    assert evidence["constraints_certified"] is False
    assert session.previous_session == date(2026, 10, 23)
    assert session.next_session == date(2026, 10, 27)
    assert session.securities["SH600036"].raw_open == Decimal("10")
    assert all(
        row.block_reason() == "UNKNOWN_TRADING_CONSTRAINTS" for row in session.securities.values()
    )
    assert all(
        row.corporate_action_clear is None and row.st is None and row.suspended is None
        and row.previous_session_volume is None
        for row in session.securities.values()
    )


def test_previous_volume_requires_raw_receipts_already_known_before_execution(store):  # noqa: F811
    source, _ = fake_source()
    current = collect_batch(store, batch(), CALENDAR, source=source)

    def previous_capture(capture):
        capture["body"] = capture["body"].replace(b"2026-10-26", b"2026-10-23")
        capture["received_at"] = "2026-10-23T09:00:00+00:00"
        return capture

    source, _ = fake_source(previous_capture)
    previous = collect_batch(
        store, batch(target="2026-10-23T15:00:00+08:00"), CALENDAR, source=source
    )
    result, evidence = observed_session(
        store.root,
        [current["batch_id"]],
        previous_batch_ids=[previous["batch_id"]],
        day=date(2026, 10, 26),
        instruments=["SH600036"],
        calendar=CALENDAR,
        now=NOW,
    )
    assert result.securities["SH600036"].previous_session_volume == 100
    prior = evidence["previous_session_volume_evidence"]
    assert prior["day"] == "2026-10-23"
    assert prior["receipts"][0]["raw_sha256"] == previous["raw"]["sha256"]
    assert prior["constraints_certified"] is False
    assert result.securities["SH600036"].block_reason() == "UNKNOWN_TRADING_CONSTRAINTS"

    def late_capture(capture):
        capture = previous_capture(capture)
        capture["received_at"] = datetime(2026, 10, 26, 2, tzinfo=UTC).isoformat()
        return capture

    source, _ = fake_source(late_capture)
    late = collect_batch(
        store, batch(target="2026-10-23T15:00:00+08:00"), CALENDAR, source=source
    )
    with pytest.raises(ValueError, match="CLOCK_MISMATCH"):
        observed_session(
            store.root,
            [current["batch_id"]],
            previous_batch_ids=[late["batch_id"]],
            day=date(2026, 10, 26),
            instruments=["SH600036"],
            calendar=CALENDAR,
            now=NOW,
        )


def test_raw_tampering_prevents_snapshot(store):  # noqa: F811
    source, _ = fake_source()
    manifest = collect_batch(store, batch(), CALENDAR, source=source)
    raw = manifest["raw"]
    store._blob_path(raw["sha256"], raw["suffix"]).write_bytes(b"tampered")
    with pytest.raises(ValueError):
        observed_session(
            store.root,
            [manifest["batch_id"]],
            day=date(2026, 10, 26),
            instruments=["SH600036"],
            calendar=CALENDAR,
            now=NOW,
        )


def test_non_day_or_wrong_day_cannot_be_used(store):  # noqa: F811
    source, _ = fake_source()
    manifest = collect_batch(store, batch("5min"), CALENDAR, source=source)
    with pytest.raises(ValueError, match="DAY_OR_CLOCK"):
        observed_session(
            store.root,
            [manifest["batch_id"]],
            day=date(2026, 10, 26),
            instruments=["SH600036"],
            calendar=CALENDAR,
            now=NOW,
        )


def test_signal_archive_current_context_and_provenance(archive):  # noqa: F811
    path = archive / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["limitations"] = ["non PIT"]
    path.write_text(
        json.dumps(signed({k: v for k, v in manifest.items() if k != "evidence_sha256"}))
    )
    targets = signal_targets(archive, data_version="a" * 64, source_revision="b" * 40)
    target, link = targets[0]
    assert target.strategy_id == "momentum20"
    assert len(target.weights) == 50 and sum(target.weights.values()) == Decimal(".95")
    assert link["artifact_id"] == target.signal_id
    with pytest.raises(ValueError, match="CONTEXT"):
        signal_targets(archive, data_version="c" * 64, source_revision="b" * 40)
    (archive / "indicative-targets.json").write_text("{}")
    with pytest.raises(ValueError):
        signal_targets(archive, data_version="a" * 64, source_revision="b" * 40)


def test_summary_is_bounded_linked_and_does_not_claim_execution(tmp_path):
    book = ForwardPaperBook(
        tmp_path,
        strategy_id="momentum20",
        mode=RuntimeMode.SHADOW,
        opening_cash=Decimal("10000"),
        fees=FeeSchedule("fixture", Decimal(0), Decimal(0)),
    )
    link = {
        "strategy_id": "momentum20",
        "kind": "signal-family",
        "artifact_id": "a" * 64,
        "manifest_sha256": "b" * 64,
        "model_id": None,
    }
    summary = public_book_summary(book, archive_link=link)
    assert summary["sessions"] == [] and summary["archive"] == link
    assert summary["real_data_paper_readiness"] is False
    assert summary["live_trading_enabled"] is False
    with pytest.raises(ValueError, match="LIMIT"):
        public_book_summary(book, archive_link=link, limit=31)
    with pytest.raises(ValueError, match="LINK"):
        public_book_summary(book, archive_link={**link, "artifact_id": "../escape"})


def test_approved_prediction_separate_model_version_and_bound_parquet(artifact):  # noqa: F811
    root, manifest, _, _ = artifact
    target, link = prediction_target(
        root, active_model_id="a" * 64, data_version="e" * 64, source_revision="c" * 40
    )
    assert target.strategy_id == "approved_model_" + "a" * 64
    assert link["model_id"] == "a" * 64
    assert target.weights == {"SH600036": Decimal(".95")}
    assert target.signal_id == manifest["prediction_id"]
    with pytest.raises(ValueError, match="CONTEXT"):
        prediction_target(
            root, active_model_id="b" * 64, data_version="e" * 64, source_revision="c" * 40
        )
    next((root / "calculation").rglob("scores.parquet")).write_bytes(b"changed")
    with pytest.raises(ValueError):
        prediction_target(
            root, active_model_id="a" * 64, data_version="e" * 64, source_revision="c" * 40
        )
