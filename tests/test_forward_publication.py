from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from test_forward_paper import session, target

from qtrade.domain import RuntimeMode
from qtrade.execution import FeeSchedule
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.forward_paper import ForwardPaperBook
from qtrade.research.forward_publication import validate_forward_publication
from scripts.qlib_forward_paper_adapter import public_book_summary


def resign(value):
    return signed({k: v for k, v in value.items() if k != "evidence_sha256"})


@pytest.fixture
def publication(tmp_path):
    book = ForwardPaperBook(
        tmp_path,
        strategy_id="momentum20",
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("10000"),
        fees=FeeSchedule("fixture", Decimal(0), Decimal(0)),
    )
    book.recover(actor="test human")
    t = replace(target(), signal_id="a" * 64, data_version="b" * 64, source_revision="c" * 40)
    market = replace(session(), market_evidence="d" * 64, calendar_evidence="e" * 64)
    book.freeze(t, now=t.generated_at)
    book.observe(t, market, now=market.observed_at)
    summary = public_book_summary(
        book,
        archive_link={
            "kind": "signal-family",
            "artifact_id": t.signal_id,
            "manifest_sha256": "f" * 64,
            "strategy_id": t.strategy_id,
            "model_id": None,
        },
    )
    stamp = datetime.now(UTC)
    return signed(
        {
            **AUTHORITY,
            "schema_version": "qtrade.forward-service.v1",
            "generated_at": stamp.isoformat(),
            "heartbeat_at": stamp.isoformat(),
            "source_revision": "c" * 40,
            "state": "RUNNING",
            "books": [summary],
            "errors": [],
            "limitations": ["Synthetic fixture; no actual forward acceptance"],
        }
    )


def test_complete_bounded_publication_preserves_actual_simulated_details(publication):
    assert (
        validate_forward_publication(publication, now=datetime(2026, 12, 1, tzinfo=UTC))
        == publication
    )
    row = publication["books"][0]["sessions"][0]
    assert row["fills"][0]["quantity"] == 500
    assert row["holdings"] == [{"instrument": "600000", "quantity": 500}]
    assert row["orders"][0]["status"] == "FILLED"
    assert publication["books"][0]["frozen_targets"][0]["weights"] == [
        {"instrument": "600000", "weight": "0.5"}
    ]


@pytest.mark.parametrize(
    "damage",
    [
        "authority",
        "unsigned",
        "path",
        "duplicate",
        "nan",
        "shadow",
        "future",
        "count",
        "backdated",
        "unknownfield",
        "numeric-float",
        "count-bool",
        "zero-price",
        "order-status",
        "missing-metrics",
        "details-bound",
        "duplicate-holding",
    ],
)
def test_invalid_or_overclaimed_forward_publication_rejected(publication, damage):
    value = deepcopy(publication)
    book = value["books"][0]
    if damage == "authority":
        value["live_trading_enabled"] = True
    elif damage == "unsigned":
        value["state"] = "DEGRADED"
        with pytest.raises(ValueError):
            validate_forward_publication(value, now=datetime(2026, 12, 1, tzinfo=UTC))
        return
    elif damage == "path":
        value["errors"] = [{"strategy_id": None, "mode": None, "code": "/root/private/token"}]
    elif damage == "duplicate":
        value["books"].append(deepcopy(book))
    elif damage == "nan":
        book["sessions"][0]["net_asset_value"] = "NaN"
    elif damage == "shadow":
        book["mode"] = "SHADOW"
        book["sessions"][0]["state"] = "SHADOW_OBSERVED"
    elif damage == "future":
        value["heartbeat_at"] = "2100-01-01T00:00:00+00:00"
    elif damage == "count":
        book["sessions"][0]["fill_count"] = 0
    elif damage == "backdated":
        t = book["frozen_targets"][0]
        t["frozen_at"] = (
            datetime.fromisoformat(t["execution_at"]) + timedelta(seconds=1)
        ).isoformat()
    elif damage == "numeric-float":
        book["sessions"][0]["cash"] = 4995.0
    elif damage == "count-bool":
        book["sessions"][0]["fill_count"] = True
    elif damage == "zero-price":
        book["sessions"][0]["fills"][0]["price"] = "0"
    elif damage == "order-status":
        book["sessions"][0]["orders"][0]["status"] = "MADE_UP"
    elif damage == "missing-metrics":
        book["sessions"][0]["net_asset_value"] = None
    elif damage == "details-bound":
        row = book["sessions"][0]
        row["holding_count"] = 51
        row["holdings"] *= 51
    elif damage == "duplicate-holding":
        row = book["sessions"][0]
        row["holding_count"] = 2
        row["holdings"] *= 2
    else:
        book["private_path"] = "/root/private"
    value["books"][0] = resign(book)
    with pytest.raises(ValueError):
        validate_forward_publication(resign(value), now=datetime(2026, 12, 1, tzinfo=UTC))


def test_pending_receipt_preserves_unknown_results_instead_of_zero(publication):
    book = publication["books"][0]
    row = book["sessions"][0]
    row.update(receipt_state="PENDING", state="PENDING", blocked_reasons={})
    for key in (
        "target_id", "signal_id", "data_version", "source_revision", "observed_at",
        "market_evidence", "calendar_evidence", "cash", "net_asset_value",
        "forward_return", "turnover",
    ):
        row[key] = None
    for key, count in (
        ("fills", "fill_count"), ("orders", "order_count"),
        ("holdings", "holding_count"), ("blockers", "blocked_count"),
    ):
        row[key], row[count], row[key + "_truncated"] = [], None, False
    book.update(risk_state="HALTED", running=False, recovery_required=True)
    publication["books"][0] = resign(book)
    value = resign(publication)
    validate_forward_publication(value, now=datetime(2026, 12, 1, tzinfo=UTC))
    value["books"][0]["sessions"][0]["cash"] = "0"
    value["books"][0] = resign(value["books"][0])
    with pytest.raises(ValueError, match="PENDING_CASH_UNKNOWN"):
        validate_forward_publication(resign(value), now=datetime(2026, 12, 1, tzinfo=UTC))
