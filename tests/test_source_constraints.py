import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.source_constraints import capture_source, restrict_rows
from qtrade.research.workflow import canonical, digest


@pytest.fixture
def source(tmp_path):
    now = datetime.now(UTC).isoformat()
    master = {"source": "BaoStock query_stock_basic", "observed_at": now,
              "historical_available_at": None, "rows": [{"code": "sz.000001"}]}
    status = signed({
        "source": "BaoStock raw daily status backfill", "symbol": "000001",
        "historical_available_at": None, "certified_at_decision": False,
        "observed_at": now, "rows": [
            {"date": "2020-01-02", "code": "sz.000001", "tradestatus": "1", "isST": "1",
             "open": "10", "close": "10", "volume": "1000", "amount": "10000"},
        ],
    })

    def save():
        (tmp_path / "master").mkdir(exist_ok=True)
        (tmp_path / "status").mkdir(exist_ok=True)
        (tmp_path / "master" / (digest(master) + ".json")).write_bytes(canonical(master))
        (tmp_path / "status/000001.json").write_bytes(canonical(status))
        coverage = signed({
            **AUTHORITY, "schema_version": "qtrade.pit-repair.v1", "observed_at": now,
            "historical_pit_certified": False, "historical_available_at_known": False,
            "master_sha256": digest(master), "master_rows": 1, "status_symbols": 1,
            "status_rows": len(status["rows"]),
            "source_checksums": {"000001.json": digest(status)},
        })
        (tmp_path / "coverage.json").write_bytes(canonical(coverage))
        return coverage

    save()
    return tmp_path, status, master, save


def test_frozen_source_identity_is_independent_of_later_backfill(source, tmp_path):
    root, status, _, save = source
    snapshot = capture_source(root)
    frozen = tmp_path / "frozen"
    snapshot.freeze(frozen)
    assert capture_source(frozen).identity == snapshot.identity
    assert (frozen / "status/000001.json").read_bytes() == canonical(status)
    status["rows"][0]["isST"] = "0"
    status.update(signed({k: v for k, v in status.items() if k != "evidence_sha256"}))
    save()
    assert capture_source(root).identity != snapshot.identity
    assert capture_source(frozen).identity == snapshot.identity
    assert capture_source(frozen).statuses["000001"]["rows"][0]["isST"] == "1"


@pytest.mark.parametrize("item", ["coverage.json", "status/000001.json", "master"])
def test_tampered_sources_are_rejected_before_a_run(source, item):
    root, _, master, _ = source
    path = root / ("master/" + digest(master) + ".json" if item == "master" else item)
    value = json.loads(path.read_bytes())
    value["observed_at"] = "2020-01-01T00:00:00+00:00"
    path.write_bytes(canonical(value))
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        capture_source(root)


@pytest.mark.parametrize("field,value", [
    ("historical_available_at", "2020-01-01T00:00:00+00:00"),
    ("certified_at_decision", True), ("symbol", "000002"),
    ("observed_at", "2099-01-01T00:00:00+00:00"),
])
def test_resigned_backfill_cannot_invent_availability_or_identity(source, field, value):
    root, status, _, save = source
    status[field] = value
    status.update(signed({k: v for k, v in status.items() if k != "evidence_sha256"}))
    save()
    with pytest.raises(ValueError):
        capture_source(root)


def test_duplicate_and_wrong_code_source_rows_are_rejected(source):
    root, status, _, save = source
    status["rows"].append(deepcopy(status["rows"][0]))
    status.update(signed({k: v for k, v in status.items() if k != "evidence_sha256"}))
    save()
    with pytest.raises(ValueError, match="ROW_IDENTITY"):
        capture_source(root)


def test_missing_source_is_an_explicit_reproducible_snapshot(tmp_path):
    absent = capture_source(None)
    absent.freeze(tmp_path / "empty")
    assert capture_source(tmp_path / "empty").identity == absent.identity
    assert absent.coverage["historical_pit_certified"] is False


def bar(day="2020-01-02", **changes):
    return {"date": day, "raw_open": 10, "raw_close": 10, "raw_volume": 1000,
            "amount": 10000, "buy_block": 0.0, "sell_block": 0.0, **changes}


def test_reconciled_st_blocks_buy_without_blocking_sell_or_changing_prices(source):
    _, status, _, _ = source
    original = bar()
    result, counts, ledger = restrict_rows([original], "000001", status)
    assert result[0] == {**original, "buy_block": 1.0}
    assert original["buy_block"] == 0
    assert counts["reconciled_bars"] == counts["additional_buy_blocks"] == 1
    assert counts["additional_sell_blocks"] == 0
    assert ledger[0]["reason"] == "RECONCILED_ST_RESEARCH_BUY_EXCLUSION"


@pytest.mark.parametrize("field,value", [
    ("raw_open", 10.02), ("raw_close", 10.02), ("raw_volume", 1001),
    ("amount", 10002), ("raw_open", float("nan")),
])
def test_raw_source_conflict_blocks_both_directions(source, field, value):
    _, status, _, _ = source
    result, counts, ledger = restrict_rows([bar(**{field: value})], "000001", status)
    assert result[0]["buy_block"] == result[0]["sell_block"] == 1
    assert counts["conflict_bars"] == 1
    assert ledger[0]["reason"] == "RAW_SOURCE_CONFLICT_BLOCK_BOTH"


def test_missing_or_non_st_state_never_relaxes_base_constraints(source):
    _, status, _, _ = source
    status["rows"][0]["isST"] = "0"
    rows = [bar(buy_block=1), bar("2020-01-03", sell_block=1)]
    result, counts, _ = restrict_rows(rows, "000001", status)
    assert result == rows
    assert counts["missing_state_bars"] == 1
    assert counts["reconciled_bars"] == 1
    assert counts["additional_buy_blocks"] == counts["additional_sell_blocks"] == 0


def test_source_suspension_conflicting_with_a_price_row_blocks_both(source):
    _, status, _, _ = source
    status["rows"][0]["tradestatus"] = "0"
    result, counts, ledger = restrict_rows([bar()], "000001", status)
    assert result[0]["buy_block"] == result[0]["sell_block"] == 1
    assert counts["source_suspended_bars"] == counts["conflict_bars"] == 1
    assert ledger[0]["reason"] == "SOURCE_SUSPENDED_BUT_PRICE_ROW_EXISTS_BLOCK_BOTH"


def test_fractional_or_unknown_base_masks_cannot_be_treated_as_binary():
    with pytest.raises(ValueError, match="BINARY_BASE"):
        restrict_rows([bar(buy_block=0.5)], "000001", None)


@pytest.mark.parametrize("field", ["tradestatus", "isST"])
@pytest.mark.parametrize("unknown", [None, ""])
def test_unknown_source_state_is_never_labelled_normal_or_used_to_relax_masks(
    source, field, unknown,
):
    root, status, _, save = source
    status["rows"][0][field] = unknown
    status.update(signed({k: v for k, v in status.items() if k != "evidence_sha256"}))
    save()
    frozen = capture_source(root)
    assert frozen.statuses["000001"]["rows"][0][field] is unknown
    result, counts, ledger = restrict_rows([bar(sell_block=1)], "000001", status)
    assert result[0]["buy_block"] == result[0]["sell_block"] == 1
    counter = "unknown_trade_state_bars" if field == "tradestatus" else "unknown_st_state_bars"
    assert counts[counter] == 1
    assert "UNKNOWN" in ledger[0]["reason"] and "NON_ST" not in ledger[0]["reason"]


def test_missing_source_is_present_in_the_diagnostic_ledger_with_original_proxy():
    original = bar(buy_block=1)
    rows, counts, ledger = restrict_rows([original], "000001", None)
    assert rows == [original]
    assert counts["missing_state_bars"] == 1
    assert ledger[0]["reason"] == "SOURCE_STATE_MISSING_PROXY_RETAINED"


def test_archive_checks_request_and_raw_source_binding_even_if_file_hashes_are_resigned(
    source, tmp_path,
):
    from hashlib import sha256

    from qtrade.research.qlib_publication import verify_archive

    root, _, _, _ = source
    snapshot = capture_source(root)
    archive = tmp_path / "archive"
    snapshot.freeze(archive / "pit-sources")
    request = signed({"config": {"name": "source-test"},
                      "source_snapshot_sha256": snapshot.identity})
    run_id = "source-test-" + request["evidence_sha256"][:16]
    summary = signed({"run_id": run_id, "source_snapshot_sha256": snapshot.identity})
    (archive / "request.json").write_bytes(canonical(request))
    (archive / "summary.json").write_bytes(canonical(summary))

    def manifest():
        files = {p.relative_to(archive).as_posix(): sha256(p.read_bytes()).hexdigest()
                 for p in archive.rglob("*") if p.is_file() and p.name != "checksums.json"}
        (archive / "checksums.json").write_bytes(canonical(signed({
            "run_id": run_id, "files": files,
        })))

    manifest()
    assert verify_archive(archive)["run_id"] == run_id
    summary = signed({"run_id": run_id, "source_snapshot_sha256": "b" * 64})
    (archive / "summary.json").write_bytes(canonical(summary))
    manifest()
    with pytest.raises(ValueError, match="SOURCE_BOUND_REQUEST"):
        verify_archive(archive)
    missing = json.loads((archive / "checksums.json").read_bytes())
    del missing["files"]["pit-sources/snapshot.json"]
    (archive / "checksums.json").write_bytes(canonical(signed({
        k: v for k, v in missing.items() if k != "evidence_sha256"
    })))
    with pytest.raises(ValueError, match="PROOF_FILES_MISSING"):
        verify_archive(archive)


def test_old_v2_constraint_archive_still_verifies_without_rewriting_its_policy(source, tmp_path):
    from hashlib import sha256

    from qtrade.research.qlib_publication import verify_archive
    from qtrade.research.source_constraints import LEGACY_POLICY

    root, _, _, _ = source
    snapshot = capture_source(root)
    archive = tmp_path / "old-v2-archive"
    snapshot.freeze(archive / "pit-sources")
    request = signed({"config": {"name": "old-v2"}, "source_snapshot_sha256": snapshot.identity})
    run_id = "old-v2-" + request["evidence_sha256"][:16]
    constraints = signed({"schema_version": "qtrade.source-constraints.v1",
                          "policy": LEGACY_POLICY, "source_snapshot_sha256": snapshot.identity,
                          "historical_pit_certified": False, "exact_price_limits_certified": False})
    summary = signed({"run_id": run_id, "source_snapshot_sha256": snapshot.identity,
                      "execution_constraints": constraints})
    for name, value in {"request.json": request, "summary.json": summary,
                        "execution-constraints.json": constraints}.items():
        (archive / name).write_bytes(canonical(value))
    for name in ("constraint-ledger.parquet", "constraint-reload-check.json"):
        (archive / name).write_bytes(b"frozen-old-artifact")
    files = {p.relative_to(archive).as_posix(): sha256(p.read_bytes()).hexdigest()
             for p in archive.rglob("*") if p.is_file()}
    (archive / "checksums.json").write_bytes(canonical(signed({"run_id": run_id, "files": files})))
    before = {p.name: p.read_bytes() for p in archive.rglob("*") if p.is_file()}
    accepted = verify_archive(archive)
    assert accepted["execution_constraints"]["policy"] == LEGACY_POLICY
    assert {p.name: p.read_bytes() for p in archive.rglob("*") if p.is_file()} == before


def test_worker_pins_versioned_source_before_it_can_change(source, tmp_path, monkeypatch):
    import shutil
    import sys

    from scripts import run_qlib_worker as worker_module

    root, status, _, _ = source
    base = tmp_path / "base"
    if sys.platform == "win32":
        base = Path("\\\\?\\" + str(base.resolve()))
    target = base / "pit-repair"
    target.mkdir(parents=True)
    for name in ("master", "status"):
        shutil.copytree(root / name, target / name)
    shutil.copy2(root / "coverage.json", target / "coverage.json")
    monkeypatch.setenv("QTRADE_RESEARCH_QUEUE_SECRET", "test-only-queue-signing-key" * 2)
    monkeypatch.setattr(worker_module.GitQueue, "from_environment", lambda: object())
    worker = worker_module.Worker(base, base / "catalog.sqlite", sys.executable, "a" * 40)
    try:
        first = worker.freeze_source()
        assert worker.freeze_source() == first
        path = base / "workflow/source-snapshots" / first
        assert capture_source(path).statuses["000001"] == status
        # Change the mutable source to an explicit missing-coverage report.
        (base / "pit-repair/coverage.json").write_bytes(canonical({
            "state": "NO_REPAIR_COVERAGE_AT_SUBMISSION", "historical_pit_certified": False,
        }))
        assert worker.freeze_source() != first
        assert capture_source(path).identity == first
    finally:
        worker.db.close()
