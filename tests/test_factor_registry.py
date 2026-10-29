from __future__ import annotations

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, Inexact, Rounded, localcontext
from hashlib import sha256
from pathlib import Path

import pytest

from qtrade.research.factor_registry import (
    FactorRegistry,
    builtin_definitions,
    evaluate_builtin,
    factor_ref,
    validate_definition,
)
from qtrade.research.signal_baselines import signal_family
from qtrade.research.workflow import canonical, digest

NOW = datetime(2026, 10, 28, 4, tzinfo=UTC)
REVISION = "a" * 40  # Deliberate synthetic revision label, not a certified Git checkout.
BASIS = "SYNTHETIC_UNADJUSTED"


@pytest.fixture(autouse=True)
def research_only(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")


def definitions():
    return builtin_definitions(source_revision=REVISION, price_basis=BASIS)


def count(registry):
    with registry.connect() as connection:
        return connection.execute("SELECT COUNT(*) FROM factor_definitions").fetchone()[0]


def rehash(value):
    body = {key: item for key, item in value.items()
            if key not in {"factor_version", "definition_sha256"}}
    value["factor_version"] = digest(body)
    value["definition_sha256"] = digest({**body, "factor_version": value["factor_version"]})
    return value


def test_register_four_versions_is_restart_safe_idempotent_and_binds_exact_sources(tmp_path):
    registry = FactorRegistry(tmp_path / "isolated-factors.sqlite")
    refs = registry.register_builtins(source_revision=REVISION, price_basis=BASIS, now=NOW)
    assert len(refs) == count(registry) == 4
    restored = FactorRegistry(registry.database)
    assert restored.register_builtins(source_revision=REVISION, price_basis=BASIS,
                                     now=NOW + timedelta(hours=1)) == refs
    for definition, reference in zip(definitions(), refs, strict=True):
        loaded = restored.get(reference)
        assert loaded == definition
        assert loaded["live_trading_enabled"] is loaded["external_order_transmission"] is False
        for name, identity in loaded["source"]["files"].items():
            assert sha256(Path(name).read_bytes()).hexdigest() == identity
    with restored.connect() as connection:
        assert {row[0] for row in connection.execute(
            "SELECT registered_at FROM factor_definitions")} == {NOW.isoformat()}
    assert refs[-1] == factor_ref(definitions()[-1])
    assert restored.get(refs[-1])["dependencies"] == list(refs[:3])


def test_registered_values_reuse_existing_factors_and_equal_rank_composition(tmp_path):
    registry = FactorRegistry(tmp_path / "factors.sqlite")
    refs = registry.register_builtins(source_revision=REVISION, price_basis=BASIS, now=NOW)
    sessions = [(NOW.date() - timedelta(days=20 - index)).isoformat() for index in range(21)]
    histories = {symbol: tuple(Decimal(30) + step * index for index in range(21))
                 for symbol, step in {"SH600001": Decimal(".1"), "SH600002": Decimal("-.1"),
                                      "SH600003": Decimal(0)}.items()}
    original = deepcopy(histories)
    family = signal_family({symbol: [{"date": day, "close": str(close)}
                            for day, close in zip(sessions, closes, strict=True)]
                            for symbol, closes in histories.items()}, sessions,
                           as_of=sessions[-1], security_type="STOCK")
    for reference in refs:
        definition = registry.get(reference)
        scores = evaluate_builtin(definition, histories)
        prior_scores = {row["instrument"]: Decimal(str(row["score"]))
                        for row in family["signals"][definition["alias"]]}
        assert set(scores) == set(prior_scores)
        assert all(abs(score - prior_scores[symbol]) < Decimal("1e-14")
                   for symbol, score in scores.items())
    assert histories == original


def test_composite_missing_dependency_fails_without_partial_registration(tmp_path):
    registry = FactorRegistry(tmp_path / "factors.sqlite")
    with pytest.raises(ValueError, match="DEPENDENCY_NOT_REGISTERED"):
        registry.register(definitions()[-1], now=NOW)
    assert count(registry) == 0
    refs = registry.register_builtins(source_revision=REVISION, price_basis=BASIS, now=NOW)
    with registry.connect() as connection:
        connection.execute("DELETE FROM factor_definitions WHERE factor_id=?",
                           (refs[0]["factor_id"],))
    with pytest.raises(ValueError, match="DEPENDENCY_NOT_REGISTERED"):
        registry.get(refs[-1])
    with pytest.raises(ValueError, match="DEPENDENCY_NOT_REGISTERED"):
        registry.register(definitions()[-1], now=NOW)


@pytest.mark.parametrize("damage", ["formula", "params", "clock", "frequency", "basis",
                                     "code", "authority_type", "dependency", "source_path"])
def test_rehashing_cannot_admit_untrusted_code_or_changed_builtin_contract(tmp_path, damage):
    definition = deepcopy(definitions()[-1] if damage == "dependency" else definitions()[0])
    if damage == "formula":
        definition["formula"] = "Ref($close,-1)/$close-1"
    elif damage == "params":
        definition["parameters"]["lookback"] = 1
    elif damage == "clock":
        definition["time_policy"]["observed_at"] = "IGNORE_OBSERVATION_CLOCK"
    elif damage == "frequency":
        definition["frequency"] = "5min"
    elif damage == "basis":
        definition["price_basis"] = "UNKNOWN"
    elif damage == "code":
        definition["module_path"] = "untrusted.__import__"
    elif damage == "authority_type":
        definition["live_trading_enabled"] = 0
    elif damage == "dependency":
        definition["dependencies"][0] = factor_ref(definition)
    else:
        definition["source"]["files"]["../../secret"] = "0" * 64
    registry = FactorRegistry(tmp_path / "factors.sqlite")
    with pytest.raises(ValueError, match="FACTOR_"):
        registry.register(rehash(definition), now=NOW)
    assert count(registry) == 0


def test_same_version_changed_payload_is_rejected_and_never_repaired_in_place(tmp_path):
    registry = FactorRegistry(tmp_path / "factors.sqlite")
    definition = definitions()[0]
    reference = registry.register(definition, now=NOW)
    changed = deepcopy(definition)
    changed["formula"] = "close[t+1]"
    with pytest.raises(ValueError, match="IDENTITY_INVALID"):
        registry.register(changed, now=NOW)
    assert registry.get(reference) == definition
    with registry.connect() as connection:
        connection.execute("UPDATE factor_definitions SET definition_json='{}'")
    with pytest.raises(ValueError, match="IMMUTABLE_VERSION_CONFLICT"):
        registry.register(definition, now=NOW)
    with pytest.raises(ValueError, match="DEFINITION_INVALID"):
        registry.get(reference)
    with registry.connect() as connection:
        row = connection.execute("SELECT definition_json FROM factor_definitions").fetchone()
        assert row[0] == '{}'


def test_valid_dependency_body_cannot_be_swapped_under_another_registered_reference(tmp_path):
    registry = FactorRegistry(tmp_path / "factors.sqlite")
    refs = registry.register_builtins(source_revision=REVISION, price_basis=BASIS, now=NOW)
    with registry.connect() as connection:
        connection.execute("UPDATE factor_definitions SET definition_json=? WHERE factor_id=?",
                           (canonical(definitions()[1]).decode(), refs[0]["factor_id"]))
    with pytest.raises(ValueError, match="DEPENDENCY_IDENTITY_MISMATCH"):
        registry.get(refs[-1])
    with pytest.raises(ValueError, match="DEPENDENCY_IDENTITY_MISMATCH"):
        registry.register(definitions()[-1], now=NOW)


def test_trigger_failure_rolls_back_entire_builtin_batch_then_retry_commits(tmp_path):
    registry = FactorRegistry(tmp_path / "factors.sqlite")
    with registry.connect() as connection:
        connection.execute("""CREATE TRIGGER controlled_failure BEFORE INSERT ON factor_definitions
            WHEN NEW.factor_id='qtrade.price.lowvol20'
            BEGIN SELECT RAISE(ABORT, 'synthetic interruption'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic interruption"):
        registry.register_builtins(source_revision=REVISION, price_basis=BASIS, now=NOW)
    assert count(registry) == 0
    with registry.connect() as connection:
        connection.execute("DROP TRIGGER controlled_failure")
    registry.register_builtins(source_revision=REVISION, price_basis=BASIS, now=NOW)
    assert count(registry) == 4


def test_competing_builtin_registrations_commit_one_immutable_copy(tmp_path):
    registry = FactorRegistry(tmp_path / "factors.sqlite")
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: registry.register_builtins(
            source_revision=REVISION, price_basis=BASIS, now=NOW), range(12)))
    assert all(result == results[0] for result in results)
    assert count(FactorRegistry(registry.database)) == 4


def test_new_basis_creates_new_versions_without_changing_original_records(tmp_path):
    registry = FactorRegistry(tmp_path / "factors.sqlite")
    original = registry.register_builtins(source_revision=REVISION, price_basis=BASIS, now=NOW)
    before = [canonical(registry.get(reference)) for reference in original]
    newer = registry.register_builtins(source_revision=REVISION,
                                      price_basis="SINA_NATIVE_ADJUSTMENT_UNCERTIFIED", now=NOW)
    assert count(registry) == 8 and all(left != right for left, right in zip(
        original, newer, strict=True))
    assert [canonical(registry.get(reference)) for reference in original] == before


def test_source_drift_blocks_execution_but_archived_version_remains_readable(tmp_path):
    registry = FactorRegistry(tmp_path / "factors.sqlite")
    old = definitions()[0]
    reference = registry.register(old, now=NOW)
    # A new trusted snapshot is describable, but cannot execute as this runtime's bytes.
    root = tmp_path / "other-source-checkout"
    for name in old["source"]["files"]:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(name).read_bytes() + b"\n# synthetic later source snapshot\n")
    changed = builtin_definitions(source_revision="b" * 40, price_basis=BASIS, source_root=root)[0]
    assert changed["factor_version"] != old["factor_version"]
    validate_definition(changed, verify_runtime_source=False)
    with pytest.raises(ValueError, match="RUNTIME_SOURCE_MISMATCH"):
        evaluate_builtin(changed, {"A": (Decimal(10),) * 21})
    assert registry.get(reference) == old


@pytest.mark.parametrize("damage", ["missing", "nan", "zero", "wrong_type"])
def test_scoring_cannot_fill_missing_or_invalid_inputs(damage):
    closes = [Decimal(10)] * 21
    if damage == "missing":
        closes.pop()
    else:
        closes[-1] = {"nan": Decimal("NaN"), "zero": Decimal(0), "wrong_type": 10}[damage]
    with pytest.raises(ValueError, match="FACTOR_"):
        evaluate_builtin(definitions()[0], {"A": tuple(closes)})


def test_references_and_registration_clock_are_strict_and_do_not_resolve_latest(tmp_path):
    registry = FactorRegistry(tmp_path / "factors.sqlite")
    reference = registry.register(definitions()[0], now=NOW)
    for changed in ({**reference, "latest": True}, {**reference, "factor_id": "../outside"},
                    {**reference, "factor_version": "latest"},
                    {**reference, "definition_sha256": "0" * 64}):
        with pytest.raises(ValueError, match="FACTOR_"):
            registry.get(changed)
    with pytest.raises(ValueError, match="TIMEZONE"):
        registry.register(definitions()[0], now=NOW.replace(tzinfo=None))
    assert count(registry) == 1


def test_live_flag_change_blocks_registration_and_computation(tmp_path, monkeypatch):
    registry = FactorRegistry(tmp_path / "factors.sqlite")
    definition = definitions()[0]
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(RuntimeError, match="LIVE_TRADING_MUST_REMAIN_DISABLED"):
        registry.register(definition, now=NOW)
    with pytest.raises(RuntimeError, match="LIVE_TRADING_MUST_REMAIN_DISABLED"):
        FactorRegistry(tmp_path / "blocked.sqlite")
    with pytest.raises(RuntimeError, match="LIVE_TRADING_MUST_REMAIN_DISABLED"):
        evaluate_builtin(definition, {"A": (Decimal(10),) * 21})
    assert count(registry) == 0 and not (tmp_path / "blocked.sqlite").exists()


def test_loaded_definitions_are_detached_from_persisted_identity(tmp_path):
    registry = FactorRegistry(tmp_path / "factors.sqlite")
    definition = definitions()[0]
    reference = registry.register(definition, now=NOW)
    definition["time_policy"]["observed_at"] = "mutated input"
    loaded = registry.get(reference)
    loaded["source"]["files"].clear()
    assert registry.get(reference) == definitions()[0]


def test_read_only_registry_never_initializes_or_changes_definition_bytes(tmp_path):
    missing = tmp_path / "missing" / "factors.sqlite"
    with pytest.raises(ValueError, match="READ_ONLY_DATABASE_MISSING"):
        FactorRegistry(missing, read_only=True)
    assert not missing.parent.exists()
    writer = FactorRegistry(tmp_path / "factors.sqlite")
    reference = writer.register(definitions()[0], now=NOW)
    before = writer.database.read_bytes()
    reader = FactorRegistry(writer.database, read_only=True)
    assert reader.get(reference) == definitions()[0]
    with pytest.raises(ValueError, match="REGISTRY_READ_ONLY"):
        reader.register(definitions()[0], now=NOW)
    with reader.connect() as connection, pytest.raises(sqlite3.OperationalError, match="readonly"):
        connection.execute("DELETE FROM factor_definitions")
    assert writer.database.read_bytes() == before


def test_numeric_context_is_frozen_and_cannot_change_or_leak_callers_context():
    values = definitions()
    histories = {symbol: tuple(Decimal(30) + Decimal(step) * index for index in range(21))
                 for symbol, step in {"A": ".13", "B": "-.09", "C": ".07", "D": ".02"}.items()}
    expected = [evaluate_builtin(value, histories) for value in values]
    assert all(value["numeric_policy"]["decimal_precision"] == 28 for value in values)
    with localcontext() as caller:
        caller.prec, caller.rounding, caller.Emin, caller.Emax = 6, ROUND_DOWN, -8, 8
        caller.traps[Inexact] = caller.traps[Rounded] = True
        caller.flags[Inexact] = True
        before = (caller.prec, caller.rounding, caller.Emin, caller.Emax,
                  dict(caller.traps), dict(caller.flags))
        assert [evaluate_builtin(value, histories) for value in values] == expected
        after = (caller.prec, caller.rounding, caller.Emin, caller.Emax,
                 dict(caller.traps), dict(caller.flags))
        assert after == before


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_sqlite_hardlinks_and_sidecar_aliases_are_rejected_before_open(tmp_path, suffix):
    protected = tmp_path / "protected.sqlite"
    with sqlite3.connect(protected) as connection:
        connection.execute("CREATE TABLE retained(value TEXT)")
    original = protected.read_bytes()
    target = tmp_path / "isolated.sqlite"
    if suffix:
        FactorRegistry(target)
    os.link(protected, Path(str(target) + suffix))
    with pytest.raises(ValueError, match="DATABASE_OR_SIDECAR_LINK_FORBIDDEN"):
        FactorRegistry(target)
    with pytest.raises(ValueError, match="DATABASE_OR_SIDECAR_LINK_FORBIDDEN"):
        FactorRegistry(target, read_only=True)
    assert protected.read_bytes() == original


def test_link_added_after_construction_cannot_bypass_connection_guard(tmp_path):
    registry = FactorRegistry(tmp_path / "factors.sqlite")
    before = registry.database.read_bytes()
    os.link(registry.database, tmp_path / "unexpected-alias.sqlite")
    with pytest.raises(ValueError, match="DATABASE_OR_SIDECAR_LINK_FORBIDDEN"):
        registry.register(definitions()[0], now=NOW)
    assert registry.database.read_bytes() == before
