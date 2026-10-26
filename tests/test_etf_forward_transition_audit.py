"""Synthetic clock fixtures exercise acceptance logic, never real market acceptance."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_etf_shadow_service import OBSERVED, REVISION, resign, runtime, write  # noqa: F401

from qtrade.monitoring.data_freshness import AUTHORITY
from qtrade.research import etf_research_contract as core
from scripts import audit_etf_forward_transition as audit
from scripts import run_etf_shadow_service as service

DAY = date(2026, 10, 27)


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def transition(runtime):  # noqa: F811
    source = runtime.archive()
    first = runtime.run()
    target = first["entries"][0]["target"]
    runtime.clock[0] = OBSERVED
    observed_source = runtime.archive(27)
    published = runtime.run()
    public_path = runtime.base/"workflow/public/etf-forward.json"
    local = runtime.local
    job = local/"contracts"/target["bindings"]["contract_id"]/"sessions"/str(DAY)
    target_dir = local/"targets"/target["target_id"]
    observation_path, = (target_dir/"observations").glob("*.json")

    def run(now=OBSERVED+timedelta(seconds=2), *, day=DAY, target_id=None):
        return audit.audit_transition(runtime.archives, local, public_path, day,
            target_id or target["target_id"], runtime.calendar, REVISION, now=now)

    return SimpleNamespace(runtime=runtime, source=source, observed_source=observed_source,
        target=target, public=published, public_path=public_path, local=local, job=job,
        target_dir=target_dir, observation_path=observation_path, run=run)


def test_raw_replay_and_exact_public_binding_pass_without_writes(transition, monkeypatch):
    original = snapshot(transition.local.parent)

    def forbidden(*args, **kwargs):
        raise AssertionError("AUDITOR_MUST_NOT_WRITE_OR_START_RUNTIME")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    for module, names in ((service, ("cycle", "record", "freeze_etf_target", "observe_etf_shadow")),
                           (core, ("freeze_etf_target", "observe_etf_shadow")),
                           (audit, ("write_json_immutable",))):
        for name in names:
            monkeypatch.setattr(module, name, forbidden)
    result = transition.run()
    assert result["state"] == "PASSED" and result["etf_shadow_transition_accepted"] is True
    assert result["clock_basis"] == "CONTROLLED_TEST_CLOCK"
    assert result["whole_step1_accepted"] is False and result["browser_verified"] is False
    assert result["paper_admission"] == "BLOCKED" and result["execution_admitted"] is False
    assert result["observed_close_count"] == 2 and result["missing_selected_close_count"] == 0
    assert result["bindings"]["target_id"] == transition.target["target_id"]
    assert set(result["bindings"]["local_file_sha256"]) == {
        "intent", "target_binding", "observation_source", "completion", "target", "admission",
        "observation"}
    assert snapshot(transition.local.parent) == original
    assert all(result[key] == [] for key in ("orders", "fills", "holdings"))
    assert all(result[key] is None for key in ("cash", "net_asset_value", "forward_return"))


@pytest.mark.parametrize("stamp", ["2026-10-27T06:59:59+00:00", "2026-10-27T07:01:00+00:00",
                                   "2026-10-27T08:29:59+00:00"])
def test_real_daily_completion_gate_never_accepts_intraday_or_before_cutoff(transition, stamp):
    result = transition.run(datetime.fromisoformat(stamp))
    assert result["state"] == "BLOCKED"
    assert result["failures"] == ["ETF_TRANSITION_AUDIT_REAL_SESSION_NOT_COMPLETE"]


def test_future_or_wrong_day_cannot_substitute_for_exact_target_day(transition):
    assert transition.run(day=date(2026, 10, 28))["etf_shadow_transition_accepted"] is False
    result = transition.run(day=date(2026, 10, 26))
    assert result["state"] == "BLOCKED"
    assert result["failures"] == ["ETF_TRANSITION_AUDIT_TARGET_DAY_OR_IDENTITY_CHANGED"]


@pytest.mark.parametrize("damage", ["missing", "stale", "stopped", "degraded", "future",
    "missing_entry", "missing_observation", "invented_close", "paper", "fills", "nav"])
def test_public_missing_stale_future_or_fabricated_evidence_blocks(transition, damage):
    value = json.loads(transition.public_path.read_bytes())
    if damage == "missing":
        transition.public_path.unlink()
    elif damage == "stale":
        value["heartbeat_at"] = value["generated_at"] = (
            OBSERVED-timedelta(seconds=721)).isoformat()
        # A public document cannot predate its observations either; both failures block.
    elif damage == "stopped":
        value["state"] = "STOPPED"
    elif damage == "degraded":
        value["state"], value["errors"] = "DEGRADED", ["ETF_SHADOW_TEST_ERROR"]
    elif damage == "future":
        value["heartbeat_at"] = value["generated_at"] = (OBSERVED+timedelta(days=1)).isoformat()
    elif damage == "missing_entry":
        value["entries"] = [row for row in value["entries"]
                            if row["target"]["target_id"] != transition.target["target_id"]]
    elif damage == "missing_observation":
        for row in value["entries"]:
            if row["target"]["target_id"] == transition.target["target_id"]:
                row["observation"] = None
    elif damage == "invented_close":
        for row in value["entries"]:
            if row["observation"]:
                row["observation"]["observed_closes"][0]["close"] = "999"
                row["observation"] = resign(row["observation"])
    elif damage == "paper":
        value["mode"] = "PAPER"
    elif damage == "fills":
        value["fills"] = [{"quantity": 100}]
    else:
        value["net_asset_value"] = "1000000"
    if damage != "missing":
        write(transition.public_path, resign(value))
    result = transition.run()
    assert result["state"] == "BLOCKED" and result["etf_shadow_transition_accepted"] is False
    assert result["whole_step1_accepted"] is False


@pytest.mark.parametrize("name", ["target", "admission", "intent", "target-binding",
                                  "observation-source", "completed", "observation"])
def test_missing_immutable_local_receipts_never_recreated(transition, name):
    paths = {"target": transition.target_dir/"target.json",
        "admission": transition.target_dir/"admission.json", "intent": transition.job/"intent.json",
        "target-binding": transition.job/"target-binding.json",
        "observation-source": transition.job/"observation-source.json",
        "completed": transition.job/"completed.json", "observation": transition.observation_path}
    paths[name].unlink()
    before = snapshot(transition.local.parent)
    assert transition.run()["state"] == "BLOCKED"
    assert snapshot(transition.local.parent) == before and not paths[name].exists()


@pytest.mark.parametrize("damage", ["late_admission", "future_observation", "invented_price",
    "intent_revision", "wrong_binding", "wrong_source", "raw_target", "raw_observed"])
def test_resigned_private_tampering_and_raw_corruption_fail_replay(transition, damage):
    if damage == "late_admission":
        path = transition.target_dir/"admission.json"
        value = json.loads(path.read_bytes())
        value["durable_at"] = "2026-10-27T01:30:00+00:00"
    elif damage in {"future_observation", "invented_price"}:
        path = transition.observation_path
        value = json.loads(path.read_bytes())
        if damage == "future_observation":
            value["processed_at"] = (OBSERVED+timedelta(days=1)).isoformat()
        else:
            value["observed_closes"][0]["close"] = "999"
    elif damage == "intent_revision":
        path = transition.job/"intent.json"
        value = json.loads(path.read_bytes())
        value["implementation_revision"] = "d"*40
    elif damage == "wrong_binding":
        path = transition.job/"target-binding.json"
        value = json.loads(path.read_bytes())
        value["target_id"] = "0"*64
    elif damage == "wrong_source":
        path = transition.job/"observation-source.json"
        value = json.loads(path.read_bytes())
        value["diagnostic_id"] = transition.source.name
    else:
        root = transition.source if damage == "raw_target" else transition.observed_source
        blob, = (root/"capture-store/objects").glob("*")
        blob.write_bytes(blob.read_bytes()[:-1]+b"x")
        value = None
    if value is not None:
        write(path, resign(value))
    assert transition.run()["state"] == "BLOCKED"


def test_captured_actual_api_envelope_is_bound_and_flags_checked(transition):
    wrapper = {**AUTHORITY, "publication_available": True, "service_fresh": True,
               "current_target": True, "publication": transition.public}
    write(transition.public_path, wrapper)
    result = transition.run()
    assert result["state"] == "PASSED"
    assert result["publication_input_kind"] == "CAPTURED_PUBLIC_API_ENVELOPE"
    wrapper["service_fresh"] = False
    write(transition.public_path, wrapper)
    assert transition.run()["state"] == "BLOCKED"


def test_clock_expiry_and_publication_change_during_raw_replay_block(transition, monkeypatch):
    original = audit.verify_etf_shadow_observation
    actual_time = [OBSERVED+timedelta(seconds=2)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return actual_time[0].astimezone(tz or UTC)

    monkeypatch.setattr(audit, "datetime", Clock)

    def slow(*args, **kwargs):
        result = original(*args, **kwargs)
        actual_time[0] += timedelta(seconds=721)
        return result

    monkeypatch.setattr(audit, "verify_etf_shadow_observation", slow)
    result = transition.run(now=None)
    assert result["state"] == "BLOCKED"
    assert result["failures"] == ["ETF_TRANSITION_AUDIT_PUBLICATION_NOT_FRESH"]


def test_cli_no_clock_override_and_only_separate_immutable_report(transition, monkeypatch):
    args = ["--archives", str(transition.runtime.archives), "--local-root", str(transition.local),
        "--published-forward", str(transition.public_path), "--day", str(DAY),
        "--target-id", transition.target["target_id"], "--revision", REVISION]
    output = transition.local.parent/"audit-reports"/"passed.json"
    before = snapshot(transition.local.parent)
    with pytest.raises(SystemExit):
        audit.main(args+["--output", str(output), "--now", OBSERVED.isoformat()])
    assert not output.exists()

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (OBSERVED+timedelta(seconds=2)).astimezone(tz or UTC)

    monkeypatch.setattr(audit, "datetime", Clock)
    writer = audit.write_json_immutable

    def write_report(path, value):
        try:
            writer(path, value)
        except PermissionError:
            assert json.loads(path.read_bytes()) == value

    monkeypatch.setattr(audit, "write_json_immutable", write_report)
    assert audit.main(args+["--output", str(output)]) == 0
    result = json.loads(output.read_bytes())
    assert result["clock_basis"] == "SYSTEM_WALL_CLOCK"
    after = snapshot(transition.local.parent)
    added = str(output.relative_to(transition.local.parent)).replace("\\", "/")
    assert set(after)-set(before) == {added}
    assert all(after[key] == value for key, value in before.items())
    with pytest.raises(SystemExit):
        audit.main(args+["--output", str(output)])
    for bad_output in (transition.local/"audit.json", transition.runtime.archives/"audit.json",
                       transition.public_path.parent/"audit.json"):
        with pytest.raises(SystemExit):
            audit.main(args+["--output", str(bad_output)])
        assert not bad_output.exists()
    future_args = list(args)
    future_args[future_args.index("--day")+1] = "2026-10-28"
    blocked_path = output.parent/"blocked.json"
    assert audit.main(future_args+["--output", str(blocked_path)]) == 2
    blocked = json.loads(blocked_path.read_bytes())
    assert blocked["state"] == "BLOCKED" and blocked["whole_step1_accepted"] is False


def test_live_enabled_or_naive_test_clock_cannot_claim_acceptance(transition, monkeypatch):
    with pytest.raises(ValueError, match="AWARE_CLOCK"):
        transition.run(now=OBSERVED.replace(tzinfo=None))
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(ValueError, match="LIVE_DISABLED"):
        transition.run()


@pytest.mark.parametrize("kind", ["unallocated", "missing_selected"])
def test_unallocated_target_is_valid_but_missing_selected_marks_block(runtime, kind):  # noqa: F811
    runtime.archive(negative=kind == "unallocated")
    target = runtime.run()["entries"][0]["target"]
    runtime.clock[0] = OBSERVED
    runtime.archive(27, missing=kind == "missing_selected")
    runtime.run()
    result = audit.audit_transition(runtime.archives, runtime.local,
        runtime.base/"workflow/public/etf-forward.json", DAY, target["target_id"],
        runtime.calendar, REVISION, now=OBSERVED+timedelta(seconds=1))
    if kind == "unallocated":
        assert result["state"] == "PASSED" and result["observed_close_count"] == 0
        assert result["cash"] is None and result["forward_return"] is None
    else:
        assert result["state"] == "BLOCKED"
        # A degraded service also blocks before the incomplete local observation can pass.
        assert result["etf_shadow_transition_accepted"] is False


def test_publication_mutation_during_replay_is_detected(transition, monkeypatch):
    original = audit.verify_etf_shadow_observation

    def changing(*args, **kwargs):
        result = original(*args, **kwargs)
        value = json.loads(transition.public_path.read_bytes())
        value["source_revision"] = "d"*40
        write(transition.public_path, resign(value))
        return result

    monkeypatch.setattr(audit, "verify_etf_shadow_observation", changing)
    result = transition.run()
    assert result["state"] == "BLOCKED"
    assert result["failures"] == ["ETF_TRANSITION_AUDIT_PUBLICATION_CHANGED_DURING_AUDIT"]


def test_local_immutable_file_change_after_replay_is_detected(transition, monkeypatch):
    original = audit.verify_etf_shadow_observation

    def changing(*args, **kwargs):
        result = original(*args, **kwargs)
        path = transition.target_dir/"admission.json"
        path.write_bytes(path.read_bytes()+b"\n")
        return result

    monkeypatch.setattr(audit, "verify_etf_shadow_observation", changing)
    result = transition.run()
    assert result["state"] == "BLOCKED"
    assert result["failures"] == [
        "ETF_TRANSITION_AUDIT_LOCAL_OR_SOURCE_EVIDENCE_CHANGED_DURING_AUDIT"]
