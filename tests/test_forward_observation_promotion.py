from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest

from scripts.promote_baostock_forward_observation import (
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
    tmp_path: Path,
) -> None:
    destination = tmp_path / "report.json"
    candidate = tmp_path / "candidate.json"
    destination.write_bytes(b'{"generation":1}\n')
    candidate.write_bytes(b'{"generation":2}\n')

    _atomic_publish(candidate, destination)

    assert destination.read_bytes() == candidate.read_bytes()
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
            "bar_count": 3600,
            "anchor_exact_match": True,
        }
    ]

    validate_source_sessions(candidate, source_reports)

    source_reports[0]["evidence_sha256"] = "2" * 64
    with pytest.raises(ValueError, match="source sessions"):
        validate_source_sessions(candidate, source_reports)
