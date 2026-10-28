"""Independent factor/READY/report/registry integration; all state is synthetic."""
from __future__ import annotations

import json
import sqlite3
from contextlib import suppress
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_qlib_strategy_contract import admitted as qlib_data  # noqa: F401
from test_step2_factor_contract_independent import contract as factor_data  # noqa: F401

from qtrade.research import factor_strategy_contract as contracts
from qtrade.research.factor_evaluation import FactorEvaluationStore
from qtrade.research.strategy_contract import (
    bound_default_spec,
    ready_dataset,
)
from qtrade.research.strategy_contract import (
    freeze_strategy as freeze_qlib,
)
from qtrade.research.strategy_contract import (
    strategy_spec as qlib_spec,
)
from qtrade.strategies.registry import StrategyRegistry, StrategyRole, StrategyStatus


@pytest.fixture
def setup(factor_data, tmp_path):  # noqa: F811
    root, _ = factor_data.build()
    report = factor_data.evaluate(root)
    evaluations = FactorEvaluationStore(tmp_path / "evaluations.sqlite")
    reference = evaluations.save(report)
    request = {"name": "factor-momentum", "factor_ref": factor_data.request[
        "ordered_factor_refs"][0], "evaluation_ref": reference, "signal_kind": "FACTOR_SINGLE",
        "segments": {"train": [factor_data.days[0], factor_data.days[8]],
                     "valid": [factor_data.days[10], factor_data.days[18]],
                     "test": [factor_data.days[20], factor_data.days[30]]},
        "portfolio": {"method": "TopkDropout", "topk": 4, "n_drop": 1,
                      "hold_thresh": 2, "risk_degree": "0.8"},
        "execution": {"buy_cost_bps": "8", "sell_cost_bps": "13", "min_cost": "5",
                      "initial_cash": "1000000", "slippage_bps": "0"}, "parent": None}

    def freeze(spec=None, evidence=None):
        return contracts.freeze_strategy(deepcopy(request if spec is None else spec),
            factors=factor_data.registry, evaluation=report if evidence is None else evidence,
            source_revision="e" * 40)

    registry = StrategyRegistry(tmp_path / "strategies.sqlite")

    def register(definition, job="a" * 32, actor="independent", now=None):
        return contracts.register_strategy(registry, definition, factors=factor_data.registry,
            evaluations=evaluations, version_root=root, data_database=factor_data.database,
            job_id=job, actor=actor, now=now or datetime.now(UTC))

    return SimpleNamespace(data=factor_data, root=root, report=report, evaluations=evaluations,
        request=request, registry=registry, freeze=freeze, register=register,
        database=tmp_path / "strategies.sqlite")


def logical_dump(path):
    with sqlite3.connect(path) as connection:
        return tuple(connection.iterdump())


def test_single_factor_contract_binds_actual_ready_evaluation_and_registry_atomically(setup):
    definition = setup.freeze()
    assert contracts.validate_strategy(definition, verify_runtime_source=True) == definition
    record = setup.register(definition)
    assert (record.status, record.role) == (StrategyStatus.EXPERIMENT, StrategyRole.NONE)
    assert record.spec.parameters == (("contract_sha256", definition["evidence_sha256"]),)
    assert record.spec.factor_set == (setup.request["factor_ref"]["factor_id"] + "@"
                                     + setup.request["factor_ref"]["factor_version"],)
    assert setup.registry.research_contract(record.spec.strategy_id) == definition
    assert len(setup.registry.research_jobs(record.spec.strategy_id)) == 1
    assert len(setup.registry.transitions(record.spec.strategy_id)) == 1
    assert setup.registry.lineage(record.spec.strategy_id) is None
    context = definition["evaluation_context"]
    assert context["label"]["kind"] == "RAW_CLOSE_TO_CLOSE"
    assert context["diagnostic_cost_policy"]["executable_return_claim"] is False
    assert definition["execution"]["timing"] == "PREVIOUS_SIGNAL_NEXT_SESSION_OPEN"
    assert definition["execution"]["costs_applied_to_diagnostic"] is False
    assert definition["signal"]["adapter_implemented"] is False
    assert definition["split_policy"]["unseen_test_claim"] is False
    assert definition["paper_admission"] is definition["model_activation"] is False


def test_existing_composite_preserves_exact_dependency_dag_and_direction(setup):
    request = deepcopy(setup.request)
    request.update(factor_ref=setup.data.request["ordered_factor_refs"][3],
                   signal_kind="FACTOR_COMPOSITE")
    definition = setup.freeze(request)
    assert definition["signal"]["dependency_refs"] == setup.data.request["ordered_factor_refs"][:3]
    assert definition["factor_definition"]["parameters"]["weights"] == ["1/3"] * 3
    assert definition["signal"]["direction"] == "HIGH_SCORE_LONG_ONLY_NO_SIGN_SELECTION"
    setup.register(definition)


def test_repeated_jobs_keep_original_spec_clocks_and_do_not_duplicate_transition(setup):
    definition = setup.freeze()
    first = setup.register(definition)
    again = setup.register(definition, now=datetime.now(UTC))
    second = setup.register(definition, job="b" * 32, now=datetime.now(UTC))
    assert first == again == second
    assert len(setup.registry.research_jobs(first.spec.strategy_id)) == 2
    assert len(setup.registry.transitions(first.spec.strategy_id)) == 1


@pytest.mark.parametrize("change", ["unknown", "model", "unknown_factor", "unknown_version",
    "kind", "weights", "weekly", "arbitrary_cost", "float", "bool", "negative_cost",
    "slippage", "too_many_topk", "overlap", "out_of_ready", "non_session", "purge", "label_exit"])
def test_strict_builder_rejects_unknown_or_incompatible_requests(setup, change):
    request = deepcopy(setup.request)
    if change == "unknown":
        request["module_path"] = "untrusted.module"
    elif change == "model":
        request["signal_kind"] = "MODEL_SCORE"
    elif change == "unknown_factor":
        request["factor_ref"]["factor_id"] = "unregistered.factor"
    elif change == "unknown_version":
        request["factor_ref"]["factor_version"] = "f" * 64
    elif change == "kind":
        request["signal_kind"] = "FACTOR_COMPOSITE"
    elif change == "weights":
        request["weights"] = ["-1"]
    elif change == "weekly":
        request["portfolio"]["rebalance"] = "week"
    elif change == "arbitrary_cost":
        request["execution"]["cost_callable"] = "custom"
    elif change == "float":
        request["portfolio"]["risk_degree"] = 0.8
    elif change == "bool":
        request["portfolio"]["topk"] = True
    elif change == "negative_cost":
        request["execution"]["buy_cost_bps"] = "-1"
    elif change == "slippage":
        request["execution"]["slippage_bps"] = "2"
    elif change == "too_many_topk":
        request["portfolio"]["topk"] = 9
    elif change == "overlap":
        request["segments"]["valid"][0] = request["segments"]["train"][1]
    elif change == "out_of_ready":
        request["segments"]["test"][1] = setup.data.days[-1]
    elif change == "non_session":
        request["segments"]["train"][0] = "2021-01-09"
    elif change == "purge":
        request["segments"]["valid"][0] = setup.data.days[9]
    elif change == "label_exit":
        request["segments"]["test"][1] = setup.data.days[24]
    with pytest.raises(ValueError):
        setup.freeze(request)
    assert setup.registry.list() == ()


@pytest.mark.parametrize("change", ["factor", "evaluation_id", "manifest", "data", "label",
    "split", "coverage", "knowledge", "future", "portfolio", "execution", "source",
    "metric_source", "parent",
    "authority", "unknown"])
def test_rehashing_does_not_bypass_definition_or_local_evidence_checks(setup, change):
    definition = setup.freeze()
    if change == "factor":
        definition["spec"]["factor_ref"]["factor_version"] = "f" * 64
    elif change == "evaluation_id":
        definition["spec"]["evaluation_ref"]["evaluation_id"] = "factor-eval-" + "f" * 32
    elif change == "manifest":
        definition["spec"]["evaluation_ref"]["report_manifest_sha256"] = "f" * 64
    elif change == "data":
        definition["evaluation_context"]["data_binding"]["manifest_sha256"] = "f" * 64
    elif change == "label":
        definition["evaluation_context"]["label"]["formula"] = "next-open-net"
    elif change == "split":
        definition["evaluation_context"]["diagnostic_split_policy"]["unseen_test_claim"] = True
    elif change == "coverage":
        definition["evaluation_context"]["eligibility_ledger_sha256"] = "f" * 64
    elif change == "knowledge":
        definition["evaluation_context"]["knowledge_mode"] = "RETROSPECTIVE_RECONSTRUCTION"
    elif change == "future":
        definition["evaluation_context"]["computed_at"] = "2099-01-01T00:00:00+00:00"
    elif change == "portfolio":
        definition["portfolio"]["topk"] = 3
    elif change == "execution":
        definition["execution"]["rules_certified"] = True
    elif change == "source":
        definition["source"]["files"]["src/qtrade/research/factor_strategy_contract.py"] = "f" * 64
    elif change == "metric_source":
        definition["source"]["files"]["src/qtrade/research/metrics.py"] = "f" * 64
    elif change == "parent":
        definition["spec"]["parent"] = {"strategy_id": "factor-" + "f" * 64,
            "strategy_version": "f" * 64, "contract_sha256": "e" * 64, "reason": "change",
            "hypothesis": "new test", "change_summary": "topk"}
    elif change == "authority":
        definition["external_order_transmission"] = True
    elif change == "unknown":
        definition["unsafe_extension"] = True
    # Preserve mutations that are structurally valid by recomposing the entire
    # identity. Actual local store resolution must still reject invented facts.
    with suppress(ValueError):
        definition = contracts._compose(definition["spec"], definition["factor_definition"],
            definition["evaluation_context"], definition["source"]) if change in {
                "evaluation_id", "manifest", "coverage", "knowledge", "source", "metric_source",
                "parent"
            } else definition
    before = logical_dump(setup.database)
    with pytest.raises((ValueError, KeyError)):
        setup.register(definition)
    assert logical_dump(setup.database) == before


@pytest.mark.parametrize("field", ["name", "factor", "evaluation", "segments", "topk",
                                  "cost", "source", "parent"])
def test_every_declared_behavior_or_reference_changes_identity(setup, field):
    first = setup.freeze()
    request = deepcopy(setup.request)
    if field == "name":
        request["name"] = "different-name"
    elif field == "factor":
        request["factor_ref"] = setup.data.request["ordered_factor_refs"][1]
    elif field == "evaluation":
        report = setup.data.evaluate(setup.root)
        request["evaluation_ref"] = setup.evaluations.save(report)
        assert setup.freeze(request, report)["strategy_version"] != first["strategy_version"]
        return
    elif field == "segments":
        request["segments"]["train"][0] = setup.data.days[1]
    elif field == "topk":
        request["portfolio"]["topk"] = 3
    elif field == "cost":
        request["execution"]["buy_cost_bps"] = "9"
    elif field == "source":
        other = contracts.freeze_strategy(request, factors=setup.data.registry,
            evaluation=setup.report, source_revision="f" * 40)
        assert other["strategy_version"] != first["strategy_version"]
        return
    elif field == "parent":
        request["parent"] = parent_ref(first)
    assert setup.freeze(request)["strategy_version"] != first["strategy_version"]


def parent_ref(definition):
    return {"strategy_id": definition["strategy_id"],
        "strategy_version": definition["strategy_version"],
        "contract_sha256": definition["evidence_sha256"], "reason": "controlled change",
        "hypothesis": "compare predeclared topk", "change_summary": "change topk from 4 to 3"}


def test_parent_contract_and_lineage_are_exact_and_atomic(setup):
    parent = setup.freeze()
    setup.register(parent)
    request = deepcopy(setup.request)
    request["parent"] = parent_ref(parent)
    request["portfolio"]["topk"] = 3
    child = setup.freeze(request)
    record = setup.register(child, job="b" * 32)
    lineage = setup.registry.lineage(record.spec.strategy_id)
    assert lineage.parent_strategy_id == parent["strategy_id"]
    assert lineage.promotion_result == "NOT_REQUESTED"
    assert setup.register(child, job="c" * 32) == record
    assert setup.registry.lineage(record.spec.strategy_id) == lineage
    assert len(setup.registry.transitions(record.spec.strategy_id)) == 1


@pytest.mark.parametrize("damage", ["missing", "wrong_hash", "wrong_version", "wrong_family",
                                    "corrupt_contract", "corrupt_spec"])
def test_parent_must_exist_with_exact_family_version_and_contract(setup, damage):
    parent = setup.freeze()
    if damage != "missing":
        setup.register(parent)
    request = deepcopy(setup.request)
    request["parent"] = parent_ref(parent)
    if damage == "wrong_hash":
        request["parent"]["contract_sha256"] = "f" * 64
    elif damage == "wrong_version":
        request["parent"].update(strategy_version="f" * 64, strategy_id="factor-" + "f" * 64)
    elif damage == "wrong_family":
        with sqlite3.connect(setup.database) as connection:
            connection.execute("UPDATE strategies SET family='OTHER'")
    elif damage == "corrupt_contract":
        with sqlite3.connect(setup.database) as connection:
            connection.execute("UPDATE strategy_research_contracts SET contract_json='{}'")
    elif damage == "corrupt_spec":
        with sqlite3.connect(setup.database) as connection:
            raw = json.loads(connection.execute("SELECT spec_json FROM strategies").fetchone()[0])
            raw["model"] = "UNRELATED"
            connection.execute("UPDATE strategies SET spec_json=?", (json.dumps(raw),))
    child = setup.freeze(request)
    before = logical_dump(setup.database)
    with pytest.raises(ValueError):
        setup.register(child, job="b" * 32)
    assert logical_dump(setup.database) == before


@pytest.mark.parametrize("conflict", ["job_strategy", "job_actor", "stored_contract", "stored_spec",
                                     "stored_lineage", "insert_failure"])
def test_conflicts_and_mid_transaction_failure_leave_no_partial_writes(setup, conflict):
    first = setup.freeze()
    setup.register(first)
    definition, job, actor = first, "b" * 32, "independent"
    if conflict == "job_strategy":
        request = deepcopy(setup.request)
        request["portfolio"]["topk"] = 3
        definition, job = setup.freeze(request), "a" * 32
    elif conflict == "job_actor":
        job, actor = "a" * 32, "different-actor"
    else:
        with sqlite3.connect(setup.database) as connection:
            if conflict == "stored_contract":
                connection.execute("UPDATE strategy_research_contracts SET contract_json='{}'")
            elif conflict == "stored_spec":
                raw = json.loads(connection.execute(
                    "SELECT spec_json FROM strategies").fetchone()[0])
                raw["name"] = "tampered"
                connection.execute("UPDATE strategies SET spec_json=?", (json.dumps(raw),))
            elif conflict == "stored_lineage":
                connection.execute("INSERT INTO strategy_lineage VALUES(?,?,?)",
                    (first["strategy_id"], first["strategy_id"], "{}"))
            else:
                connection.execute("CREATE TRIGGER injected_failure BEFORE INSERT ON "
                    "strategy_research_jobs BEGIN SELECT RAISE(ABORT,'independent fault'); END")
                request = deepcopy(setup.request)
                request["parent"] = parent_ref(first)
                request["portfolio"]["topk"] = 3
                definition = setup.freeze(request)
    before = logical_dump(setup.database)
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        setup.register(definition, job=job, actor=actor)
    assert logical_dump(setup.database) == before


def test_factor_registration_never_reopens_or_promotes_changed_lifecycle(setup):
    definition = setup.freeze()
    first = setup.register(definition)
    setup.registry.transition(first.spec.strategy_id, StrategyStatus.CANDIDATE,
        actor="independent", reason="external test transition")
    before = logical_dump(setup.database)
    with pytest.raises(ValueError, match="EXPERIMENT_NONE"):
        setup.register(definition, job="b" * 32)
    assert logical_dump(setup.database) == before


@pytest.mark.parametrize("failure", ["live", "job", "actor", "spec", "future", "before_report"])
def test_registry_entrypoint_and_adapter_reject_authority_identity_clock_errors(setup, monkeypatch,
                                                                              failure):
    definition = setup.freeze()
    now = datetime.now(UTC)
    record = contracts.strategy_spec(definition, now=now)
    job, actor = "a" * 32, "independent"
    if failure == "live":
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    elif failure == "job":
        job = "unsafe"
    elif failure == "actor":
        actor = "operator with spaces"
    elif failure == "spec":
        record = replace(record, model="QLIB_MODEL")
    elif failure == "future":
        with pytest.raises(ValueError, match="CLOCK"):
            contracts.strategy_spec(definition, now=now + timedelta(days=1))
        return
    elif failure == "before_report":
        with pytest.raises(ValueError, match="PRECEDES_EVALUATION"):
            contracts.strategy_spec(definition, now=datetime.fromisoformat(
                setup.report["computed_at"]) - timedelta(seconds=1))
        return
    before = logical_dump(setup.database)
    with pytest.raises(ValueError):
        setup.registry.register_factor_research_experiment(record, contract=definition,
                                                           job_id=job, actor=actor)
    assert logical_dump(setup.database) == before


def test_old_qlib_entrypoint_rejects_factor_schema(setup):
    definition = setup.freeze()
    before = logical_dump(setup.database)
    with pytest.raises((ValueError, KeyError)):
        setup.registry.register_research_experiment(contracts.strategy_spec(
            definition, now=datetime.now(UTC)), contract=definition,
            job_id="a" * 32, actor="independent")
    assert logical_dump(setup.database) == before


def test_actual_ready_data_bytes_are_reverified_before_registration(setup):
    definition = setup.freeze()
    before = logical_dump(setup.database)
    path = setup.root / "stocks/600101/daily.parquet"
    path.write_bytes(path.read_bytes() + b"independent corrupt bytes")
    with pytest.raises(ValueError):
        setup.register(definition)
    assert logical_dump(setup.database) == before


def test_cross_ready_version_report_cannot_be_registered_against_another_archive(setup):
    other_root, _ = setup.data.build(nonce="b")
    report = setup.data.evaluate(other_root)
    request = deepcopy(setup.request)
    request["evaluation_ref"] = setup.evaluations.save(report)
    definition = setup.freeze(request, report)
    before = logical_dump(setup.database)
    with pytest.raises(ValueError, match="LOCAL_READY"):
        setup.register(definition)
    assert logical_dump(setup.database) == before


@pytest.mark.parametrize("store", ["factor", "evaluation", "dependency"])
def test_missing_or_corrupt_actual_reference_storage_cannot_be_self_signed_away(setup, store):
    request = deepcopy(setup.request)
    if store == "dependency":
        request.update(factor_ref=setup.data.request["ordered_factor_refs"][3],
                       signal_kind="FACTOR_COMPOSITE")
    definition = setup.freeze(request)
    if store == "factor":
        with sqlite3.connect(setup.data.registry.database) as connection:
            connection.execute("DELETE FROM factor_definitions")
    elif store == "evaluation":
        with sqlite3.connect(setup.evaluations.database) as connection:
            connection.execute("UPDATE factor_evaluations SET payload='{}'")
    else:
        with sqlite3.connect(setup.data.registry.database) as connection:
            connection.execute("DELETE FROM factor_definitions WHERE factor_id=?",
                               (setup.data.request["ordered_factor_refs"][0]["factor_id"],))
    before = logical_dump(setup.database)
    with pytest.raises(ValueError):
        setup.register(definition)
    assert logical_dump(setup.database) == before


def test_new_factor_entrypoint_does_not_accept_a_valid_qlib_definition(qlib_data, tmp_path):  # noqa: F811
    root, database, _ = qlib_data
    definition = freeze_qlib(bound_default_spec(ready_dataset(root, database)), "e" * 40)
    registry = StrategyRegistry(tmp_path / "registry.sqlite")
    before = logical_dump(tmp_path / "registry.sqlite")
    with pytest.raises(ValueError):
        registry.register_factor_research_experiment(qlib_spec(definition,
            universe=("SH600036",), now=datetime.now(UTC)), contract=definition,
            job_id="a" * 32, actor="independent")
    assert logical_dump(tmp_path / "registry.sqlite") == before


def test_valid_but_insufficient_diagnostics_do_not_become_factor_strategy_admission(setup):
    root, _ = setup.data.build(nonce="constant", transform=lambda _s, _i, row: {
        **row, "close": "100"})
    report = setup.data.evaluate(root)
    assert report["state"] == "INSUFFICIENT_VALID_SAMPLE"
    request = deepcopy(setup.request)
    request["evaluation_ref"] = setup.evaluations.save(report)
    with pytest.raises(ValueError, match="DIAGNOSTIC_EVIDENCE"):
        setup.freeze(request, report)
