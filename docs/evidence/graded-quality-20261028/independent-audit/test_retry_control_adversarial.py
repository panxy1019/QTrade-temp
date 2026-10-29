"""Independent local-only probes for exact failed-attempt retry deferral."""

import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.collection_retry_policy import MODE, SCHEMA, retry_deferrals
from qtrade.research.data_quality import classify_task, retry_task_identity, validate_quality
from scripts import unified_queue as queue_module

NOW = datetime(2026, 10, 28, 2, tzinfo=UTC)
DAY = "2026-10-27"
UNIVERSE = "b"*64
SYMBOL = "600036"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def resign(value):
    return signed({k: v for k, v in value.items() if k != "evidence_sha256"})


@pytest.fixture
def retry_fixture(tmp_path, monkeypatch):
    class Clock(datetime):
        current = NOW

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz)

    monkeypatch.setattr(queue_module, "datetime", Clock)
    queue = queue_module.UnifiedCollectionQueue(tmp_path/"fixture.sqlite")
    master = signed({**AUTHORITY, "universe_version": UNIVERSE, "target_actual": 2,
                     "securities": [{"symbol": s, "security_type": "STOCK", "in_target": True,
                                     "status": "ACTIVE_OBSERVED", "issues": []}
                                    for s in [SYMBOL, "600037"]]})
    calendar = Calendar(Path(queue_module.__file__).resolve().parents[1]/"configs/observer_calendar_2026.json")
    queue.plan(master, calendar, datetime(2026, 10, 27, 9, tzinfo=UTC))
    with queue.connect() as conn:
        conn.execute("UPDATE collection_tasks SET state='FAILED',attempts=3,last_error='STALE_SOURCE_END',consecutive_failures=2")
    with queue.connect() as conn:
        row = dict(conn.execute("SELECT * FROM collection_tasks WHERE symbol=? AND frequency='day'", (SYMBOL,)).fetchone())
    observation = signed({"source": "BaoStock raw daily status backfill", "symbol": SYMBOL,
                          "observed_at": NOW.isoformat(), "historical_available_at": None,
                          "certified_at_decision": False,
                          "rows": [{"date": DAY, "code": "sh."+SYMBOL, "tradestatus": "0", "isST": "0"}]})
    issuer = {"symbol": SYMBOL, "effective_start_date": DAY,
              "claim": "ISSUER_ANNOUNCED_SUSPENSION_START", "raw_sha256": "a"*64,
              "observed_at": NOW.isoformat()}
    case = classify_task(row, [observation], [issuer], now=NOW)
    quality = signed({**AUTHORITY, "schema_version": "qtrade.data-quality-observation.v1",
                      "observed_at": NOW.isoformat(), "universe_version": UNIVERSE,
                      "source_revision": "a"*40, "production_admission_effect": "NONE",
                      "execution_admitted": False, "cases": [case],
                      "case_counts": {"SUSPENDED": 1}, "source_issues": [],
                      "recent_status": {"available": False, "content_verified_symbols": 0}})
    policy = signed({**AUTHORITY, "schema_version": SCHEMA, "mode": MODE, "state": "APPROVED",
                     "universe_version": UNIVERSE, "execution_admitted": False,
                     "paper_constraints_changed": False, "model_input_admission_changed": False,
                     "symbols": [SYMBOL], "max_deferral_seconds": 21600,
                     "max_status_age_seconds": 86400, "quality_max_age_seconds": 1200,
                     "approved_at": (NOW-timedelta(hours=1)).isoformat(), "effective_from_day": "2026-10-28",
                     "authorization_kind": "EXPLICIT_HUMAN_ACTION", "approved_by": "external-fixture-human",
                     "authorization_message_sha256": "f"*64})
    write(tmp_path/"data-quality/approved-retry-policy.json", policy)
    write(tmp_path/"data-quality/public/quality.json", quality)
    return SimpleNamespace(base=tmp_path, queue=queue, row=row, quality=quality, policy=policy, clock=Clock)


def controls(fixture, **kwargs):
    return retry_deferrals(fixture.base, kwargs.get("universe", UNIVERSE), kwargs.get("frequency", "day"),
                           kwargs.get("day", DAY), now=kwargs.get("now", NOW))


def claim(fixture, tokens=(), *, now=NOW):
    return fixture.queue.claim(UNIVERSE, "day", date.fromisoformat(DAY), now=now,
                               code_revision="c"*40, limit=2, deferred_tasks=tuple(tokens))


def exact_row(fixture):
    with fixture.queue.connect() as conn:
        return dict(conn.execute("SELECT * FROM collection_tasks WHERE symbol=? AND frequency='day'", (SYMBOL,)).fetchone())


def test_positive_control_has_real_matching_task_and_preserves_skipped_row(retry_fixture):
    f = retry_fixture
    assert validate_quality(f.quality, now=NOW)["case_counts"] == {"SUSPENDED": 1}
    result = controls(f)
    assert result["deferred_symbols"] == [SYMBOL] and len(result["deferrals"]) == 1
    token = result["deferrals"][0]
    assert token["policy_sha256"] == f.policy["evidence_sha256"]
    assert token["quality_sha256"] == f.quality["evidence_sha256"]
    assert datetime.fromisoformat(token["expires_at"]) <= NOW+timedelta(seconds=1200)
    before = exact_row(f)
    batch = claim(f, result["deferrals"])
    assert batch["spec"]["symbols"] == ["600037"]
    assert exact_row(f) == before
    assert not (f.base/"unified-data/lake").exists()


def test_absent_policy_uses_old_retry_without_reading_optional_quality(retry_fixture):
    f = retry_fixture
    (f.base/"data-quality/approved-retry-policy.json").unlink()
    (f.base/"data-quality/public/quality.json").write_text("not json")
    result = controls(f)
    assert result["state"] == "NO_APPROVED_RETRY_POLICY" and result["deferrals"] == []
    assert SYMBOL in claim(f)["spec"]["symbols"]


@pytest.mark.parametrize("scope", ["day", "frequency", "universe"])
def test_diagnosis_cannot_suppress_different_requested_scope(retry_fixture, scope):
    kwargs = {"day": "2026-10-28"} if scope == "day" else {"frequency": "5min"} if scope == "frequency" else {"universe": "e"*64}
    assert controls(retry_fixture, **kwargs)["deferrals"] == []


@pytest.mark.parametrize("field,value", [("desired_end", DAY+"T06:55:00+00:00"),
    ("attempts", 4), ("next_attempt_at", "2026-10-27T08:59:00+00:00"),
    ("last_error", "NEW_SOURCE_CONFLICT"), ("consecutive_failures", 3),
    ("last_success_batch", "e"*32), ("active_batch", "e"*32)])
def test_changed_failed_attempt_after_summary_restores_current_retry(retry_fixture, field, value):
    f = retry_fixture
    tokens = controls(f)["deferrals"]
    with f.queue.connect() as conn:
        conn.execute(f"UPDATE collection_tasks SET {field}=? WHERE symbol=? AND frequency='day'", (value, SYMBOL))
    batch = claim(f, tokens)
    # Different desired-end groups can form a separate batch; at least the old token is not a veto.
    if SYMBOL not in batch["spec"]["symbols"]:
        batch = claim(f, tokens)
    assert SYMBOL in batch["spec"]["symbols"]


@pytest.mark.parametrize("state,partial", [("PLANNED", False), ("INTERRUPTED", False), ("FAILED", True)])
def test_stale_failed_diagnosis_never_hides_unattempted_interrupted_or_partial(retry_fixture, state, partial):
    f = retry_fixture
    tokens = controls(f)["deferrals"]
    with f.queue.connect() as conn:
        conn.execute("UPDATE collection_tasks SET state=?,completed_end=? WHERE symbol=? AND frequency='day'",
                     (state, DAY+"T06:55:00+00:00" if partial else None, SYMBOL))
    assert SYMBOL in claim(f, tokens)["spec"]["symbols"]


@pytest.mark.parametrize("state,partial", [("PLANNED", False), ("INTERRUPTED", False), ("FAILED", True)])
def test_state_guard_survives_even_a_matching_token_fingerprint(retry_fixture, state, partial):
    f = retry_fixture
    token = deepcopy(controls(f)["deferrals"][0])
    with f.queue.connect() as conn:
        conn.execute("UPDATE collection_tasks SET state=?,completed_end=? WHERE symbol=? AND frequency='day'",
                     (state, DAY+"T06:55:00+00:00" if partial else None, SYMBOL))
    token["task_sha256"] = retry_task_identity(exact_row(f))
    assert SYMBOL in claim(f, [token])["spec"]["symbols"]


@pytest.mark.parametrize("seconds", [1201, 21601])
def test_token_expiry_and_quality_ttl_use_actual_claim_clock_not_caller_clock(retry_fixture, seconds):
    f = retry_fixture
    tokens = controls(f)["deferrals"]
    f.clock.current = NOW+timedelta(seconds=seconds)
    assert SYMBOL in claim(f, tokens, now=NOW)["spec"]["symbols"]


@pytest.mark.parametrize("defect", ["expired", "future_too_far", "naive", "wrong_day", "wrong_frequency",
                                    "wrong_universe", "wrong_symbol", "wrong_hash", "missing_key", "not_object",
                                    "future_quality", "stale_quality_clock", "future_source", "stale_source_clock",
                                    "missing_policy_hash", "bad_quality_hash"])
def test_damaged_or_wrong_scope_token_restores_original_retry(retry_fixture, defect):
    f = retry_fixture
    token = deepcopy(controls(f)["deferrals"][0])
    if defect == "expired": token["expires_at"] = (NOW-timedelta(seconds=1)).isoformat()
    elif defect == "future_too_far": token["expires_at"] = (NOW+timedelta(hours=7)).isoformat()
    elif defect == "naive": token["expires_at"] = "2026-10-28T03:00:00"
    elif defect == "wrong_day": token["trading_day"] = "2026-10-28"
    elif defect == "wrong_frequency": token["frequency"] = "5min"
    elif defect == "wrong_universe": token["universe_version"] = "e"*64
    elif defect == "wrong_symbol": token["symbol"] = "600038"
    elif defect == "wrong_hash": token["task_sha256"] = "0"*64
    elif defect == "missing_key": token.pop("expires_at")
    elif defect == "future_quality": token["quality_observed_at"] = (NOW+timedelta(seconds=1)).isoformat()
    elif defect == "stale_quality_clock": token["quality_observed_at"] = (NOW-timedelta(seconds=1201)).isoformat()
    elif defect == "future_source": token["source_observed_at"] = (NOW+timedelta(seconds=1)).isoformat()
    elif defect == "stale_source_clock": token["source_observed_at"] = (NOW-timedelta(seconds=86401)).isoformat()
    elif defect == "missing_policy_hash": token.pop("policy_sha256")
    elif defect == "bad_quality_hash": token["quality_sha256"] = "broken"
    else: token = []
    assert SYMBOL in claim(f, [token])["spec"]["symbols"]


@pytest.mark.parametrize("source", ["policy_list", "quality_list", "policy_bad_json", "quality_bad_json", "missing_quality"])
def test_bad_optional_source_falls_back_without_stopping_ingestion(retry_fixture, source):
    f = retry_fixture
    path = f.base/("data-quality/approved-retry-policy.json" if source.startswith("policy") else "data-quality/public/quality.json")
    if source == "missing_quality": path.unlink()
    else: path.write_text("[]" if source.endswith("list") else "not-json")
    result = controls(f)
    assert result["deferrals"] == [] and result["production_admission_effect"] == "NONE"
    assert SYMBOL in claim(f, result["deferrals"])["spec"]["symbols"]


@pytest.mark.parametrize("field,value", [("state", "PROPOSED"), ("paper_constraints_changed", True),
    ("model_input_admission_changed", True), ("live_trading_enabled", True),
    ("approved_at", "2099-01-01T00:00:00+00:00"), ("authorization_message_sha256", "0"*64)])
def test_new_retry_approval_never_supplies_other_authority(retry_fixture, field, value):
    f = retry_fixture
    policy = deepcopy(f.policy)
    policy[field] = value
    write(f.base/"data-quality/approved-retry-policy.json", resign(policy))
    result = controls(f)
    assert result["deferrals"] == [] and result["execution_admitted"] is False


@pytest.mark.parametrize("defect", ["stale_quality", "missing_pdf_id", "missing_vendor_id", "old_status", "unknown_state"])
def test_stale_or_incomplete_suspension_proof_cannot_defer(retry_fixture, defect):
    f = retry_fixture
    quality = deepcopy(f.quality)
    case = quality["cases"][0]
    if defect == "stale_quality": quality["observed_at"] = (NOW-timedelta(seconds=1201)).isoformat()
    elif defect == "missing_pdf_id": case["issuer_start_evidence"] = []
    elif defect == "missing_vendor_id": case["day_state_evidence"] = []
    elif defect == "old_status": case["source_observed_at"] = (NOW-timedelta(seconds=86401)).isoformat()
    else:
        case["classification"] = "UNKNOWN"
        case["market_state"] = "UNKNOWN"
        case["proposed_recheck_at"] = None
        quality["case_counts"] = {"UNKNOWN": 1}
    write(f.base/"data-quality/public/quality.json", resign(quality))
    assert controls(f)["deferrals"] == []
