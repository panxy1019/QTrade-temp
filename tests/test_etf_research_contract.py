from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research import etf_research_contract as contract
from qtrade.research import etf_trend
from qtrade.research.data_versions import file_hash
from qtrade.research.etf_trend import admit, sessions
from scripts.unified_store import UnifiedBatchStore

FROZEN = datetime(2026, 10, 26, 10, tzinfo=UTC)
OBSERVED = datetime(2026, 10, 27, 10, tzinfo=UTC)
REVISION = "c"*40


def resign(value):
    return signed({k: v for k, v in value.items() if k != "evidence_sha256"})


@pytest.fixture
def environment(monkeypatch):
    directory = Path(__file__).resolve().anchor if os.name == "nt" else None
    with TemporaryDirectory(prefix="etfc-", dir=directory) as temporary:
        root = Path(temporary)
        calendar = Calendar(contract._calendar(None).document)
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                current = datetime(2026, 10, 30, 12, tzinfo=UTC)
                return current.astimezone(tz) if tz else current.replace(tzinfo=None)

        # Synthetic next-session fixtures test behavior only, never production evidence.
        monkeypatch.setattr(etf_trend, "datetime", Clock)
        if os.name == "nt":
            monkeypatch.setattr("scripts.unified_store._fsync_directory", lambda _: None)
            writer = contract.write_json_immutable

            def write(path, value):
                try:
                    writer(path, value)
                except PermissionError:
                    assert path.is_file() and json.loads(path.read_bytes()) == value

            monkeypatch.setattr(contract, "write_json_immutable", write)

        def archive(day=26, *, negative=False, missing=False):
            stamp = datetime(2026, 10, day, 9, tzinfo=UTC)
            window = sessions(calendar, date(2026, 10, day))
            captures = []
            for symbol in ("510300", "159915"):
                rows = []
                for index, session in enumerate(window):
                    close = Decimal("2")+Decimal(index)*Decimal("-.01" if negative else ".01")
                    rows.append({"day": session, "open": str(close),
                                 "high": str(close+Decimal(".1")),
                                 "low": str(close-Decimal(".1")),
                                 "close": str(close), "volume": "1000"})
                if missing:
                    rows.pop(4)
                captures.append({"source_id": "sina_klines", "request": {
                    "symbol": ("sh" if symbol.startswith("5") else "sz")+symbol,
                    "scale": "240", "ma": "no", "datalen": "63"},
                    "received_at": stamp.isoformat(), "body": json.dumps(rows).encode(),
                    "status_code": 200, "transport_error": None, "body_complete": True})
            bindings = {"master_sha256": "a"*64, "as_of": str(stamp.date()),
                        "code_revision": "b"*40,
                        "calendar_sha256": signed(calendar.document)["evidence_sha256"],
                        "contract": "ETF_CLOSE_DIAGNOSTIC_V1"}
            path = root/"sources"/signed(bindings)["evidence_sha256"]
            path.mkdir(parents=True)
            raw = UnifiedBatchStore(path/"capture-store").capture(captures)
            receipt = signed({**AUTHORITY, "bindings": bindings, "raw": raw})
            (path/"receipt.json").write_text(json.dumps(receipt))
            value = admit(captures, ["510300", "159915"], calendar, stamp.date(), stamp)
            value.update(bindings=bindings, receipt_sha256=file_hash(path/"receipt.json"))
            (path/"diagnostic.json").write_text(json.dumps(resign(value)))
            return path

        yield root, calendar, archive


def freeze(environment, **kwargs):
    root, calendar, archive = environment
    source = archive()
    value = contract.freeze_etf_target(source, root/"targets", REVISION, calendar=calendar,
                                       now=FROZEN, **kwargs)
    return source, root/"targets"/value["target_id"]/"target.json", value


def test_frozen_etf_allocation_reuses_positive_momentum_and_records_clocks(environment):
    root, calendar, _ = environment
    source, path, value = freeze(environment, policy=contract.EtfAllocationPolicy(top_k=1))
    assert value["weights"] == [{"instrument": "SZ159915", "weight": "0.950000000000"}]
    assert value["source_observed_at"] == "2026-10-26T09:00:00+00:00"
    assert value["generated_at"] == value["frozen_at"] == FROZEN.isoformat()
    assert value["bindings"]["expected_execution_at"] == "2026-10-27T09:30:00+08:00"
    assert value["bindings"]["source_revision"] == "b"*40
    assert value["bindings"]["implementation_revision"] == REVISION
    assert value["contract"]["evaluation"]["historical_metrics"] is None
    assert value["paper_admission"]["state"] == "BLOCKED"
    assert all(value[key] == [] for key in ("orders", "fills", "holdings"))
    assert all(value[key] is None for key in ("cash", "net_asset_value", "forward_return"))
    before = path.read_bytes()
    assert contract.freeze_etf_target(source, root/"targets", REVISION,
        policy=contract.EtfAllocationPolicy(top_k=1), calendar=calendar,
        now=OBSERVED) == value
    assert path.read_bytes() == before


def test_no_retroactive_target_can_be_created(environment):
    root, calendar, archive = environment
    source = archive()
    with pytest.raises(ValueError, match="PROSPECTIVE_FREEZE"):
        contract.freeze_etf_target(source, root/"targets", REVISION, calendar=calendar,
                                   now=OBSERVED)
    assert not (root/"targets").exists()


def test_target_write_crossing_open_is_never_admitted_on_restart(environment, monkeypatch):
    root, calendar, archive = environment
    source = archive()
    current = [datetime(2026, 10, 27, 1, 29, 59, tzinfo=UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0].astimezone(tz) if tz else current[0].replace(tzinfo=None)

    monkeypatch.setattr(contract, "datetime", Clock)
    writer = contract.write_json_immutable

    def crossing_write(path, value):
        writer(path, value)
        if path.name == "target.json":
            current[0] += timedelta(seconds=2)

    monkeypatch.setattr(contract, "write_json_immutable", crossing_write)
    with pytest.raises(ValueError, match="DURABLE_ADMISSION_LATE"):
        contract.freeze_etf_target(source, root/"targets", REVISION, calendar=calendar)
    path, = (root/"targets").glob("*/target.json")
    before = path.read_bytes()
    assert not (path.parent/"admission.json").exists()
    with pytest.raises(ValueError, match="DURABLE_ADMISSION_MISSING"):
        contract.verify_etf_target(path, source, calendar=calendar)
    with pytest.raises(ValueError, match="DURABLE_ADMISSION_MISSING"):
        contract.freeze_etf_target(source, root/"targets", REVISION, calendar=calendar)
    assert path.read_bytes() == before
    assert not (path.parent/"admission.json").exists()


def test_target_crash_before_admission_never_backfills_receipt(environment, monkeypatch):
    root, calendar, archive = environment
    source = archive()
    writer = contract.write_json_immutable

    def interrupted(path, value):
        if path.name == "admission.json":
            raise OSError("simulated crash before admission")
        writer(path, value)

    monkeypatch.setattr(contract, "write_json_immutable", interrupted)
    with pytest.raises(OSError, match="simulated crash"):
        contract.freeze_etf_target(source, root/"targets", REVISION,
                                   calendar=calendar, now=FROZEN)
    path, = (root/"targets").glob("*/target.json")
    monkeypatch.setattr(contract, "write_json_immutable", writer)
    for stamp in (FROZEN+timedelta(seconds=1), OBSERVED):
        with pytest.raises(ValueError, match="DURABLE_ADMISSION_MISSING"):
            contract.freeze_etf_target(source, root/"targets", REVISION,
                                       calendar=calendar, now=stamp)
        with pytest.raises(ValueError, match="DURABLE_ADMISSION_MISSING"):
            contract.verify_etf_target(path, source, calendar=calendar, now=stamp)
    assert not (path.parent/"admission.json").exists()


@pytest.mark.parametrize("damage", ["target", "hash", "late", "before", "extra", "naive"])
def test_durable_admission_is_exact_target_bound_and_before_open(environment, damage):
    _, calendar, _ = environment
    source, path, _ = freeze(environment)
    marker = path.parent/"admission.json"
    value = json.loads(marker.read_bytes())
    if damage == "target":
        value["target_id"] = "0"*64
    elif damage == "hash":
        value["target_sha256"] = "0"*64
    elif damage == "late":
        value["durable_at"] = "2026-10-27T01:30:00+00:00"
    elif damage == "before":
        value["durable_at"] = (FROZEN-timedelta(seconds=1)).isoformat()
    elif damage == "extra":
        value["private_path"] = "/private"
    else:
        value["durable_at"] = FROZEN.replace(tzinfo=None).isoformat()
    marker.write_text(json.dumps(resign(value)))
    with pytest.raises(ValueError):
        contract.verify_etf_target(path, source, calendar=calendar, now=OBSERVED)


def test_admission_attests_target_durability_not_marker_completion(environment, monkeypatch):
    root, calendar, archive = environment
    source = archive()
    current = [datetime(2026, 10, 27, 1, 29, 58, tzinfo=UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0].astimezone(tz) if tz else current[0].replace(tzinfo=None)

    monkeypatch.setattr(contract, "datetime", Clock)
    writer = contract.write_json_immutable

    def delayed(path, value):
        writer(path, value)
        current[0] += timedelta(seconds=1 if path.name == "target.json" else 2)

    monkeypatch.setattr(contract, "write_json_immutable", delayed)
    value = contract.freeze_etf_target(source, root/"targets", REVISION, calendar=calendar)
    path = root/"targets"/value["target_id"]/"target.json"
    marker = json.loads((path.parent/"admission.json").read_bytes())
    assert marker["durable_at"] == "2026-10-27T01:29:59+00:00"
    assert current[0] == datetime(2026, 10, 27, 1, 30, 1, tzinfo=UTC)
    assert contract.verify_etf_target(path, source, calendar=calendar) == value


def test_source_clock_must_precede_actual_freeze(environment):
    root, calendar, archive = environment
    with pytest.raises(ValueError, match="PROSPECTIVE_FREEZE"):
        contract.freeze_etf_target(archive(), root/"targets", REVISION, calendar=calendar,
                                   now=FROZEN-timedelta(hours=2))


def test_no_positive_trend_is_cash_target_but_missing_input_is_not_cash(environment):
    root, calendar, archive = environment
    source = archive(negative=True)
    value = contract.freeze_etf_target(source, root/"targets", REVISION,
                                       calendar=calendar, now=FROZEN)
    assert value["weights"] == [] and value["qualified_inputs"] == 2


def test_completely_unavailable_input_never_generates_a_fake_cash_target(environment):
    root, calendar, archive = environment
    with pytest.raises(ValueError, match="UNAVAILABLE_NOT_CASH"):
        contract.freeze_etf_target(archive(missing=True), root/"targets", REVISION,
                                   calendar=calendar, now=FROZEN)
    assert not (root/"targets").exists()


def test_actual_next_session_shadow_has_source_bound_closes_and_no_portfolio_return(environment):
    _, calendar, archive = environment
    source, path, target = freeze(environment)
    observed = archive(27)
    value = contract.observe_etf_shadow(path, source, observed, calendar=calendar, now=OBSERVED)
    assert value["state"] == "SHADOW_OBSERVED" and value["session"] == "2026-10-27"
    assert value["bindings"]["target_id"] == target["target_id"]
    assert len(value["observed_closes"]) == 2 and value["missing_target_inputs"] == []
    assert all(value[key] == [] for key in ("orders", "fills", "holdings"))
    assert all(value[key] is None for key in ("cash", "net_asset_value", "forward_return"))
    assert contract.observe_etf_shadow(path, source, observed, calendar=calendar,
                                       now=OBSERVED+timedelta(minutes=1)) == value
    stored = path.parent/"observations"/(value["observation_id"]+".json")
    before = stored.read_bytes()
    assert contract.verify_etf_target(path, source, calendar=calendar, now=OBSERVED) == target
    assert contract.verify_etf_shadow_observation(stored, path, source, observed,
                                                 calendar=calendar, now=OBSERVED) == value
    assert stored.read_bytes() == before


@pytest.mark.parametrize("wrong_day", [26, 28])
def test_shadow_cannot_substitute_same_or_later_session(environment, wrong_day):
    _, calendar, archive = environment
    source, path, _ = freeze(environment)
    observed = source if wrong_day == 26 else archive(wrong_day)
    with pytest.raises(ValueError, match="ACTUAL_NEXT_SESSION"):
        contract.observe_etf_shadow(path, source, observed, calendar=calendar,
                                   now=datetime(2026, 10, 28, 10, tzinfo=UTC))


def test_shadow_waits_for_actual_source_observation_clock(environment):
    _, calendar, archive = environment
    source, path, _ = freeze(environment)
    observed = archive(27)
    with pytest.raises(ValueError, match="ACTUAL_NEXT_SESSION"):
        contract.observe_etf_shadow(path, source, observed, calendar=calendar,
                                   now=OBSERVED-timedelta(hours=2))


def test_shadow_missing_next_day_inputs_are_not_filled_with_old_closes(environment):
    _, calendar, archive = environment
    source, path, _ = freeze(environment)
    value = contract.observe_etf_shadow(path, source, archive(27, missing=True),
                                        calendar=calendar, now=OBSERVED)
    assert value["observed_closes"] == []
    assert value["missing_target_inputs"] == ["SH510300", "SZ159915"]
    assert value["forward_return"] is None


@pytest.mark.parametrize("field,value", [("execution_admitted", True),
    ("execution_admitted", 0), ("mode", "PAPER"), ("forward_return", "0"),
    ("frozen_at", "2026-10-27T09:30:00+08:00")])
def test_resigned_target_cannot_relax_clocks_mode_or_execution_claims(environment, field, value):
    _, calendar, archive = environment
    source, path, target = freeze(environment)
    target[field] = value
    path.write_text(json.dumps(resign(target)))
    with pytest.raises(ValueError):
        contract.observe_etf_shadow(path, source, archive(27), calendar=calendar, now=OBSERVED)


def test_contract_axis_is_derived_and_rule_forgery_is_rejected():
    base = contract.etf_contract(contract.EtfAllocationPolicy())
    changed = contract.etf_contract(contract.EtfAllocationPolicy(top_k=1))
    assert contract.changed_etf_contract_axes(base, changed) == ["portfolio"]
    assert contract.changed_etf_contract_axes(base, deepcopy(base)) == []
    changed["execution"]["rules_admitted"] = True
    with pytest.raises(ValueError, match="BEHAVIOR_INVALID"):
        contract.changed_etf_contract_axes(base, changed)


@pytest.mark.parametrize("damage", ["path", "bool_count", "nan_weight", "float_weight",
    "paper", "fill", "fee", "source_clock", "revision", "identity", "future", "stock"])
def test_public_target_validator_rejects_resigned_overclaims(environment, damage):
    _, calendar, _ = environment
    _, _, value = freeze(environment)
    if damage == "path":
        value["private_path"] = "/root/private"
    elif damage == "bool_count":
        value["qualified_inputs"] = True
    elif damage == "nan_weight":
        value["weights"][0]["weight"] = "NaN"
    elif damage == "float_weight":
        value["weights"][0]["weight"] = .475
    elif damage == "paper":
        value["mode"] = "PAPER"
    elif damage == "fill":
        value["fills"] = [{"price": "2"}]
    elif damage == "fee":
        value["contract"]["execution"]["fee_policy"] = "STOCK_FEE"
    elif damage == "source_clock":
        value["source_observed_at"] = FROZEN.isoformat().replace("10:00", "01:00")
    elif damage == "revision":
        value["bindings"]["implementation_revision"] = "short"
    elif damage == "identity":
        value["bindings"]["contract_id"] = "0"*64
    elif damage == "stock":
        value["weights"][0]["instrument"] = "SH600000"
    else:
        value["frozen_at"] = OBSERVED.isoformat()
    with pytest.raises(ValueError):
        contract.validate_etf_target(resign(value), calendar=calendar, now=OBSERVED)


@pytest.mark.parametrize("damage", ["fill", "holding", "cash", "nav", "return", "nan_close",
    "missing", "target", "authority", "extra", "clock", "duplicate", "stock"])
def test_public_shadow_validator_rejects_resigned_overclaims(environment, damage):
    _, calendar, archive = environment
    source, path, target = freeze(environment)
    value = contract.observe_etf_shadow(path, source, archive(27), calendar=calendar, now=OBSERVED)
    if damage == "fill":
        value["fills"] = [{}]
    elif damage == "holding":
        value["holdings"] = [{}]
    elif damage in {"cash", "nav", "return"}:
        key = {"cash": "cash", "nav": "net_asset_value", "return": "forward_return"}[damage]
        value[key] = "0"
    elif damage == "nan_close":
        value["observed_closes"][0]["close"] = "NaN"
    elif damage == "missing":
        value["missing_target_inputs"] = ["SZ159915"]
    elif damage == "target":
        value["bindings"]["target_id"] = "0"*64
    elif damage == "authority":
        value["live_trading_enabled"] = 0
    elif damage == "extra":
        value["private_path"] = "/root/private"
    elif damage == "clock":
        value["observed_at"] = FROZEN.isoformat()
    elif damage == "duplicate":
        value["observed_closes"].append(value["observed_closes"][0])
    else:
        value["observed_closes"][0]["instrument"] = "SH600000"
    with pytest.raises(ValueError):
        contract.validate_etf_shadow_observation(resign(value), target,
                                                 calendar=calendar, now=OBSERVED)


def test_rehashed_observation_price_cannot_pass_independent_raw_replay(environment):
    _, calendar, archive = environment
    source, path, _ = freeze(environment)
    observed = archive(27)
    value = contract.observe_etf_shadow(path, source, observed, calendar=calendar, now=OBSERVED)
    stored = path.parent/"observations"/(value["observation_id"]+".json")
    value["observed_closes"][0]["close"] = "3"
    stored.write_text(json.dumps(resign(value)))
    with pytest.raises(ValueError, match="RAW_REPLAY_MISMATCH"):
        contract.verify_etf_shadow_observation(stored, path, source, observed,
                                              calendar=calendar, now=OBSERVED)


@pytest.mark.parametrize("policy", [contract.EtfAllocationPolicy(top_k=True),
    contract.EtfAllocationPolicy(top_k=51),
    contract.EtfAllocationPolicy(risk_degree=Decimal("NaN")),
    contract.EtfAllocationPolicy(risk_degree=Decimal("1"))])
def test_invalid_policy_cannot_become_versioned_research_contract(policy):
    with pytest.raises(ValueError):
        contract.etf_contract(policy)


def test_live_environment_never_creates_etf_target(environment, monkeypatch):
    root, calendar, archive = environment
    source = archive()
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(ValueError, match="LIVE_MUST_REMAIN_DISABLED"):
        contract.freeze_etf_target(source, root/"targets", REVISION, calendar=calendar, now=FROZEN)
    assert not (root/"targets").exists()
