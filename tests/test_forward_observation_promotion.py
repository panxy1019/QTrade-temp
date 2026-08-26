from __future__ import annotations

import json
import os
import stat
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest

from scripts.promote_baostock_forward_observation import (
    _FROZEN_FORWARD_ANCHOR_SHA256,
    _atomic_publish,
    validate_progression,
    validate_source_sessions,
)


def _report(*, count: int, generated_at: str) -> dict[str, object]:
    return {
        "accepted_future_session_count": count,
        "future_sessions": [
            {
                "target_day": f"2026-09-{day:02d}",
                "evidence_sha256": str(day) * 64,
                "bar_count": 3600,
                "anchor_exact_match": True,
            }
            for day in range(1, count + 1)
        ],
        "preregistration_sha256": "a" * 64,
        "policy_hash": "b" * 64,
        "generated_at": generated_at,
    }


def test_forward_observation_progression_requires_append_only_sessions() -> None:
    current = _report(count=1, generated_at="2026-09-01T16:00:00+08:00")
    candidate = _report(count=2, generated_at="2026-09-02T16:00:00+08:00")

    validate_progression(current, candidate)

    rewritten = deepcopy(candidate)
    rewritten_sessions = cast(list[dict[str, Any]], rewritten["future_sessions"])
    rewritten_sessions[0]["target_day"] = "2026-09-03"
    with pytest.raises(ValueError, match="rewrites"):
        validate_progression(current, rewritten)

    unchanged = _report(count=1, generated_at="2026-09-02T16:00:00+08:00")
    with pytest.raises(ValueError, match="add at least one"):
        validate_progression(current, unchanged)


def test_forward_observation_progression_keeps_lock_and_time_monotonic() -> None:
    current = _report(count=1, generated_at="2026-09-01T16:00:00+08:00")
    candidate = _report(count=2, generated_at="2026-09-02T16:00:00+08:00")

    changed_lock = deepcopy(candidate)
    changed_lock["policy_hash"] = "c" * 64
    with pytest.raises(ValueError, match="frozen observation identity"):
        validate_progression(current, changed_lock)

    stale = deepcopy(candidate)
    stale["generated_at"] = current["generated_at"]
    with pytest.raises(ValueError, match="strictly advance"):
        validate_progression(current, stale)


def test_atomic_forward_observation_publish_replaces_complete_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "report.json"
    candidate = tmp_path / "candidate.json"
    destination.write_bytes(b'{"generation":1}\n')
    candidate.write_bytes(b'{"generation":2}\n')
    synced_types: list[str] = []
    real_fsync = os.fsync

    def track_fsync(file_descriptor: int) -> None:
        mode = os.fstat(file_descriptor).st_mode
        synced_types.append("directory" if stat.S_ISDIR(mode) else "file")
        real_fsync(file_descriptor)

    monkeypatch.setattr(os, "fsync", track_fsync)

    _atomic_publish(candidate, destination)

    assert destination.read_bytes() == candidate.read_bytes()
    assert synced_types == ["file", "directory"]
    assert list(tmp_path.glob(".report.json.*.tmp")) == []


def test_atomic_forward_observation_publish_rejects_changed_candidate(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "report.json"
    candidate = tmp_path / "candidate.json"
    destination.write_bytes(b'{"generation":1}\n')
    original = {"generation": 2}
    expected_digest = sha256(
        json.dumps(original, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    replacement = {"generation": 3}
    replacement["evidence_sha256"] = sha256(
        json.dumps(replacement, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    candidate.write_text(json.dumps(replacement), encoding="utf-8")

    with pytest.raises(ValueError, match="changed after verification"):
        _atomic_publish(
            candidate,
            destination,
            expected_evidence_sha256=expected_digest,
        )

    assert destination.read_bytes() == b'{"generation":1}\n'
    assert list(tmp_path.glob(".report.json.*.tmp")) == []


def test_forward_observation_source_sessions_must_match_candidate() -> None:
    candidate = _report(count=1, generated_at="2026-09-01T16:00:00+08:00")
    source_reports = [
        {
            "target_day": "2026-09-01",
            "evidence_sha256": "1" * 64,
            "schema_version": 1,
            "report_id": "baostock-complete-forward-session-v1",
            "collected_at": "2026-09-01T16:00:00+08:00",
            "bar_count": 3600,
            "anchor_exact_match": True,
            "anchor_day": "2026-08-25",
            "anchor_sha256": _FROZEN_FORWARD_ANCHOR_SHA256,
            "preregistration_id": "baostock-locked-forward-observation-v1",
            "preregistration_sha256": "a" * 64,
            "provider": "BaoStock",
            "provider_version": "0.9.3",
            "frequency": "5",
            "adjustflag": "2",
            "manifest": {
                "data_version": "baostock-forward-2026-09-01-qfq-v1",
                "source": "BaoStock 0.9.3 native 5-minute; adjustflag=2",
                "schema_version": "qtrade.bar.v1",
                "ingestion_time": "2026-09-01T07:59:00+00:00",
                "checksum": "b" * 64,
                "quality_status": "PASSED",
            },
            "point_in_time": False,
            "future_adjustment_knowledge_possible": True,
            "realtime_readiness": False,
            "real_data_paper_readiness": False,
            "promotion": "NOT_ELIGIBLE",
            "external_order_transmission": False,
        }
    ]
    validation_time = datetime.fromisoformat("2026-09-01T17:00:00+08:00")

    def validate(candidate_value: dict[str, object]) -> None:
        validate_source_sessions(
            candidate_value,
            source_reports,
            validation_time=validation_time,
        )

    validate(candidate)

    source_reports[0]["evidence_sha256"] = "2" * 64
    with pytest.raises(ValueError, match="source sessions"):
        validate(candidate)

    source_reports[0]["evidence_sha256"] = "1" * 64
    source_reports[0]["anchor_day"] = "2026-08-24"
    with pytest.raises(ValueError, match="source sessions"):
        validate(candidate)

    source_reports[0]["anchor_day"] = "2026-08-25"
    source_reports[0]["external_order_transmission"] = True
    with pytest.raises(ValueError, match="source sessions"):
        validate(candidate)

    source_reports[0]["external_order_transmission"] = False
    cast(dict[str, object], source_reports[0]["manifest"])[
        "data_version"
    ] = "baostock-forward-wrong-day-qfq-v1"
    with pytest.raises(ValueError, match="source sessions"):
        validate(candidate)

    cast(dict[str, object], source_reports[0]["manifest"])[
        "data_version"
    ] = "baostock-forward-2026-09-01-qfq-v1"
    source_reports[0]["collected_at"] = "2026-09-01T15:09:59+08:00"
    with pytest.raises(ValueError, match="source sessions"):
        validate(candidate)

    source_reports[0]["collected_at"] = "2026-09-01T16:00:00+08:00"
    source_reports[0]["provider_version"] = "0.9.4"
    cast(dict[str, object], source_reports[0]["manifest"])[
        "source"
    ] = "BaoStock 0.9.4 native 5-minute; adjustflag=2"
    with pytest.raises(ValueError, match="source sessions"):
        validate(candidate)

    source_reports[0]["provider_version"] = "0.9.3"
    cast(dict[str, object], source_reports[0]["manifest"])[
        "source"
    ] = "BaoStock 0.9.3 native 5-minute; adjustflag=2"
    future_candidate = deepcopy(candidate)
    future_candidate["generated_at"] = "2027-09-01T16:00:00+08:00"
    with pytest.raises(ValueError, match="source sessions"):
        validate(future_candidate)

    compact_day_candidate = deepcopy(candidate)
    compact_sessions = cast(
        list[dict[str, object]], compact_day_candidate["future_sessions"]
    )
    compact_sessions[0]["target_day"] = "20260901"
    source_reports[0]["target_day"] = "20260901"
    with pytest.raises(ValueError, match="source sessions"):
        validate(compact_day_candidate)
