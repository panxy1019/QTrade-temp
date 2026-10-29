"""Independent synthetic READY integration and numerical/temporal acceptance.

No production data, service, broker, existing test fixture or metric implementation
is used as the oracle. Content hashes provide consistency, never a signature.
"""

from __future__ import annotations

import json
import math
import sqlite3
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from itertools import combinations
from statistics import mean, pstdev, stdev
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.data_versions import SCHEMA, DataVersionCatalog, content_id, file_hash
from qtrade.research.factor_evaluation import (
    FactorEvaluationStore,
    evaluate_daily,
    validate_report,
)
from qtrade.research.factor_registry import FactorRegistry


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _reseal(report):
    report["report_manifest"] = {name: _digest(value)
                                 for name, value in report["documents"].items()}
    report["specification_sha256"] = report["report_manifest"]["specification"]
    report["report_manifest_sha256"] = _digest(report["report_manifest"])
    return report


def _price(number, index):
    # The first two securities deliberately tie throughout the window.
    number = max(2, number)
    return (Decimal(100 + number * 7) + Decimal(index * (number - 3)) / 50
            + Decimal((index * (number + 1)) % 9) / 10
            + Decimal(index * index * (number % 3)) / 1000)


@pytest.fixture
def contract(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    days, day = [], date(2021, 1, 4)
    while len(days) < 32:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    instruments = [f"SH60010{number}" for number in range(1, 9)]
    registry = FactorRegistry(tmp_path / "factors.sqlite")
    refs = registry.register_builtins(source_revision="b" * 40,
        price_basis="QLIB_NORMALIZED_ADJUSTMENT_UNCERTIFIED", now=datetime.now(UTC))
    database, archive = tmp_path / "versions.sqlite", tmp_path / "archive"
    catalog = DataVersionCatalog(database, archive)

    def build(*, nonce="a", transform=None):
        stage = archive / ("stage-" + nonce)
        (stage / "provider/calendars").mkdir(parents=True)
        (stage / "provider/instruments").mkdir()
        for name in ("day.txt", "day_future.txt"):
            (stage / "provider/calendars" / name).write_text("\n".join(days) + "\n")
        intervals = "".join(f"{s}\t{days[0]}\t{days[-2]}\n" for s in instruments)
        for name in ("all.txt", "workflow_supported.txt"):
            (stage / "provider/instruments" / name).write_text(intervals)
        rows = {}
        for number, instrument in enumerate(instruments, 1):
            selected = []
            for index, session in enumerate(days[:-1]):
                row = {"date": session, "close": str(_price(number, index)),
                       "available_at": session + "T07:01:00+00:00",
                       "observed_at": session + "T07:02:00+00:00"}
                if transform is not None:
                    row = transform(instrument, index, row)
                if row is not None:
                    selected.append(row)
            folder = stage / "stocks" / instrument[2:]
            folder.mkdir(parents=True)
            pq.write_table(pa.Table.from_pylist(selected), folder / "daily.parquet")
            rows[instrument] = {row["date"]: row for row in selected}
        symbols = [s[2:] for s in instruments]
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
                                           if k.startswith("provider/")}, {}),
            "actual_end": days[-2], "checks_passed": True, "placeholder_all_missing": True,
            "unknown_current_prices_missing": True})
        (stage / "provider-reload.json").write_text(json.dumps(reload))
        files["provider-reload.json"] = file_hash(stage / "provider-reload.json")
        bindings = {"source_snapshot_sha256": _digest({"synthetic_nonce": nonce})}
        identity = content_id(files, bindings)
        version = signed({**AUTHORITY, "schema_version": SCHEMA, "version_id": identity,
            "state": "RESEARCH_READY_QUALIFIED_SUBSET", "frequency": "day",
            "historical_pit_certified": False, "external_execution_ready": False,
            "created_at": datetime.now(UTC).isoformat(), "actual_end": days[-2],
            "current_symbols": symbols, "historical_symbols": symbols, "blocked_symbols": [],
            "calendar_boundaries": [days[-1]], "files": files, "bindings": bindings})
        (stage / "version.json").write_text(json.dumps(version))
        root = archive / "versions" / identity
        root.parent.mkdir(exist_ok=True)
        stage.rename(root)
        catalog.admit(identity, now=datetime.now(UTC))
        return root, rows

    request = {"ordered_factor_refs": list(refs), "instruments": instruments,
        "decisions": [{"session": s, "decision_at": s + "T08:00:00+00:00"}
                      for s in days[20:25]],
        "label_horizon_sessions": 1, "knowledge_mode": "OBSERVED_SYSTEM", "quantiles": 3,
        "min_pairs": 3, "stability_block_sessions": 2, "source_revision": "c" * 40,
        "revalidates_evaluation_id": None}

    def evaluate(root, *, spec=None, barrier=None, identity=None):
        return evaluate_daily(version_root=root, data_database=database,
            registry=FactorRegistry(registry.database, read_only=True),
            request=request if spec is None else spec,
            now=datetime.now(UTC) if barrier is None else barrier, evaluation_id=identity)

    return SimpleNamespace(build=build, evaluate=evaluate, request=request, days=days,
                           registry=registry, instruments=instruments, database=database)


def _ranks(values):
    ordered = sorted(values)
    return [Decimal(ordered.index(v) + 1 + len(ordered) - ordered[::-1].index(v)) / 2
            for v in values]


def _pearson(left, right):
    x, y = [float(v) for v in left], [float(v) for v in right]
    mx, my = mean(x), mean(y)
    return sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / math.sqrt(
        sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))


def _assert_near(actual, expected):
    assert actual is not None
    assert float(actual) == pytest.approx(float(expected), abs=2e-13)


def _scores(rows, instruments, days, position):
    base = [{}, {}, {}]
    for instrument in instruments:
        prices = [Decimal(rows[instrument][s]["close"]) for s in days[position-20:position+1]]
        base[0][instrument] = prices[-1] / prices[0] - 1
        base[1][instrument] = -(prices[-1] / prices[-6] - 1)
        base[2][instrument] = -Decimal(str(pstdev(
            float(prices[i] / prices[i-1] - 1) for i in range(1, 21))))
    ranks = [_ranks([values[s] for s in instruments]) for values in base]
    composite = {s: sum(((r[i] - 1)/(len(instruments)-1) for r in ranks), Decimal(0)) / 3
                 for i, s in enumerate(instruments)}
    return [*base, composite]


def test_four_factor_metrics_match_independent_formula_rank_group_stability_oracles(contract):
    root, rows = contract.build()
    report = contract.evaluate(root)
    assert validate_report(report) == report
    docs = report["documents"]
    observed_ic, observed_rank, spreads, previous = {}, {}, {}, {}
    for offset, position in enumerate(range(20, 25)):
        session = contract.days[position]
        values = _scores(rows, contract.instruments, contract.days, position)
        labels = {s: Decimal(rows[s][contract.days[position+1]]["close"])
                  / Decimal(rows[s][session]["close"]) - 1 for s in contract.instruments}
        for factor, scores in enumerate(values):
            key, instruments = str(factor), contract.instruments
            day = docs["daily_metrics"][key][offset]
            samples = {s["instrument"]: s for s in docs["samples"]
                       if s["session"] == session and s["factor_index"] == factor}
            for instrument in instruments:
                _assert_near(samples[instrument]["factor_value"], scores[instrument])
                assert Decimal(samples[instrument]["raw_label"]) == labels[instrument]
            ic = _pearson([scores[s] for s in instruments], [labels[s] for s in instruments])
            rank = _pearson(_ranks([scores[s] for s in instruments]),
                            _ranks([labels[s] for s in instruments]))
            _assert_near(day["ic"], ic)
            _assert_near(day["rank_ic"], rank)
            observed_ic.setdefault(key, []).append(ic)
            observed_rank.setdefault(key, []).append(rank)
            ordered = sorted(instruments, key=lambda s: (scores[s], s))
            groups = [ordered[i * 8 // 3:(i+1) * 8 // 3] for i in range(3)]
            assert [group["instruments"] for group in day["groups"]] == groups
            for actual, members in zip(day["groups"], groups, strict=True):
                assert Decimal(actual["mean_label"]) == sum(labels[s] for s in members)/len(members)
            spread = sum(labels[s] for s in groups[-1])/3 - sum(labels[s] for s in groups[0])/2
            assert Decimal(day["long_short_spread"]) == spread
            spreads.setdefault(key, []).append(spread)
            if offset:
                assert Decimal(day["top_group_turnover"]) == Decimal(
                    len(set(groups[-1]) - previous[key]))/3
            else:
                assert day["top_group_turnover"] is None
            previous[key] = set(groups[-1])
        for left, right in combinations(range(4), 2):
            actual = next(r for r in docs["redundancy_daily"] if r["session"] == session
                          and r["left_factor_index"] == left and r["right_factor_index"] == right)
            assert actual["instruments"] == contract.instruments and actual["pairs"] == 8
            _assert_near(actual["pearson"], _pearson(list(values[left].values()),
                                                    list(values[right].values())))
            _assert_near(actual["spearman"], _pearson(_ranks(list(values[left].values())),
                                                     _ranks(list(values[right].values()))))
    for key, series in observed_ic.items():
        summary = docs["summary"]["factors"][key]
        assert summary["candidate_pairs"] == summary["valid_pairs"] == 40
        _assert_near(summary["ic"]["mean"], mean(series))
        _assert_near(summary["ic"]["sample_std_ddof1"], stdev(series))
        _assert_near(summary["ic"]["ir"], mean(series)/stdev(series))
        _assert_near(summary["rank_ic"]["mean"], mean(observed_rank[key]))
        assert [block["requested_days"] for block in docs["stability"][key]] == [2, 2, 1]
        for index, block in enumerate(docs["stability"][key]):
            selected = series[index*2:index*2+2]
            _assert_near(block["ic"]["mean"], mean(selected))
            assert block["ic"]["valid_days"] == len(selected)
        assert docs["stability"][key][-1]["ic"]["ir_reason"] == "INSUFFICIENT_DAYS"
    assert len(docs["summary"]["redundancy"]) == 6
    assert docs["summary"]["historical_computation_claim"] is False
    for limitation in ("HISTORICAL_PIT_NOT_CERTIFIED",
                       "CURRENT_LIQUIDITY_UNIVERSE_SURVIVORSHIP_AND_SELECTION_BIAS",
                       "CLOSE_LABEL_DIAGNOSTIC_NOT_EXECUTED_PORTFOLIO_NET_RETURN"):
        assert limitation in docs["summary"]["limitations"]


@pytest.mark.parametrize("mutation,reason", [
    ("unknown_id", "REFERENCE_INVALID"), ("unknown_version", "VERSION_NOT_REGISTERED"),
    ("latest", "REFERENCE_INVALID"), ("digest", "REFERENCE_IDENTITY_MISMATCH"),
    ("missing", "REFERENCE_INVALID"), ("duplicate", "UNIQUE_FACTOR_REFS_REQUIRED"),
])
def test_request_never_resolves_unknown_or_implicit_factor_versions(contract, mutation, reason):
    root, _ = contract.build()
    request = deepcopy(contract.request)
    ref = request["ordered_factor_refs"][0]
    if mutation == "unknown_id":
        ref["factor_id"] = "unregistered.factor"
    elif mutation == "unknown_version":
        ref["factor_version"] = "0" * 64
    elif mutation == "latest":
        ref["factor_version"] = "latest"
    elif mutation == "digest":
        ref["definition_sha256"] = "f" * 64
    elif mutation == "missing":
        del ref["definition_sha256"]
    else:
        request["ordered_factor_refs"][1] = deepcopy(ref)
    with pytest.raises(ValueError, match=reason):
        contract.evaluate(root, spec=request)


def test_composite_dependency_must_still_exist_at_evaluation_boundary(contract):
    root, _ = contract.build()
    request = deepcopy(contract.request)
    request["ordered_factor_refs"] = request["ordered_factor_refs"][-1:]
    with sqlite3.connect(contract.registry.database) as connection:
        connection.execute("DELETE FROM factor_definitions WHERE factor_id=?",
                           (contract.request["ordered_factor_refs"][0]["factor_id"],))
    with pytest.raises(ValueError, match="DEPENDENCY_NOT_REGISTERED"):
        contract.evaluate(root, spec=request)


@pytest.mark.parametrize("clock", ["available_at", "observed_at"])
def test_feature_clock_exact_barrier_and_one_microsecond_late_are_distinct(contract, clock):
    decision = contract.request["decisions"][0]["decision_at"]

    def transform(instrument, index, row):
        if index == 20 and instrument in contract.instruments[:3]:
            number = contract.instruments.index(instrument)
            row[clock] = (decision if number == 0 else
                          decision.replace("00+00:00", "00.000001+00:00") if number == 1 else None)
        return row

    root, _ = contract.build(transform=transform)
    report = contract.evaluate(root)
    facts = [r for r in report["documents"]["eligibility_ledger"]
             if r["session"] == contract.days[20] and r["factor_index"] == 0]
    assert [r["feature_reason"] for r in facts[:3]] == [None,
        "DEPENDENCY_NOT_KNOWN_AT_DECISION", "DEPENDENCY_AVAILABILITY_UNKNOWN"]
    assert report["documents"]["daily_metrics"]["0"][0]["feature_valid"] == 6
    retrospective = deepcopy(contract.request)
    retrospective["knowledge_mode"] = "RETROSPECTIVE_RECONSTRUCTION"
    posthoc = contract.evaluate(root, spec=retrospective)
    assert validate_report(posthoc) == posthoc
    assert posthoc["documents"]["daily_metrics"]["0"][0]["feature_valid"] == 8


@pytest.mark.parametrize("clock", ["available_at", "observed_at"])
def test_label_clock_later_than_evaluation_barrier_is_not_a_valid_pair(contract, clock):
    barrier = datetime.fromisoformat(contract.days[21] + "T08:00:00+00:00")

    def transform(instrument, index, row):
        if index == 21 and instrument == contract.instruments[0]:
            row[clock] = (barrier + timedelta(microseconds=1)).isoformat()
        return row

    root, _ = contract.build(transform=transform)
    request = deepcopy(contract.request)
    request["decisions"] = request["decisions"][:1]
    docs = contract.evaluate(root, spec=request, barrier=barrier)["documents"]
    assert docs["daily_metrics"]["0"][0]["label_valid"] == 7
    assert docs["eligibility_ledger"][0]["label_reason"] == "LABEL_NOT_AVAILABLE_AT_EVALUATION"
    assert docs["samples"][0]["factor_value"] is not None
    assert docs["samples"][0]["raw_label"] is None


def test_partial_coverage_preserves_scope_and_separate_feature_label_reasons(contract):
    def transform(instrument, index, row):
        if instrument in contract.instruments[:2] and index == 10:
            return None
        if instrument == contract.instruments[2] and index == 19:
            row["close"] = "0"
        if instrument == contract.instruments[3] and index == 21:
            return None
        return row

    root, _ = contract.build(transform=transform)
    docs = contract.evaluate(root)["documents"]
    assert len(docs["samples"]) == len(docs["eligibility_ledger"]) == 8*5*4
    first = docs["daily_metrics"]["0"][0]
    assert (first["candidates"], first["feature_valid"], first["label_valid"], first["pairs"]) \
        == (8, 5, 7, 4)
    assert docs["daily_metrics"]["1"][0]["feature_valid"] == 7
    reasons = [row["feature_reason"] for row in docs["eligibility_ledger"][:8]]
    assert reasons[:3] == ["MISSING_COMMON_SESSION_CLOSE", "MISSING_COMMON_SESSION_CLOSE",
                           "INVALID_CLOSE"]
    assert docs["eligibility_ledger"][3]["label_reason"] == "LABEL_CLOSE_MISSING_OR_INVALID"
    assert docs["specification"]["universe"]["instruments"] == contract.instruments


def test_future_label_prices_never_change_feature_values(contract):
    original, _ = contract.build()
    changed, _ = contract.build(nonce="b", transform=lambda s, i, row:
        {**row, "close": str(Decimal(row["close"]) * (i + 1))} if i == 21 else row)
    request = deepcopy(contract.request)
    request["decisions"] = request["decisions"][:1]
    before = contract.evaluate(original, spec=request)["documents"]
    after = contract.evaluate(changed, spec=request)["documents"]
    assert before["specification"]["data_binding"] != after["specification"]["data_binding"]
    assert [s["factor_value"] for s in before["samples"]] == [
        s["factor_value"] for s in after["samples"]]
    assert [s["raw_label"] for s in before["samples"]] != [s["raw_label"] for s in after["samples"]]


def test_no_mature_labels_means_null_metrics_while_factor_redundancy_stays_separate(contract):
    root, _ = contract.build()
    request = deepcopy(contract.request)
    request["decisions"] = request["decisions"][:1]
    barrier = datetime.fromisoformat(contract.days[21] + "T06:59:59.999999+00:00")
    report = contract.evaluate(root, spec=request, barrier=barrier)
    assert validate_report(report) == report
    assert report["state"] == "INSUFFICIENT_VALID_SAMPLE"
    docs = report["documents"]
    for days in docs["daily_metrics"].values():
        daily = days[0]
        assert (daily["feature_valid"], daily["label_valid"], daily["pairs"]) == (8, 0, 0)
        assert daily["ic"] is daily["rank_ic"] is daily["long_short_spread"] is None
        assert daily["correlation_reason"] == "INSUFFICIENT_PAIRS"
        assert daily["groups"] == []
    assert all(s["raw_label"] is None for s in docs["samples"])
    assert all(r["pairs"] == 8 and r["pearson"] is not None for r in docs["redundancy_daily"])
    assert all(r["label_reason"] == "LABEL_NOT_MATURE_IN_FROZEN_VERSION"
               for r in docs["eligibility_ledger"])


def test_same_experiment_id_cannot_replace_valid_old_results(contract, tmp_path):
    root, _ = contract.build()
    old = contract.evaluate(root)
    store = FactorEvaluationStore(tmp_path / "reports.sqlite")
    store.save(old)
    with sqlite3.connect(store.database) as connection:
        original = connection.execute(
            "SELECT payload,payload_sha256 FROM factor_evaluations").fetchone()
    newer, _ = contract.build(nonce="b", transform=lambda s, i, row:
                              {**row, "close": str(Decimal(row["close"])*2)} if i == 21 else row)
    replacement = contract.evaluate(newer, identity=old["evaluation_id"])
    assert replacement["specification_sha256"] != old["specification_sha256"]
    with pytest.raises(FileExistsError, match="IMMUTABLE_ID_EXISTS"):
        store.save(replacement)
    assert store.get(old["evaluation_id"]) == old
    with sqlite3.connect(store.database) as connection:
        saved = connection.execute(
            "SELECT payload,payload_sha256 FROM factor_evaluations").fetchone()
        assert saved == original
    request = deepcopy(contract.request)
    request["revalidates_evaluation_id"] = old["evaluation_id"]
    linked = contract.evaluate(newer, spec=request)
    store.save(linked)
    assert store.get(linked["evaluation_id"])["documents"]["specification"]["request"][
        "revalidates_evaluation_id"] == old["evaluation_id"]


@pytest.mark.parametrize("mutation", ["future_dependency_clock", "wrong_history_grid",
    "future_label_endpoint", "available_one_microsecond_late", "observed_one_microsecond_late",
    "unknown_dependency_clock", "missing_dependency_clock", "wrong_dependency_session",
    "false_known_claim", "wrong_historical_label_endpoint"])
def test_rehashed_temporal_contract_tampering_is_rejected(contract, mutation):
    root, _ = contract.build()
    report = contract.evaluate(root)
    ledger = report["documents"]["eligibility_ledger"][0]
    if mutation == "future_dependency_clock":
        ledger["dependency_clocks"][-1]["observed_at"] = "2099-01-01T00:00:00+00:00"
    elif mutation == "wrong_history_grid":
        ledger["history_sessions"][-1] = "2099-01-01"
    elif mutation == "future_label_endpoint":
        ledger["label_exit_session"] = "2099-01-01"
    elif mutation in {"available_one_microsecond_late", "observed_one_microsecond_late"}:
        clock = "available_at" if mutation.startswith("available") else "observed_at"
        decision = datetime.fromisoformat(contract.request["decisions"][0]["decision_at"])
        ledger["dependency_clocks"][-1][clock] = (decision + timedelta(microseconds=1)).isoformat()
    elif mutation == "unknown_dependency_clock":
        ledger["dependency_clocks"][-1]["observed_at"] = None
    elif mutation == "missing_dependency_clock":
        ledger["dependency_clocks"].pop()
    elif mutation == "wrong_dependency_session":
        ledger["dependency_clocks"][-1]["session"] = contract.days[19]
    elif mutation == "false_known_claim":
        ledger["dependencies_known_at_barrier"] = False
    else:
        ledger["label_exit_session"] = contract.days[22]
    with pytest.raises(ValueError, match="ARCHIVED_.*(?:CLOCK|GRID|LABEL)"):
        validate_report(_reseal(report))


def test_archived_label_maturity_is_checked_against_real_session_close(contract):
    root, _ = contract.build()
    request = deepcopy(contract.request)
    request["decisions"] = request["decisions"][:1]
    report = contract.evaluate(root, spec=request)
    # A plausible historical instant, not a special far-future sentinel.
    report["documents"]["specification"]["label"]["maturity_barrier"] = (
        contract.days[21] + "T06:59:59.999999+00:00")
    with pytest.raises(ValueError, match="ARCHIVED_LABEL_MATURITY"):
        validate_report(_reseal(report))


@pytest.mark.parametrize("mutation", ["calendar_hash", "calendar_scope", "universe",
                                    "factor_source", "missing_evaluator_source"])
def test_rehashed_spec_source_and_scope_must_still_match_frozen_contract(contract, mutation):
    root, _ = contract.build()
    report = contract.evaluate(root)
    spec = report["documents"]["specification"]
    if mutation == "calendar_hash":
        spec["calendar_sha256"] = "0" * 64
    elif mutation == "calendar_scope":
        spec["calendar"].pop(0)
        spec["calendar_sha256"] = _digest(spec["calendar"])
    elif mutation == "universe":
        spec["universe"]["instruments"] = spec["universe"]["instruments"][:-1]
    elif mutation == "missing_evaluator_source":
        del report["documents"]["source_snapshot"]["research/factor_evaluation.py"]
        del spec["runtime_source_sha256"]["research/factor_evaluation.py"]
    else:
        source = report["documents"]["source_snapshot"]["research/factors.py"]
        source["source"] += "\n# altered archived source\n"
        source["sha256"] = sha256(source["source"].encode()).hexdigest()
        spec["runtime_source_sha256"]["research/factors.py"] = source["sha256"]
    with pytest.raises(ValueError,
                       match="ARCHIVED_(?:CALENDAR_GRID|FACTOR_SOURCE_OR_SCOPE|SOURCE_INVENTORY)"):
        validate_report(_reseal(report))
