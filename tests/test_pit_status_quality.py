import base64
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from qtrade.monitoring.data_freshness import signed
from qtrade.research.pit_quality import status_quality
from scripts import collect_pit_evidence as collector
from scripts import run_pit_repair as scheduler

NOW = datetime(2026, 10, 26, 0, 30, tzinfo=UTC)


def record():
    return signed({
        "source": "BaoStock raw daily status backfill", "symbol": "000001",
        "observed_at": NOW.isoformat(), "historical_available_at": None,
        "certified_at_decision": False, "rows": [{
            "date": "2020-01-02", "code": "sz.000001", "tradestatus": "1", "isST": "0",
            "open": "10", "high": "11", "low": "9", "close": "10", "preclose": "10",
            "volume": "1000", "amount": "10000",
        }],
    })


@pytest.mark.parametrize("kind", ["duplicate", "unordered", "wrong_code", "range", "state",
                                  "nan", "negative", "ohlc", "zero", "future_clock"])
def test_source_content_is_checked_even_when_the_checksum_is_valid(kind, tmp_path):
    value = record()
    row = value["rows"][0]
    if kind in {"duplicate", "unordered"}:
        value["rows"].append({**row, "date": "2020-01-01" if kind == "unordered" else row["date"]})
    elif kind == "wrong_code":
        row["code"] = "sz.000002"
    elif kind == "range":
        row["date"] = "2016-01-01"
    elif kind == "state":
        row["isST"] = "2"
    elif kind == "nan":
        row["amount"] = "NaN"
    elif kind == "negative":
        row["volume"] = "-1"
    elif kind == "ohlc":
        row["high"] = "9"
    elif kind == "zero":
        row.update(open="0", low="0")
    else:
        value["observed_at"] = "2099-01-01T00:00:00+00:00"
    path = tmp_path / "000001.json"
    path.write_text(json.dumps(signed({k: v for k, v in value.items() if k != "evidence_sha256"})))
    with pytest.raises(ValueError, match="SOURCE_STATUS"):
        collector.verified_status(path, end="2026-10-23")


def test_unknown_values_are_retained_and_local_market_date_is_used():
    value = record()
    value["observed_at"] = "2026-10-25T16:30:00+00:00"
    row = value["rows"][0]
    row.update(date="2026-10-26", tradestatus="", isST=None, volume="")
    before = deepcopy(value)
    quality = status_quality(value, "000001", NOW)
    assert quality["unknown_trade_state_rows"] == quality["unknown_st_state_rows"] == 1
    assert quality["missing_price_rows"] == 1
    assert quality["historical_pit_certified"] is False
    assert value == before


def test_bad_cache_is_isolated_and_original_bytes_remain_recoverable(tmp_path, monkeypatch):
    folder = tmp_path / "status"
    folder.mkdir()
    invalid = b'{"broken": [ 1, 2, 3 ]}\n'
    (folder / "000001.json").write_bytes(invalid)
    (folder / "000002.json").write_text(json.dumps({**record(), "symbol": "000002"}))

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    monkeypatch.setattr(collector, "write_json_immutable", write)
    accepted, quarantined = collector.status_inventory(tmp_path, end="2026-10-23")
    assert not accepted and set(quarantined) == {"000001", "000002"}
    receipt = json.loads(next((tmp_path / "quarantine/000001").glob("*.json")).read_bytes())
    assert base64.b64decode(receipt["raw_bytes_base64"]) == invalid
    assert (folder / "000001.json").read_bytes() == invalid
    assert collector.status_inventory(tmp_path, end="2026-10-23") == (accepted, quarantined)


def test_resigned_content_cannot_replace_the_identity_in_accepted_coverage(tmp_path, monkeypatch):
    from qtrade.research.workflow import digest

    folder = tmp_path / "status"
    folder.mkdir()
    value = record()
    expected = {"000001.json": digest(value)}
    value["rows"][0]["isST"] = "1"
    value = signed({k: v for k, v in value.items() if k != "evidence_sha256"})
    (folder / "000001.json").write_text(json.dumps(value))
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    monkeypatch.setattr(collector, "write_json_immutable", write)
    accepted, quarantined = collector.status_inventory(
        tmp_path, end="2026-10-23", expected=expected,
    )
    assert not accepted and set(quarantined) == {"000001"}
    archive = json.loads(next((tmp_path / "quarantine/000001").glob("*.json")).read_bytes())
    assert archive["error_code"] == "CACHED_STATUS_ACCEPTED_IDENTITY_MISMATCH"


def test_direct_collection_and_legacy_migration_cannot_bypass_global_retry(tmp_path, monkeypatch):
    root = tmp_path / "pit-repair"
    root.mkdir()
    deadline = datetime.now(UTC) + timedelta(hours=2)
    prior = {"maximum": 40, "outcome": "FAILED", "consecutive_failures": 3,
             "next_attempt_at": deadline.isoformat()}
    (root / "next-pass.json").write_text(json.dumps(prior))
    with pytest.raises(ValueError, match="GLOBAL_BACKOFF_NOT_DUE"):
        collector.collect(tmp_path, 1)
    assert not (root / "attempt.json").exists()
    data = tmp_path / "qlib-data-20261023"
    data.mkdir()
    (data / "dataset.json").write_text(json.dumps({
        "stocks": [{"symbol": "000001"}], "accepted_symbols": 1,
    }))
    def write(path, value):
        path.write_text(json.dumps(value))
    monkeypatch.setattr(scheduler, "write_json_immutable", write)
    migrated = scheduler.migrate_schedule(tmp_path, prior, datetime.now(UTC))
    assert datetime.fromisoformat(migrated["next_attempt_at"]) >= deadline
    assert migrated["consecutive_failures"] == 3
