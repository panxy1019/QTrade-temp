"""Isolated realistic-format fixtures; no live pool, Provider, services or broker."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_UP, Decimal, getcontext, localcontext
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research import factor_evaluation as evaluation
from qtrade.research.data_versions import SCHEMA, DataVersionCatalog, content_id, file_hash
from qtrade.research.factor_registry import FactorRegistry
from qtrade.research.factors import PriceFactorSpec
from qtrade.research.metrics import evaluate_factor
from scripts.run_daily_factor_evaluation import isolated_output, run


def _sessions():
    result, day = [], date(2020, 1, 2)
    while len(result) < 30:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day += timedelta(days=1)
    return result


@pytest.fixture(autouse=True)
def research_only(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")


@pytest.fixture
def setup(tmp_path):
    """Seal actual Parquet/calendars/admission with synthetic historical clocks."""
    days = _sessions()
    symbols = [f"60000{i}" for i in range(1, 7)]
    instruments = ["SH" + symbol for symbol in symbols]
    now = datetime.now(UTC)
    registry = FactorRegistry(tmp_path / "factors.sqlite")
    refs = registry.register_builtins(source_revision="a" * 40,
        price_basis="QLIB_NORMALIZED_ADJUSTMENT_UNCERTIFIED", now=now)
    database = tmp_path / "versions.sqlite"
    archive = tmp_path / "archive"
    catalog = DataVersionCatalog(database, archive)

    def build(*, nonce="a", transform=None):
        stage = archive / ("stage-" + nonce)
        (stage / "provider/calendars").mkdir(parents=True)
        (stage / "provider/instruments").mkdir()
        for name in ("day.txt", "day_future.txt"):
            (stage / "provider/calendars" / name).write_text("\n".join(days) + "\n")
        for name in ("all.txt", "workflow_supported.txt"):
            (stage / "provider/instruments" / name).write_text("".join(
                f"{instrument}\t{days[0]}\t{days[-2]}\n" for instrument in instruments))
        raw_rows = {}
        for number, symbol in enumerate(symbols, 1):
            rows = []
            for index, day in enumerate(days[:-1]):
                close = (Decimal(20 + number) + Decimal(number - 3) * index / 100
                         + Decimal((index * number) % 7) / 1000)
                row = {"date": day, "close": str(close),
                       "available_at": day + "T07:01:00+00:00",
                       "observed_at": day + "T07:02:00+00:00"}
                if transform:
                    row = transform(symbol, index, row)
                if row is not None:
                    rows.append(row)
            folder = stage / "stocks" / symbol
            folder.mkdir(parents=True)
            pq.write_table(pa.Table.from_pylist(rows), folder / "daily.parquet")
            raw_rows["SH" + symbol] = rows
        dataset = signed({**AUTHORITY, "end": days[-2], "accepted_symbols": len(symbols),
            "stocks": [{"symbol": s, "end": days[-2]} for s in symbols],
            "incremental": {"current_symbols": symbols}})
        (stage / "dataset.json").write_text(json.dumps(dataset))
        (stage / "preparation-request.json").write_text(json.dumps(signed({"nonce": nonce})))
        files = {p.relative_to(stage).as_posix(): file_hash(p)
                 for p in stage.rglob("*") if p.is_file()}
        reload = signed({**AUTHORITY, "schema_version": "qtrade.qlib-provider-reload.v1",
            "dataset_sha256": files["dataset.json"], "checked_current_symbols": len(symbols),
            "provider_sha256": content_id({k: v for k, v in files.items()
                if k.startswith("provider/")}, {}), "actual_end": days[-2],
            "checks_passed": True, "placeholder_all_missing": True,
            "unknown_current_prices_missing": True})
        (stage / "provider-reload.json").write_text(json.dumps(reload))
        files["provider-reload.json"] = file_hash(stage / "provider-reload.json")
        bindings = {"source_snapshot_sha256": nonce * 64}
        identity = content_id(files, bindings)
        manifest = signed({**AUTHORITY, "schema_version": SCHEMA, "version_id": identity,
            "state": "RESEARCH_READY_QUALIFIED_SUBSET", "frequency": "day",
            "historical_pit_certified": False, "external_execution_ready": False,
            "created_at": now.isoformat(), "actual_end": days[-2],
            "current_symbols": symbols, "historical_symbols": symbols, "blocked_symbols": [],
            "calendar_boundaries": [days[-1]], "files": files, "bindings": bindings})
        (stage / "version.json").write_text(json.dumps(manifest))
        root = archive / "versions" / identity
        root.parent.mkdir(exist_ok=True)
        stage.rename(root)
        catalog.admit(identity, now=datetime.now(UTC))
        return root, raw_rows

    request = {"ordered_factor_refs": list(refs), "instruments": instruments,
        "decisions": [{"session": day, "decision_at": day + "T08:00:00+00:00"}
                      for day in days[20:26]], "label_horizon_sessions": 1,
        "knowledge_mode": "RETROSPECTIVE_RECONSTRUCTION", "quantiles": 3,
        "min_pairs": 3, "stability_block_sessions": 2, "source_revision": "a" * 40,
        "revalidates_evaluation_id": None}

    def evaluate(root, spec=None):
        return evaluation.evaluate_daily(version_root=root, data_database=database,
            registry=registry, request=spec or request, now=datetime.now(UTC))

    return build, evaluate, request, database, registry, days


def test_ready_version_integration_matches_existing_math_and_binds_every_scope(setup):
    build, evaluate, request, _, _, days = setup
    root, rows = build()
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    report = evaluate(root)
    assert evaluation.validate_report(report) == report
    assert report["state"] == "DIAGNOSTICS_COMPUTED"
    documents = report["documents"]
    spec = documents["specification"]
    assert spec["data_binding"]["data_version_id"] == root.name
    assert spec["request"]["ordered_factor_refs"] == request["ordered_factor_refs"]
    assert spec["universe"]["instruments"] == request["instruments"]
    assert spec["label"]["formula"] == "close[t+h]/close[t]-1"
    assert spec["cost_policy"]["executable_return_claim"] is False
    assert spec["metric_policy"]["ir_ddof"] == 1
    assert len(documents["stability"]["0"]) == 3
    assert len(documents["redundancy_daily"]) == 36
    prices = [[Decimal(row["close"]) for row in rows[s]] for s in request["instruments"]]
    scores = [PriceFactorSpec("momentum", 20).value(tuple(series[:21])) for series in prices]
    labels = [series[21] / series[20] - 1 for series in prices]
    expected = evaluate_factor(scores, labels, quantiles=3)
    first = documents["daily_metrics"]["0"][0]
    assert first["session"] == days[20]
    assert Decimal(first["ic"]) == expected.ic
    assert Decimal(first["rank_ic"]) == expected.rank_ic
    assert tuple(Decimal(group["mean_label"]) for group in first["groups"]) \
        == expected.quantile_returns
    assert {p: p.read_bytes() for p in before} == before
    assert report["live_trading_enabled"] is False


def test_correlations_ties_group_order_identity_turnover_and_sample_ir():
    exact = evaluation._correlation([Decimal(v) for v in (1, 1, 2, 3)],
                                   [Decimal(v) for v in (1, 2, 2, 3)], 3)
    # Average ranks (1.5,1.5,3,4) and (1,2.5,2.5,4): covariance3.75 / variance4.5.
    assert Decimal(exact["spearman"]) == Decimal(5) / 6
    values = {s: Decimal(v) for s, v in zip("ABCDEF", (1, 1, 2, 3, 4, 5), strict=True)}
    labels = {s: Decimal(v) / 100 for s, v in zip("ABCDEF", (2, 0, 1, 3, 6, 5), strict=True)}
    first = evaluation._daily_metrics(values, labels, minimum=3, quantiles=3, previous_top=None)
    second = evaluation._daily_metrics(dict(reversed(list(values.items()))), labels,
        minimum=3, quantiles=3, previous_top={"D", "F"})
    assert second["groups"] == first["groups"]
    assert second["groups"][0]["instruments"] == ["A", "B"]
    assert second["top_group_turnover"] == "0.5"
    ir = evaluation._series(["0", "1", None])
    assert ir["valid_days"] == 2 and ir["mean"] == "0.5"
    assert abs(Decimal(ir["ir"]) - Decimal(2).sqrt() / 2) < Decimal("1e-15")
    assert evaluation._series(["1", "1"])["ir_reason"] == "ZERO_SAMPLE_STANDARD_DEVIATION"
    assert evaluation._series([None, "1"])["ir_reason"] == "INSUFFICIENT_DAYS"


@pytest.mark.parametrize("kind,reason", [
    ("constant", "CONSTANT_LEFT"), ("few", "INSUFFICIENT_PAIRS"),
    ("label_constant", "CONSTANT_RIGHT"),
])
def test_invalid_correlations_are_null_with_reason(kind, reason):
    left, right = [Decimal(1), Decimal(2), Decimal(3)], [Decimal(1), Decimal(2), Decimal(3)]
    if kind == "constant":
        left = [Decimal(1)] * 3
    elif kind == "label_constant":
        right = [Decimal(1)] * 3
    else:
        left, right = left[:2], right[:2]
    result = evaluation._correlation(left, right, 3)
    assert result["pearson"] is None and result["spearman"] is None
    assert result["reason"] == reason


def test_common_calendar_gap_is_not_observed_row_warmup_and_labels_remain_separate(setup):
    build, evaluate, _, _, _, _ = setup
    root, _ = build(transform=lambda s, i, row: None if s == "600001" and i == 10 else row)
    documents = evaluate(root)["documents"]
    rejected = [row for row in documents["eligibility_ledger"]
                if row["factor_index"] == 0 and row["instrument"] == "SH600001"]
    assert rejected[0]["feature_reason"] == "MISSING_COMMON_SESSION_CLOSE"
    assert documents["daily_metrics"]["0"][0]["pairs"] == 5
    assert documents["daily_metrics"]["1"][0]["pairs"] == 6
    sample = next(row for row in documents["samples"] if row["factor_index"] == 0
                  and row["instrument"] == "SH600001")
    assert sample["factor_value"] is None and sample["raw_label"] is not None


@pytest.mark.parametrize("clock", ["available_at", "observed_at"])
def test_late_or_unknown_clocks_stay_posthoc_and_cannot_enter_observed_system(setup, clock):
    build, evaluate, request, _, _, _ = setup

    def transform(symbol, index, row):
        if symbol == "600001" and index == 20:
            row[clock] = row["date"] + "T08:00:00.000001+00:00"
        if symbol == "600002" and index == 20:
            row[clock] = None
        return row

    root, _ = build(transform=transform)
    retrospective = evaluate(root)
    spec = deepcopy(request)
    spec["knowledge_mode"] = "OBSERVED_SYSTEM"
    actual = evaluate(root, spec)
    assert retrospective["documents"]["daily_metrics"]["0"][0]["pairs"] == 6
    assert actual["documents"]["daily_metrics"]["0"][0]["pairs"] == 4
    facts = [row for row in actual["documents"]["eligibility_ledger"]
             if row["factor_index"] == 0 and row["session"] == request["decisions"][0]["session"]]
    assert facts[0]["feature_reason"] == "DEPENDENCY_NOT_KNOWN_AT_DECISION"
    assert facts[1]["feature_reason"] == "DEPENDENCY_AVAILABILITY_UNKNOWN"
    assert retrospective["documents"]["summary"]["historical_computation_claim"] is False


def test_unmatured_labels_and_missing_warmup_are_explicit(setup):
    build, evaluate, request, _, _, days = setup
    root, _ = build()
    spec = deepcopy(request)
    spec["decisions"] = [{"session": day, "decision_at": day + "T08:00:00+00:00"}
                         for day in days[:29]]
    report = evaluate(root, spec)
    daily = report["documents"]["daily_metrics"]["0"]
    assert daily[0]["pairs"] == 0 and daily[0]["ic"] is None
    assert daily[-1]["label_valid"] == 0 and daily[-1]["pairs"] == 0
    facts = report["documents"]["eligibility_ledger"]
    assert any(row["feature_reason"] == "INSUFFICIENT_COMMON_CALENDAR_WARMUP" for row in facts)
    assert any(row["label_reason"] == "LABEL_NOT_MATURE_IN_FROZEN_VERSION" for row in facts)


def test_future_prices_do_not_enter_features_or_earlier_mature_labels(setup):
    build, evaluate, request, _, _, _ = setup
    first, _ = build()
    other, _ = build(nonce="b", transform=lambda s, i, row:
                     {**row, "close": "999"} if i > 26 else row)
    old, new = evaluate(first)["documents"], evaluate(other)["documents"]
    assert old["daily_metrics"] == new["daily_metrics"]
    assert old["samples"] == new["samples"]
    assert old["specification"]["data_binding"] != new["specification"]["data_binding"]
    assert old["specification"]["request"] == request


def test_new_version_revalidation_appends_and_concurrent_same_id_has_one_winner(setup, tmp_path):
    build, evaluate, request, _, _, _ = setup
    first, _ = build()
    store = evaluation.FactorEvaluationStore(tmp_path / "reports.sqlite")
    old = evaluate(first)
    original = json.dumps(old, sort_keys=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(store.save, old) for _ in range(2)]
        results = []
        for future in futures:
            try:
                results.append(future.result())
            except FileExistsError:
                results.append("immutable")
    assert sum(item == "immutable" for item in results) == 1
    other, _ = build(nonce="b")
    spec = deepcopy(request)
    spec["revalidates_evaluation_id"] = old["evaluation_id"]
    revised = evaluate(other, spec)
    store.save(revised)
    assert revised["evaluation_id"] != old["evaluation_id"]
    assert revised["specification_sha256"] != old["specification_sha256"]
    assert json.dumps(store.get(old["evaluation_id"]), sort_keys=True) == original
    assert store.get(revised["evaluation_id"]) == revised


def test_manifest_and_stored_payload_tampering_fail_closed(setup, tmp_path):
    build, evaluate, _, _, _, _ = setup
    root, _ = build()
    report = evaluate(root)
    changed = deepcopy(report)
    changed["documents"]["daily_metrics"]["0"][0]["ic"] = "0.999"
    with pytest.raises(ValueError, match="MANIFEST"):
        evaluation.validate_report(changed)
    # Rehashed mixed sections still fail numerical replay from the archived sample values.
    changed["report_manifest"]["daily_metrics"] = evaluation._hash(
        changed["documents"]["daily_metrics"])
    changed["report_manifest_sha256"] = evaluation._hash(changed["report_manifest"])
    with pytest.raises(ValueError, match="NUMERICAL_REPLAY"):
        evaluation.validate_report(changed)
    changed = deepcopy(report)
    changed["state"] = "ACTIVE"
    with pytest.raises(ValueError, match="STATE"):
        evaluation.validate_report(changed)
    store = evaluation.FactorEvaluationStore(tmp_path / "report.sqlite")
    store.save(report)
    with sqlite3.connect(store.database) as connection:
        connection.execute("UPDATE factor_evaluations SET payload='{}'")
    with pytest.raises(ValueError, match="STORED_PAYLOAD_CHANGED"):
        store.get(report["evaluation_id"])


@pytest.mark.parametrize("mutation", [
    "limitations", "historical_claim", "cost", "label", "split", "numeric", "authority",
])
def test_rehashed_reports_cannot_remove_limits_or_change_fixed_research_claims(setup, mutation):
    build, evaluate, _, _, _, _ = setup
    root, _ = build()
    report = evaluate(root)
    documents = report["documents"]
    spec = documents["specification"]
    if mutation == "limitations":
        documents["summary"]["limitations"] = []
    elif mutation == "historical_claim":
        documents["summary"]["historical_computation_claim"] = True
    elif mutation == "cost":
        spec["cost_policy"]["executable_return_claim"] = True
    elif mutation == "label":
        spec["label"]["formula"] = "open[t+2]/open[t+1]-1"
    elif mutation == "split":
        spec["split_policy"]["unseen_test_claim"] = True
    elif mutation == "numeric":
        spec["numeric_policy"]["decimal_precision"] = 6
    else:
        spec["point_in_time"] = True
    report["report_manifest"] = {name: evaluation._hash(value)
                                 for name, value in documents.items()}
    report["specification_sha256"] = report["report_manifest"]["specification"]
    report["report_manifest_sha256"] = evaluation._hash(report["report_manifest"])
    with pytest.raises(ValueError, match="FIXED_RESEARCH_CLAIMS"):
        evaluation.validate_report(report)


def test_cli_isolated_export_failure_attempts_and_protected_paths(setup, tmp_path):
    build, _, request, database, registry, _ = setup
    root, _ = build()
    output = tmp_path / "isolated-results"
    reference = run(version_root=root, data_database=database, factor_database=registry.database,
                    request=request, output_root=output)
    report = json.loads((output / (reference["evaluation_id"] + ".json")).read_bytes())
    assert evaluation.validate_report(report)["specification_sha256"] \
        == reference["specification_sha256"]
    with pytest.raises(ValueError, match="PROTECTED"):
        isolated_output(root / "results", protected=(root,))
    with pytest.raises(ValueError, match="NONPRODUCTION"):
        isolated_output(tmp_path / "production/results", protected=(root,))
    invalid = deepcopy(request)
    invalid["ordered_factor_refs"][0]["definition_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        run(version_root=root, data_database=database, factor_database=registry.database,
            request=invalid, output_root=output)
    with sqlite3.connect(output / "evaluations.sqlite") as connection:
        assert connection.execute("SELECT count(*) FROM factor_evaluation_attempts"
                                  ).fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM factor_evaluation_failures"
                                  ).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM factor_evaluations").fetchone()[0] == 1


def test_actual_cli_subprocess_exports_one_reviewable_report(setup, tmp_path):
    build, _, request, database, registry, _ = setup
    root, _ = build()
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(request))
    repo = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "PYTHONPATH": str(repo / "src") + os.pathsep + str(repo),
                   "LIVE_TRADING_ENABLED": "false"}
    output = tmp_path / "subprocess-output"
    completed = subprocess.run([
        sys.executable, str(repo / "scripts/run_daily_factor_evaluation.py"),
        "--version-root", str(root), "--data-database", str(database),
        "--factor-database", str(registry.database), "--request", str(request_file),
        "--output-root", str(output)], cwd=repo, env=environment, capture_output=True,
        text=True, check=True, timeout=30)
    reference = json.loads(completed.stdout)
    report = json.loads((output / (reference["evaluation_id"] + ".json")).read_bytes())
    assert evaluation.validate_report(report)["report_manifest_sha256"] \
        == reference["report_manifest_sha256"]


def test_cli_rejects_hardlink_alias_of_protected_input_before_any_write(setup, tmp_path):
    build, _, request, database, registry, _ = setup
    root, _ = build()
    output = tmp_path / "hardlink-output"
    output.mkdir()
    before = registry.database.read_bytes()
    os.link(registry.database, output / "evaluations.sqlite")
    with pytest.raises(ValueError, match="HARDLINK"):
        run(version_root=root, data_database=database, factor_database=registry.database,
            request=request, output_root=output)
    assert registry.database.read_bytes() == before


def test_store_rechecks_hardlink_alias_after_construction_for_each_write(setup, tmp_path):
    build, evaluate, request, _, registry, _ = setup
    root, _ = build()
    report = evaluate(root)
    store = evaluation.FactorEvaluationStore(tmp_path / "after-construction.sqlite")
    store.database.unlink()
    before = registry.database.read_bytes()
    os.link(registry.database, store.database)
    operations = [lambda: store.start(request, now=datetime.now(UTC)),
                  lambda: store.save(report),
                  lambda: store.fail(report["evaluation_id"], error_type="ValueError",
                                     now=datetime.now(UTC))]
    for operation in operations:
        with pytest.raises(ValueError, match="HARDLINK"):
            operation()
    assert registry.database.read_bytes() == before


def test_numeric_policy_ignores_and_preserves_hostile_caller_decimal_context(setup):
    build, evaluate, _, _, _, _ = setup
    root, _ = build()
    expected = evaluate(root)
    with localcontext() as context:
        context.prec, context.rounding = 6, ROUND_UP
        before = (context.prec, context.rounding, dict(context.traps))
        actual = evaluate(root)
        assert (getcontext().prec, getcontext().rounding, dict(getcontext().traps)) == before
    for key in ("daily_metrics", "samples", "stability", "redundancy_daily"):
        assert actual["documents"][key] == expected["documents"][key]
    assert actual["documents"]["specification"]["numeric_policy"]["decimal_precision"] == 28


def test_decision_barrier_uses_shanghai_session_and_label_requires_closed_session(setup):
    build, evaluate, request, database, registry, days = setup
    root, _ = build()
    spec = deepcopy(request)
    spec["decisions"] = [{"session": days[20], "decision_at": days[20] + "T07:00:00+00:00"}]
    utc = evaluate(root, spec)
    spec["decisions"][0]["decision_at"] = days[20] + "T15:00:00+08:00"
    shanghai = evaluate(root, spec)
    assert utc["documents"]["daily_metrics"] == shanghai["documents"]["daily_metrics"]
    # Label endpoint is the next synthetic exchange session; 14:59 Shanghai is not its close.
    report = evaluation.evaluate_daily(version_root=root, data_database=database,
        registry=registry, request=spec,
        now=datetime.fromisoformat(days[21] + "T06:59:00+00:00"))
    assert report["documents"]["daily_metrics"]["0"][0]["label_valid"] == 0
    assert report["documents"]["eligibility_ledger"][0]["label_reason"] \
        == "LABEL_NOT_MATURE_IN_FROZEN_VERSION"
    assert datetime.fromisoformat(report["computed_at"]).year >= 2026
    spec["decisions"][0]["decision_at"] = days[20] + "T06:59:59+00:00"
    with pytest.raises(ValueError, match="DECISION_CLOCK"):
        evaluate(root, spec)
    spec["decisions"][0]["decision_at"] = days[21] + "T08:00:00+00:00"
    with pytest.raises(ValueError, match="DECISION_CLOCK"):
        evaluate(root, spec)


def test_nonproduction_work_budget_rejects_before_reading_any_dataset(setup, monkeypatch):
    _, evaluate, request, _, _, _ = setup
    spec = deepcopy(request)
    spec["instruments"] = [f"SH{number:06d}" for number in range(6000)]

    def no_read(*args, **kwargs):
        pytest.fail("oversized evaluation must be rejected before input reads")

    monkeypatch.setattr(evaluation, "_read_dataset", no_read)
    with pytest.raises(ValueError, match="DEPENDENCY_CELL_BUDGET"):
        evaluate(Path("not-read"), spec)


def test_input_decode_reads_only_required_columns_and_calendar_window(setup, monkeypatch):
    build, evaluate, request, _, _, days = setup
    root, _ = build()
    spec = deepcopy(request)
    spec["ordered_factor_refs"] = [spec["ordered_factor_refs"][1]]  # six-close reversal only
    spec["decisions"] = spec["decisions"][:1]
    original = pq.read_table
    seen = []

    def read(path, *, columns, filters):
        assert columns == ["date", "close", "available_at", "observed_at"]
        assert filters == [("date", ">=", days[15]), ("date", "<=", days[21])]
        table = original(path, columns=columns, filters=filters)
        assert table.num_rows == 7
        seen.append(path)
        return table

    monkeypatch.setattr(pq, "read_table", read)
    report = evaluate(root, spec)
    assert len(seen) == 6
    for item in report["documents"]["source_snapshot"].values():
        from hashlib import sha256

        assert sha256(item["source"].encode("utf-8")).hexdigest() == item["sha256"]


def test_store_mutation_rechecks_live_disabled_after_construction(setup, tmp_path, monkeypatch):
    build, evaluate, request, _, _, _ = setup
    root, _ = build()
    report = evaluate(root)
    store = evaluation.FactorEvaluationStore(tmp_path / "reports.sqlite")
    identity = store.start(request, now=datetime.now(UTC))
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(ValueError, match="LIVE_DISABLED"):
        store.save(report)
    with pytest.raises(ValueError, match="LIVE_DISABLED"):
        store.start(request, now=datetime.now(UTC))
    with pytest.raises(ValueError, match="LIVE_DISABLED"):
        store.fail(identity, error_type="ValueError", now=datetime.now(UTC))


def test_failed_attempt_cannot_be_completed_and_failed_insert_leaves_no_partial_report(
    setup, tmp_path,
):
    build, evaluate, request, _, _, _ = setup
    root, _ = build()
    store = evaluation.FactorEvaluationStore(tmp_path / "attempts.sqlite")
    identity = store.start(request, now=datetime.now(UTC))
    store.fail(identity, error_type="ValueError", now=datetime.now(UTC))
    report = evaluate(root)
    report["evaluation_id"] = identity
    with pytest.raises(ValueError, match="FAILED_ATTEMPT"):
        store.save(report)
    report["evaluation_id"] = "factor-eval-" + "f" * 32
    with sqlite3.connect(store.database) as connection:
        connection.execute("CREATE TRIGGER reject_report BEFORE INSERT ON factor_evaluations "
                           "BEGIN SELECT RAISE(ABORT,'controlled failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="controlled failure"):
        store.save(report)
    with sqlite3.connect(store.database) as connection:
        assert connection.execute("SELECT count(*) FROM factor_evaluations").fetchone()[0] == 0


@pytest.mark.parametrize("mutation", ["arbitrary_code", "noncontiguous", "wrong_basis", "live"])
def test_unsupported_or_unsafe_contracts_rejected(setup, monkeypatch, mutation):
    build, evaluate, request, _, registry, _ = setup
    root, _ = build()
    spec = deepcopy(request)
    if mutation == "arbitrary_code":
        spec["python"] = "raise AssertionError('must never run')"
    elif mutation == "noncontiguous":
        del spec["decisions"][1]
    elif mutation == "wrong_basis":
        spec["ordered_factor_refs"] = list(registry.register_builtins(source_revision="a" * 40,
            price_basis="SYNTHETIC_UNADJUSTED", now=datetime.now(UTC)))
    else:
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(ValueError):
        evaluate(root, spec)
