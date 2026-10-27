import json
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research import strategy_contract as contracts
from qtrade.research.data_versions import SCHEMA, DataVersionCatalog, content_id, file_hash
from qtrade.research.workflow import build_config, default_spec, validate_spec
from qtrade.strategies.registry import StrategyRegistry, StrategyRole, StrategyStatus


def make_version(archive, *, end="2026-10-26", boundary="2026-10-27", nonce="a"):
    root = archive / ("stage-" + nonce)
    (root / "provider/calendars").mkdir(parents=True)
    (root / "provider/instruments").mkdir()
    calendar = "2016-10-24\n2017-04-05\n" + end + "\n" + boundary + "\n"
    for name in ("day.txt", "day_future.txt"):
        (root / "provider/calendars" / name).write_text(calendar)
    for name in ("all.txt", "workflow_supported.txt"):
        (root / "provider/instruments" / name).write_text(
            "SH600036\t2016-10-24\t" + end + "\n")
    dataset = signed({**AUTHORITY, "end": end, "accepted_symbols": 1,
        "stocks": [{"symbol": "600036", "end": end}],
        "incremental": {"current_symbols": ["600036"]}})
    (root / "dataset.json").write_text(json.dumps(dataset))
    (root / "preparation-request.json").write_text(json.dumps(signed({"nonce": nonce})))
    files = {p.relative_to(root).as_posix(): file_hash(p) for p in root.rglob("*") if p.is_file()}
    reload = signed({**AUTHORITY, "schema_version": "qtrade.qlib-provider-reload.v1",
        "dataset_sha256": files["dataset.json"], "checked_current_symbols": 1,
        "provider_sha256": content_id({k: v for k, v in files.items()
                                      if k.startswith("provider/")}, {}),
        "actual_end": end, "checks_passed": True, "placeholder_all_missing": True,
        "unknown_current_prices_missing": True})
    (root / "provider-reload.json").write_text(json.dumps(reload))
    files["provider-reload.json"] = file_hash(root / "provider-reload.json")
    binding = {"nonce": nonce, "source_snapshot_sha256": "f" * 64}
    identity = content_id(files, binding)
    manifest = signed({**AUTHORITY, "schema_version": SCHEMA, "version_id": identity,
        "state": "RESEARCH_READY_QUALIFIED_SUBSET", "frequency": "day",
        "historical_pit_certified": False, "external_execution_ready": False,
        "created_at": datetime.now(UTC).isoformat(), "actual_end": end,
        "current_symbols": ["600036"], "historical_symbols": ["600036"],
        "blocked_symbols": [], "calendar_boundaries": [boundary],
        "files": files, "bindings": binding})
    (root / "version.json").write_text(json.dumps(manifest))
    target = archive / "versions" / identity
    target.parent.mkdir(exist_ok=True)
    root.rename(target)
    return target


@pytest.fixture
def admitted(tmp_path):
    archive = tmp_path / "archive"
    root = make_version(archive)
    database = tmp_path / "versions.sqlite"
    catalog = DataVersionCatalog(database, archive)
    catalog.admit(root.name, now=datetime.now(UTC))
    return root, database, catalog


def bound_spec(binding):
    spec = default_spec("linear")
    spec.update(dataset_id=binding["dataset_id"], data_binding=binding,
                source_snapshot_sha256=binding["source_snapshot_sha256"],
                feature_set_version=contracts.FEATURE_SET_VERSION)
    spec["segments"]["test"][1] = "2026-10-26"
    return spec


def reseal(value):
    return signed({k: v for k, v in value.items() if k != "evidence_sha256"})


def test_ready_dataset_is_ro_fully_verified_and_does_not_follow_current_head(admitted):
    root, database, catalog = admitted
    binding = contracts.ready_dataset(root, database)
    newer = make_version(root.parent.parent, end="2026-10-27", boundary="2026-10-28", nonce="b")
    catalog.admit(newer.name, now=datetime.now(UTC))
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert contracts.ready_dataset(root, database) == binding
    assert {p: p.read_bytes() for p in before} == before
    assert binding["data_version_id"] == root.name
    assert binding["actual_end"] == "2026-10-26"
    assert binding["supported_symbols"] == 1
    assert contracts.supported_universe(root) == ("SH600036",)
    assert binding["external_order_transmission"] is False
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM data_events").fetchone()[0] == 2


def test_unadmitted_or_tampered_provider_cannot_be_selected(admitted):
    root, database, _ = admitted
    other = make_version(root.parent.parent, nonce="b")
    with pytest.raises(ValueError, match="UNIQUE_READY"):
        contracts.ready_dataset(other, database)
    (root / "provider/instruments/workflow_supported.txt").write_text("changed")
    with pytest.raises(ValueError, match="FILE_IDENTITY"):
        contracts.ready_dataset(root, database)


def test_ambiguous_or_changed_ready_receipt_is_rejected(admitted):
    root, database, _ = admitted
    with sqlite3.connect(database) as connection:
        event = json.loads(connection.execute("SELECT payload FROM data_events").fetchone()[0])
        event["admitted_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        event = reseal(event)
        connection.execute("INSERT INTO data_events(event_id,version_id,kind,payload) "
            "VALUES(?,?,?,?)", (event["evidence_sha256"], root.name, "QLIB_DATA_READY",
                                   json.dumps(event)))
    with pytest.raises(ValueError, match="UNIQUE_READY"):
        contracts.ready_dataset(root, database)


def test_ready_admission_drift_after_expensive_verify_is_not_accepted(admitted, monkeypatch):
    root, database, _ = admitted
    original = contracts.verify_version

    def verify(path):
        value = original(path)
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE data_versions SET admitted_at='changed'")
        return value

    monkeypatch.setattr(contracts, "verify_version", verify)
    with pytest.raises(ValueError, match="ADMISSION_CHANGED"):
        contracts.ready_dataset(root, database)


def test_legacy_specs_and_configs_keep_their_original_bytes():
    spec = default_spec()
    raw = json.dumps(spec, sort_keys=True)
    assert json.dumps(validate_spec(spec), sort_keys=True) == raw
    baseline = json.loads(Path("configs/qlib_daily_baseline.json").read_text())
    config = build_config(spec, baseline)
    assert "data_binding" not in config and "feature_set_version" not in config
    assert config["workflow_spec"] == spec
    with pytest.raises(ValueError, match="UNSUPPORTED_DATASET"):
        validate_spec({**spec, "dataset_id": "qlib-version-" + "a" * 64})


@pytest.mark.parametrize("change", ["future_split", "source", "features", "version",
    "model", "hash", "authority", "unknown", "missing_version", "missing_binding"])
def test_version_bound_spec_rejects_unavailable_or_forged_inputs(admitted, change):
    root, database, _ = admitted
    spec = bound_spec(contracts.ready_dataset(root, database))
    if change == "future_split":
        spec["segments"]["test"][1] = "2026-10-27"
    elif change == "source":
        spec["source_snapshot_sha256"] = "e" * 64
    elif change == "features":
        spec["features"] = "Alpha360"
    elif change == "version":
        spec["feature_set_version"] = "latest"
    elif change == "model":
        spec["model"] = "xgboost"
    elif change == "hash":
        spec["data_binding"]["data_version_id"] = "a" * 64
    elif change == "authority":
        spec["data_binding"]["live_trading_enabled"] = 0
        spec["data_binding"] = reseal(spec["data_binding"])
    elif change == "unknown":
        spec["data_binding"]["provider_path"] = "../../private"
        spec["data_binding"] = reseal(spec["data_binding"])
    elif change == "missing_version":
        del spec["feature_set_version"]
    else:
        del spec["data_binding"]
    with pytest.raises(ValueError):
        validate_spec(spec)


def test_frozen_strategy_identity_binds_data_feature_model_portfolio_execution_and_code(admitted):
    root, database, _ = admitted
    spec = bound_spec(contracts.ready_dataset(root, database))
    original = deepcopy(spec)
    definition = contracts.freeze_strategy(spec, "a" * 40)
    assert spec == original
    assert contracts.validate_strategy(definition) == definition
    assert definition["execution"]["limit_threshold"] == ["$buy_block", "$sell_block"]
    assert definition["signal"]["model_artifact_id"] is None
    config = definition["training_config"]
    assert config["model"]["kwargs"] == {"estimator": "ridge", "alpha": 1.0}
    assert config["fit"] == {}
    assert config["infer_processors"][-1] == {"class": "Float64Features",
        "module_path": "qtrade.research.qlib_processors"}
    assert config["online_feature_precision"] == "FLOAT64_AFTER_FROZEN_NORMALIZATION_V1"
    assert definition["restore_contract"] == "FITTED_DATASETH_RMDLOADER_V1"
    for key, value in (("topk", 40), ("buy_cost_bps", 10), ("model", "lightgbm")):
        changed = deepcopy(spec)
        changed[key] = value
        assert contracts.freeze_strategy(changed, "a" * 40)["strategy_id"] != definition[
            "strategy_id"]
    assert contracts.freeze_strategy(spec, "b" * 40)["strategy_id"] != definition["strategy_id"]
    changed = deepcopy(definition)
    changed["execution"]["trade_unit"] = 1
    with pytest.raises(ValueError, match="STRATEGY_DEFINITION_CHANGED"):
        contracts.validate_strategy(reseal(changed))


def test_catalog_choices_are_actual_definitions_and_locally_rechecked(admitted):
    root, database, _ = admitted
    binding = contracts.ready_dataset(root, database)
    choices = contracts.strategy_choices(binding, "a" * 40)
    assert [row["spec"]["model"] for row in choices] == ["linear", "lightgbm"]
    assert len({row["strategy_version"] for row in choices}) == 2
    for row in choices:
        assert row["spec"]["segments"]["test"][1] == binding["actual_end"]
        assert contracts.verify_bound_spec(row["spec"], root, database) == row["spec"]
    forged = deepcopy(choices[0]["spec"])
    forged["data_binding"]["ready_event_id"] = "a" * 64
    forged["data_binding"] = reseal(forged["data_binding"])
    # A content hash is not admission authority: the worker must make this check.
    assert validate_spec(forged) == forged
    with pytest.raises(ValueError, match="LOCAL_READY_DATASET_MISMATCH"):
        contracts.verify_bound_spec(forged, root, database)


def test_registry_atomically_links_jobs_without_promoting_or_rebinding(admitted, tmp_path):
    root, database, _ = admitted
    spec = bound_spec(contracts.ready_dataset(root, database))
    definition = contracts.freeze_strategy(spec, "a" * 40)
    registry = StrategyRegistry(tmp_path / "strategies.sqlite")
    now = datetime.now(UTC)
    record = contracts.register_strategy(registry, definition, job_id="1" * 32,
        actor="research", universe=contracts.supported_universe(root), now=now)
    assert record.status is StrategyStatus.EXPERIMENT and record.role is StrategyRole.NONE
    assert registry.research_contract(record.spec.strategy_id) == definition
    assert len(registry.transitions(record.spec.strategy_id)) == 1
    again = contracts.register_strategy(registry, definition, job_id="1" * 32,
        actor="research", universe=("SH600036",), now=now + timedelta(seconds=1))
    assert again == record
    assert len(registry.research_jobs(record.spec.strategy_id)) == 1
    registry.transition(record.spec.strategy_id, StrategyStatus.CANDIDATE,
                        actor="research", reason="separate research review")
    later = contracts.register_strategy(registry, definition, job_id="2" * 32,
        actor="research", universe=("SH600036",), now=now + timedelta(seconds=2))
    assert later.status is StrategyStatus.CANDIDATE
    assert len(registry.research_jobs(record.spec.strategy_id)) == 2
    assert len(registry.transitions(record.spec.strategy_id)) == 2


def test_registration_job_conflict_rolls_back_new_strategy_and_contract(admitted, tmp_path):
    root, database, _ = admitted
    spec = bound_spec(contracts.ready_dataset(root, database))
    registry = StrategyRegistry(tmp_path / "strategies.sqlite")
    first = contracts.freeze_strategy(spec, "a" * 40)
    now = datetime.now(UTC)
    contracts.register_strategy(registry, first, job_id="1" * 32, actor="research",
                                 universe=("SH600036",), now=now)
    second = contracts.freeze_strategy({**spec, "topk": 40}, "a" * 40)
    with pytest.raises(ValueError, match="JOB_STRATEGY_CONFLICT"):
        contracts.register_strategy(registry, second, job_id="1" * 32, actor="research",
                                     universe=("SH600036",), now=now)
    assert len(registry.list()) == 1
    assert registry.research_contract(second["strategy_id"]) is None
    assert registry.research_jobs(second["strategy_id"]) == ()


def test_failed_job_link_rolls_back_strategy_contract_and_lifecycle(admitted, tmp_path):
    root, database, _ = admitted
    path = tmp_path / "strategies.sqlite"
    registry = StrategyRegistry(path)
    definition = contracts.freeze_strategy(bound_spec(contracts.ready_dataset(root, database)),
                                           "a" * 40)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TRIGGER reject_job BEFORE INSERT ON strategy_research_jobs "
                           "BEGIN SELECT RAISE(ABORT,'controlled job-link failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="controlled job-link failure"):
        contracts.register_strategy(registry, definition, job_id="1" * 32, actor="research",
                                     universe=("SH600036",), now=datetime.now(UTC))
    assert registry.list() == ()
    assert registry.research_contract(definition["strategy_id"]) is None
    assert registry.transitions(definition["strategy_id"]) == ()


def test_closed_strategy_replays_original_job_but_cannot_accept_new_job(admitted, tmp_path):
    root, database, _ = admitted
    registry = StrategyRegistry(tmp_path / "strategies.sqlite")
    definition = contracts.freeze_strategy(bound_spec(contracts.ready_dataset(root, database)),
                                           "a" * 40)
    arguments = dict(job_id="1" * 32, actor="research", universe=("SH600036",),
                     now=datetime.now(UTC))
    contracts.register_strategy(registry, definition, **arguments)
    registry.transition(definition["strategy_id"], StrategyStatus.REJECTED,
                        actor="review", reason="failed independent review")
    replay = contracts.register_strategy(registry, definition, **arguments)
    assert replay.status is StrategyStatus.REJECTED
    arguments["job_id"] = "2" * 32
    with pytest.raises(ValueError, match="LIFECYCLE_CLOSED"):
        contracts.register_strategy(registry, definition, **arguments)
    assert len(registry.research_jobs(definition["strategy_id"])) == 1


def test_unknown_universe_or_live_environment_never_registers(admitted, tmp_path, monkeypatch):
    root, database, _ = admitted
    definition = contracts.freeze_strategy(bound_spec(contracts.ready_dataset(root, database)),
                                           "a" * 40)
    registry = StrategyRegistry(tmp_path / "strategies.sqlite")
    with pytest.raises(ValueError, match="UNIVERSE_MISMATCH"):
        contracts.register_strategy(registry, definition, job_id="1" * 32, actor="research",
                                     universe=("SH600000",), now=datetime.now(UTC))
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(ValueError, match="LIVE_DISABLED"):
        contracts.register_strategy(registry, definition, job_id="1" * 32, actor="research",
                                     universe=("SH600036",), now=datetime.now(UTC))
    assert registry.list() == ()
