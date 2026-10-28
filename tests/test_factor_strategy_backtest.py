"""Nonproduction score replay, causality and native Qlib adapter acceptance.

READY/registries are actual sealed synthetic files. Tests using backend doubles
are unit boundary checks; only explicit native tests claim Qlib execution.
"""
# ruff: noqa: F811 -- pytest fixture imports intentionally match fixture parameters.

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from datetime import datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
from test_factor_research_worker import archive  # noqa: F401
from test_factor_strategy_contract import setup as strategy_data  # noqa: F401
from test_step2_factor_contract_independent import _scores
from test_step2_factor_contract_independent import contract as factor_data  # noqa: F401

from qtrade.research import factor_strategy_backtest as adapter
from qtrade.research.factor_strategy_contract import freeze_strategy
from qtrade.research.workflow import digest
from scripts import run_factor_strategy_backtest as runner


def score_case(setup, *, selected=0, transform=None, mode=None):
    root, report = setup.root, setup.report
    if transform is not None or mode is not None:
        root, _ = setup.data.build(nonce="b", transform=transform)
        request = deepcopy(setup.data.request)
        if mode is not None:
            request["knowledge_mode"] = mode
        report = setup.data.evaluate(root, spec=request)
    spec = deepcopy(setup.request)
    spec.update(
        factor_ref=setup.data.request["ordered_factor_refs"][selected],
        signal_kind="FACTOR_COMPOSITE" if selected == 3 else "FACTOR_SINGLE",
        evaluation_ref={
            key: report[key]
            for key in ("evaluation_id", "specification_sha256", "report_manifest_sha256")
        },
    )
    definition = freeze_strategy(
        spec, factors=setup.data.registry, evaluation=report, source_revision="e" * 40
    )
    scores = adapter.prepare_scores(
        definition,
        report,
        factors=setup.data.registry,
        version_root=root,
        data_database=setup.data.database,
    )
    return definition, scores, root, report


@pytest.mark.parametrize("selected", [0, 1, 2, 3])
def test_all_four_scores_match_independent_price_oracle_and_next_session(strategy_data, selected):
    setup = strategy_data
    before = {p: p.read_bytes() for p in setup.root.rglob("*") if p.is_file()}
    original = digest(setup.report)
    definition, scores, root, _ = score_case(setup, selected=selected)
    raw = {
        code: {
            str(row["date"]): row
            for row in pq.read_table(root / "stocks" / code[2:] / "daily.parquet").to_pylist()
        }
        for code in setup.data.instruments
    }
    for item in scores["schedule"]:
        position = setup.data.days.index(item["session"])
        oracle = _scores(raw, setup.data.instruments, setup.data.days, position)[selected]
        assert item["execution_session"] == setup.data.days[position + 1]
        assert datetime.fromisoformat(item["execution_at"]) > datetime.fromisoformat(
            item["decision_at"]
        )
        for row in (r for r in scores["rows"] if r["session"] == item["session"]):
            assert float(row["score"]) == pytest.approx(float(oracle[row["instrument"]]), abs=2e-13)
    prediction, _ = adapter.score_series(scores)
    assert prediction.index.names == ["datetime", "instrument"]
    assert str(prediction.index[0][0].date()) == scores["schedule"][0]["session"]
    assert not prediction.index.has_duplicates
    assert definition["signal"]["adapter_implemented"] is False
    assert definition["execution"]["backtest_implemented"] is False
    assert digest(setup.report) == original
    assert all(p.read_bytes() == value for p, value in before.items())


def test_future_diagnostic_label_change_does_not_change_any_score(strategy_data):
    setup = strategy_data
    _, original, _, _ = score_case(setup)
    last_label = setup.data.days[25]

    def transform(code, index, row):
        return (
            {**row, "close": str(Decimal(row["close"]) * 2)} if row["date"] == last_label else row
        )

    _, changed, _, _ = score_case(setup, transform=transform)
    assert [(r["session"], r["instrument"], r["score"]) for r in original["rows"]] == [
        (r["session"], r["instrument"], r["score"]) for r in changed["rows"]
    ]
    assert [r["raw_diagnostic_label"] for r in original["rows"]] != [
        r["raw_diagnostic_label"] for r in changed["rows"]
    ]


def test_null_label_does_not_remove_feature_score(strategy_data):
    setup = strategy_data
    first = setup.data.instruments[0]
    _, scores, _, _ = score_case(
        setup, transform=lambda code, i, row: None if code == first and i == 25 else row
    )
    target = next(
        row
        for row in scores["rows"]
        if row["session"] == setup.data.days[24] and row["instrument"] == first
    )
    assert target["raw_diagnostic_label"] is None and target["score"] is not None
    prediction, labels = adapter.score_series(scores)
    key = (pd.Timestamp(target["session"]), target["instrument"])
    assert key in prediction.index and pd.isna(labels.loc[key])


@pytest.mark.parametrize("clock", ["available_at", "observed_at"])
def test_late_feature_clock_excludes_observed_but_retrospective_discloses(strategy_data, clock):
    setup = strategy_data
    first = setup.data.instruments[0]

    def transform(code, i, row):
        return {**row, clock: row["date"] + "T09:00:00+00:00"} if code == first and i == 20 else row

    _, scores, _, _ = score_case(setup, transform=transform)
    target = next(
        row
        for row in scores["rows"]
        if row["session"] == setup.data.days[20] and row["instrument"] == first
    )
    assert (
        target["score"] is None and target["feature_reason"] == "DEPENDENCY_NOT_KNOWN_AT_DECISION"
    )
    # A different frozen fixture/version is required, never mutate the prior READY archive.
    second_root, _ = setup.data.build(nonce="c", transform=transform)
    request = deepcopy(setup.data.request)
    request["knowledge_mode"] = "RETROSPECTIVE_RECONSTRUCTION"
    report = setup.data.evaluate(second_root, spec=request)
    spec = deepcopy(setup.request)
    spec["evaluation_ref"] = {
        key: report[key]
        for key in ("evaluation_id", "specification_sha256", "report_manifest_sha256")
    }
    definition = freeze_strategy(
        spec, factors=setup.data.registry, evaluation=report, source_revision="e" * 40
    )
    retrospective = adapter.prepare_scores(
        definition,
        report,
        factors=setup.data.registry,
        version_root=second_root,
        data_database=setup.data.database,
    )
    row = next(
        r
        for r in retrospective["rows"]
        if r["session"] == setup.data.days[20] and r["instrument"] == first
    )
    assert row["score"] is not None and row["dependencies_known_at_barrier"] is False
    assert retrospective["knowledge_mode"] == "RETROSPECTIVE_RECONSTRUCTION"


def test_missing_common_grid_is_excluded_without_compressing_history(strategy_data):
    setup = strategy_data
    first = setup.data.instruments[0]
    _, scores, _, _ = score_case(
        setup, transform=lambda code, i, row: None if code == first and i == 10 else row
    )
    missing = [r for r in scores["rows"] if r["instrument"] == first]
    assert all(
        r["score"] is None and r["feature_reason"] == "MISSING_COMMON_SESSION_CLOSE"
        for r in missing
    )


def test_actual_ready_mutation_rejected(strategy_data):
    setup = strategy_data
    definition = setup.freeze()
    path = setup.root / "stocks" / setup.data.instruments[0][2:] / "daily.parquet"
    path.write_bytes(path.read_bytes() + b"drift")
    with pytest.raises(ValueError):
        adapter.prepare_scores(
            definition,
            setup.report,
            factors=setup.data.registry,
            version_root=setup.root,
            data_database=setup.data.database,
        )


def quote_fixture():
    index = pd.MultiIndex.from_product(
        [["SH600001"], pd.to_datetime(["2021-02-01"])], names=["instrument", "datetime"]
    )
    return pd.DataFrame(
        {
            "$open": [10.0],
            "$close": [10.1],
            "$factor": [1.0],
            "$buy_block": [0.0],
            "$sell_block": [0.0],
            "$capacity": [1000.0],
        },
        index=index,
    )


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("$factor", np.nan, "DISABLE_LOTS"),
        ("$capacity", np.nan, "MISSING"),
        ("$capacity", -1, "INVALID"),
        ("$buy_block", 2, "INVALID"),
        ("$sell_block", np.nan, "MISSING"),
        ("$open", np.nan, "NOT_EXPLICITLY_BLOCKED"),
    ],
)
def test_missing_or_invalid_execution_constraints_fail_closed(field, value, code):
    quotes = quote_fixture()
    quotes[field] = value
    with pytest.raises(ValueError, match=code):
        adapter.validate_quotes(quotes, ["SH600001"], ["2021-02-01"])


def test_missing_quote_requires_explicit_both_side_block_and_zero_capacity():
    quotes = quote_fixture()
    quotes["$open"] = np.nan
    quotes["$buy_block"] = quotes["$sell_block"] = 1.0
    quotes["$capacity"] = 0.0
    adapter.validate_quotes(quotes, ["SH600001"], ["2021-02-01"])


def test_native_config_reuses_frozen_costs_and_holding_policy(strategy_data):
    config = adapter.native_config(strategy_data.freeze())
    assert config["exchange"]["open_cost"] == 0.0008
    assert config["exchange"]["close_cost"] == 0.0013
    assert config["exchange"]["min_cost"] == 5
    assert config["portfolio"]["hold_thresh"] == 2
    assert config["exchange"]["deal_price"] == "open" and config["benchmark"] is None


def request_for(definition, identity="f" * 32):
    return {
        "schema_version": adapter.REQUEST_SCHEMA,
        "kind": adapter.KIND,
        "job_id": identity,
        "actor": "factor-backtest-fixture",
        "strategy_id": definition["strategy_id"],
        "strategy_version": definition["strategy_version"],
        "strategy_contract_sha256": definition["evidence_sha256"],
        "registration_job_id": "a" * 32,
        "data_version_id": definition["evaluation_context"]["data_binding"]["data_version_id"],
        "source_revision": "e" * 40,
        "live_trading_enabled": False,
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "FACTOR_STRATEGY_REGISTER_V1"),
        ("job_id", "a" * 32),
        ("strategy_version", "unknown"),
        ("live_trading_enabled", True),
        ("actor", "../../unsafe"),
        ("source_revision", True),
    ],
)
def test_new_backtest_request_never_aliases_registration_or_live(strategy_data, field, value):
    request = request_for(strategy_data.freeze())
    request[field] = value
    with pytest.raises(ValueError):
        adapter.validate_request(request)


def fake_native(provider, definition, scores):
    """Explicit unit boundary double: never actual Qlib/native acceptance evidence."""
    days = [row["execution_session"] for row in scores["schedule"]]
    report = pd.DataFrame(
        {
            "return": [0.01, -0.005, 0.02, -0.01, 0.005],
            "cost": [0.001] * 5,
            "turnover": [0.2] * 5,
            "bench": [0.0] * 5,
        },
        index=pd.to_datetime(days),
    )
    positions = [{"session": day, "cash": 1000000.0, "holdings": {}} for day in days]
    return report, positions, {"fixture_only": "BACKEND_UNIT_DOUBLE_NOT_NATIVE_EXECUTION"}


def fixture_writer(path, value):
    """Exclusive fixture I/O only; does not certify POSIX directory fsync on Windows."""
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, allow_nan=False)


@pytest.mark.parametrize("change", ["job", "contract", "active", "archive", "factors"])
def test_actual_registered_and_archive_drift_stops_before_backend(archive, monkeypatch, change):
    setup = archive.setup
    definition = setup.freeze()
    setup.register(definition)
    request = request_for(definition)
    if change == "job":
        request["registration_job_id"] = "b" * 32
    elif change == "contract":
        request["strategy_contract_sha256"] = "0" * 64
    elif change == "active":
        with sqlite3.connect(setup.database) as connection:
            connection.execute("UPDATE strategies SET status='APPROVED'")
    elif change == "archive":
        path = archive.folder / "index.json"
        path.write_bytes(path.read_bytes().replace(b'"templates"', b'"unknown"'))
    elif change == "factors":
        path = archive.folder / "factors.sqlite"
        path.write_bytes(path.read_bytes() + b"drift")
    archive.request_path.write_text(json.dumps(request))
    before = setup.database.read_bytes()
    monkeypatch.setattr(runner, "write_json_immutable", fixture_writer)
    monkeypatch.setattr(runner, "native_backtest", lambda *args: pytest.fail("backend executed"))
    with pytest.raises((ValueError, KeyError)):
        runner.run(
            base=archive.base,
            data_database=setup.data.database,
            strategy_database=setup.database,
            archive_root=archive.folder,
            request_path=archive.request_path,
            output=archive.output,
            source_revision="e" * 40,
        )
    assert setup.database.read_bytes() == before
    assert not (archive.output / "result.json").exists()
    assert json.loads((archive.output / "failure.json").read_bytes())["automatic_retry"] is False


@pytest.mark.skipif(__import__("sys").platform == "win32", reason="existing POSIX immutable writer")
def test_runner_uses_existing_registered_job_and_owned_state_without_original_writes(
    archive, monkeypatch
):
    setup = archive.setup
    definition = setup.freeze()
    setup.register(definition)
    before_db = setup.database.read_bytes()
    before_archive = {p: p.read_bytes() for p in archive.folder.rglob("*") if p.is_file()}
    request = request_for(definition)
    archive.request_path.write_text(json.dumps(request))
    monkeypatch.setattr(runner, "native_backtest", fake_native)
    result = runner.run(
        base=archive.base,
        data_database=setup.data.database,
        strategy_database=setup.database,
        archive_root=archive.folder,
        request_path=archive.request_path,
        output=archive.output,
        source_revision="e" * 40,
    )
    assert result == runner.verify_archive(archive.output)
    assert result["packages"]["fixture_only"].startswith("BACKEND_UNIT_DOUBLE")
    assert setup.database.read_bytes() == before_db
    assert all(p.read_bytes() == raw for p, raw in before_archive.items())
    assert len(setup.registry.research_jobs(definition["strategy_id"])) == 1
    expected_net = np.prod(1 + np.array([0.01, -0.005, 0.02, -0.01, 0.005]) - 0.001) - 1
    assert result["metrics"]["total_return_net"] == pytest.approx(expected_net)
    assert result["original_registration_modified"] is result["unseen_test_claim"] is False
    with pytest.raises(ValueError, match="NEW_OUTPUT"):
        runner.run(
            base=archive.base,
            data_database=setup.data.database,
            strategy_database=setup.database,
            archive_root=archive.folder,
            request_path=archive.request_path,
            output=archive.output,
            source_revision="e" * 40,
        )
    (archive.output / "diagnostics.json").write_bytes(b"[]")
    with pytest.raises(ValueError, match="HASH"):
        runner.verify_archive(archive.output)


@pytest.mark.parametrize("blocked", [False, True])
def test_real_native_qlib_first_execution_uses_previous_score_and_real_fees(
    strategy_data, tmp_path, blocked
):
    pytest.importorskip("qlib", reason="Native Qlib is absent; cannot claim execution acceptance")
    definition, scores, root, _ = score_case(strategy_data)
    provider = tmp_path / "native-fixture-provider"
    (provider / "calendars").mkdir(parents=True)
    (provider / "instruments").mkdir()
    days = strategy_data.data.days
    for name in ("day.txt", "day_future.txt"):
        (provider / "calendars" / name).write_text("\n".join(days) + "\n")
    codes = strategy_data.data.instruments
    for name in ("all.txt", "workflow_supported.txt"):
        (provider / "instruments" / name).write_text(
            "".join(f"{code}\t{days[0]}\t{days[-1]}\n" for code in codes)
        )
    first_day = scores["schedule"][0]["session"]
    best = max(
        (r for r in scores["rows"] if r["session"] == first_day),
        key=lambda row: Decimal(row["score"]),
    )["instrument"]
    for code in codes:
        values = pq.read_table(root / "stocks" / code[2:] / "daily.parquet").to_pylist()
        closes = [float(row["close"]) for row in values]
        closes += [closes[-1]] * (len(days) - len(closes))
        fields = {
            "open": closes,
            "close": closes,
            "factor": [1.0] * len(days),
            "volume": [1e7] * len(days),
            "capacity": [1e5] * len(days),
            "buy_block": [float(blocked and code == best)] * len(days),
            "sell_block": [0.0] * len(days),
        }
        folder = provider / "features" / code.lower()
        folder.mkdir(parents=True)
        for field, values in fields.items():
            np.array([0.0, *values], dtype="<f4").tofile(folder / (field + ".day.bin"))
    portfolio, positions, packages = adapter.native_backtest(provider, definition, scores)
    assert packages["pyqlib"] == "0.9.7"
    assert len(portfolio) == len(scores["schedule"])
    assert positions[0]["holdings"] and portfolio.iloc[0]["cost"] > 0
    if blocked:
        assert best not in positions[0]["holdings"]
    else:
        assert best in positions[0]["holdings"] and len(positions[0]["holdings"]) == 4
    assert all(amount % 100 == pytest.approx(0) for amount in positions[0]["holdings"].values())


def test_live_true_blocks_before_any_execution(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(ValueError, match="LIVE_DISABLED"):
        adapter.validate_request({})
