"""Lock the BaoStock future-session observation contract before fetching it."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
EXPECTED_DEVELOPMENT_REPORT_ID = (
    "baostock-retrospective-development-policy-search-v1"
)
EXPECTED_POLICY_ID = "dev-floor-098-slow12x48-v1"


def _canonical_sha256(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _load_signed(path: Path, signature_key: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"signed JSON is malformed: {path}")
    digest = value.get(signature_key)
    unsigned = {key: item for key, item in value.items() if key != signature_key}
    if not isinstance(digest, str) or _canonical_sha256(unsigned) != digest:
        raise ValueError(f"signed JSON verification failed: {path}")
    return cast(dict[str, Any], value)


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def build_preregistration(
    *,
    development: dict[str, Any],
    dataset: dict[str, Any],
    created_at: datetime,
    implementation_hashes: dict[str, str],
) -> dict[str, Any]:
    if development.get("report_id") != EXPECTED_DEVELOPMENT_REPORT_ID:
        raise ValueError("unexpected development report identity")
    if development.get("promotion") != "NOT_ELIGIBLE":
        raise ValueError("development report must remain NOT_ELIGIBLE")
    manifest = dataset.get("manifest")
    future_lock = development.get("future_observation_lock")
    leader = development.get("observed_leader")
    candidates = development.get("candidates")
    if (
        not isinstance(manifest, dict)
        or not isinstance(future_lock, dict)
        or not isinstance(leader, dict)
        or not isinstance(candidates, list)
        or future_lock.get("policy_id") != EXPECTED_POLICY_ID
        or leader.get("policy_id") != EXPECTED_POLICY_ID
        or future_lock.get("minimum_complete_future_sessions") != 20
        or future_lock.get("partial_same_day_observations_enter_metrics") is not False
        or future_lock.get("parameter_changes_before_completion") is not False
    ):
        raise ValueError("development future lock is inconsistent")
    candidate = next(
        (
            row
            for row in candidates
            if isinstance(row, dict) and row.get("policy_id") == EXPECTED_POLICY_ID
        ),
        None,
    )
    if not isinstance(candidate, dict):
        raise ValueError("locked development policy is missing")
    dataset_claim = development.get("dataset")
    if (
        not isinstance(dataset_claim, dict)
        or dataset_claim.get("data_version") != manifest.get("data_version")
        or dataset_claim.get("checksum") != manifest.get("checksum")
        or dataset_claim.get("evidence_sha256") != dataset.get("evidence_sha256")
        or dataset.get("symbol_count") != 75
        or dataset.get("sector_count") != 15
        or dataset.get("session_count") != 368
        or dataset.get("session_bar_count_per_symbol") != 48
        or dataset.get("bar_interval") != "5m"
        or dataset.get("point_in_time") is not False
        or dataset.get("future_adjustment_knowledge_possible") is not True
    ):
        raise ValueError("development report and long dataset are not identity bound")
    required_hashes = {
        "probe_baostock_forward_session.py",
        "run_baostock_locked_forward_observation.py",
        "minute_backtest.py",
    }
    if set(implementation_hashes) != required_hashes or any(
        len(value) != 64 for value in implementation_hashes.values()
    ):
        raise ValueError("future observation implementation hashes are incomplete")

    first_day = str(future_lock["first_eligible_trading_day"])
    unsigned: dict[str, Any] = {
        "schema_version": 1,
        "preregistration_id": "baostock-locked-forward-observation-v1",
        "created_at": created_at.astimezone(SHANGHAI).isoformat(),
        "status": "LOCKED_AFTER_FIRST_SESSION_OPEN_BEFORE_TARGET_SESSION_FETCH",
        "timing_disclosure": {
            "development_policy_locked_before_first_session_open": True,
            "evaluation_contract_fixed_after_first_session_open": True,
            "target_session_data_fetched_before_this_lock": False,
            "claim_as_blind_preregistration": False,
        },
        "development_evidence": {
            "report_id": development["report_id"],
            "evidence_sha256": development["evidence_sha256"],
            "locked_at": future_lock["locked_at"],
            "policy_id": candidate["policy_id"],
            "policy_hash": candidate["policy_hash"],
            "parameters": candidate["policy"],
        },
        "warmup_dataset": {
            "data_version": manifest["data_version"],
            "checksum": manifest["checksum"],
            "evidence_sha256": dataset["evidence_sha256"],
            "session_count": dataset["session_count"],
            "symbol_count": dataset["symbol_count"],
            "sector_count": dataset["sector_count"],
            "bar_interval": dataset["bar_interval"],
            "last_trading_day": dataset["trading_days"][-1],
            "point_in_time": False,
            "future_adjustment_knowledge_possible": True,
        },
        "collection_lock": {
            "first_eligible_trading_day": first_day,
            "minimum_complete_future_sessions": 20,
            "provider": "BaoStock",
            "provider_frequency": "5",
            "adjustflag": "2",
            "required_symbol_count": 75,
            "required_sector_count": 15,
            "required_bars_per_symbol_session": 48,
            "required_total_bars_per_session": 3600,
            "earliest_collection_time_china": "15:10:00",
            "partial_sessions_enter_dataset": False,
            "suspended_or_zero_placeholder_sessions_enter_dataset": False,
            "first_successful_complete_snapshot_is_immutable": True,
            "anchor_day_must_exactly_match_frozen_warmup_dataset": True,
            "anchor_revision_action": "REJECT_TARGET_SESSION",
        },
        "evaluation_lock": {
            "metric_sessions": "future complete sessions only",
            "warmup_sessions_enter_metrics": False,
            "cost_bps": [0, 5],
            "parameter_selection_after_future_read": False,
            "parameter_changes_before_twenty_sessions": False,
            "required_gates_after_twenty_sessions": [
                {
                    "gate_id": "OVERLAY_DRAWDOWN_NOT_WORSE_AT_5_BPS",
                    "rule": "overlay maximum drawdown must be greater than or equal to base",
                },
                {
                    "gate_id": "RETURN_SHORTFALL_WITHIN_50_BPS_AT_5_BPS",
                    "rule": "overlay total return minus base total return must be at least -0.005",
                },
                {
                    "gate_id": "MEAN_EXPOSURE_AT_LEAST_90_PERCENT",
                    "rule": "overlay mean exposure must be at least 0.90",
                },
                {
                    "gate_id": "MEAN_TURNOVER_PER_SYMBOL_AT_MOST_FIVE",
                    "rule": (
                        "total mean turnover per symbol across the first twenty "
                        "sessions must be at most 5"
                    ),
                },
                {
                    "gate_id": "LEAVE_ONE_SECTOR_OUT_STABILITY",
                    "rule": (
                        "at least 12 of 15 leave-one-sector-out returns must be no "
                        "worse than base by more than 100 bps"
                    ),
                },
            ],
            "conclusion_before_minimum_sessions": "INCOMPLETE",
            "pass_does_not_authorize_paper_or_live": True,
            "failure_must_be_retained": True,
        },
        "causal_contract": {
            "signal_observed_at": "FIVE_MINUTE_BAR_CLOSE_T",
            "exposure_change_at": "NEXT_FIVE_MINUTE_BAR_OPEN",
            "same_bar_signal_execution": False,
            "reduction_only": True,
            "maximum_exposure": 1,
            "external_order_transmission": False,
        },
        "implementation_sha256": dict(sorted(implementation_hashes.items())),
        "interpretation_lock": {
            "promotion": "NOT_ELIGIBLE",
            "realtime_readiness": False,
            "real_data_paper_readiness": False,
            "current_non_point_in_time_universe": True,
            "qfq_may_use_future_adjustment_knowledge": True,
            "no_paper_or_shadow_session_evidence": True,
        },
    }
    return {**unsigned, "preregistration_sha256": _canonical_sha256(unsigned)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--development-report",
        type=Path,
        default=Path("deployment/evidence/development-policy-search/report.json"),
    )
    parser.add_argument(
        "--dataset-manifest",
        type=Path,
        default=Path("deployment/evidence/long-five-minute-market-data/manifest.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "docs/preregistrations/baostock-locked-forward-observation-v1.json"
        ),
    )
    parser.add_argument(
        "--deployment-copy",
        type=Path,
        default=Path("deployment/evidence/forward-observation/preregistration.json"),
    )
    args = parser.parse_args()
    for target in (args.output, args.deployment_copy):
        if target.exists():
            raise FileExistsError(f"refusing to overwrite preregistration: {target}")
    root = Path(__file__).resolve().parents[1]
    implementation_paths = {
        "probe_baostock_forward_session.py": root
        / "scripts/probe_baostock_forward_session.py",
        "run_baostock_locked_forward_observation.py": root
        / "scripts/run_baostock_locked_forward_observation.py",
        "minute_backtest.py": root / "src/qtrade/research/minute_backtest.py",
    }
    preregistration = build_preregistration(
        development=_load_signed(args.development_report, "evidence_sha256"),
        dataset=_load_signed(args.dataset_manifest, "evidence_sha256"),
        created_at=datetime.now(SHANGHAI),
        implementation_hashes={
            name: _file_sha256(path) for name, path in implementation_paths.items()
        },
    )
    encoded = json.dumps(preregistration, ensure_ascii=False, indent=2) + "\n"
    for target in (args.output, args.deployment_copy):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded, encoding="utf-8")
    print(
        json.dumps(
            {
                "status": preregistration["status"],
                "preregistration_sha256": preregistration[
                    "preregistration_sha256"
                ],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
