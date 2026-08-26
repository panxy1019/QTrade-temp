"""Run a small retrospective search without reusing the reserved tail in the engine."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from qtrade.market_data import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.research import run_minute_overlay_backtest
from qtrade.runtime import MinuteOverlayPolicy
from scripts.run_sina_minute_overlay_backtest import _load_dataset, _metrics

SHANGHAI = ZoneInfo("Asia/Shanghai")
DATA_VERSION = "baostock-five-minute-stock-sw15x5-qfq-20250102-20260825-v1"
DEVELOPMENT_SESSION_COUNT = 360
RESERVED_TAIL_SESSION_COUNT = 8

DEVELOPMENT_POLICIES: tuple[tuple[str, MinuteOverlayPolicy], ...] = (
    (
        "dev-floor-095-slow6x24-v1",
        MinuteOverlayPolicy(
            6, 24, 12, Decimal("0.012"), Decimal("0.035"), Decimal("0.95"), 6
        ),
    ),
    (
        "dev-floor-098-slow6x24-v1",
        MinuteOverlayPolicy(
            6, 24, 12, Decimal("0.012"), Decimal("0.035"), Decimal("0.98"), 6
        ),
    ),
    (
        "dev-floor-095-slow12x48-v1",
        MinuteOverlayPolicy(
            12, 48, 24, Decimal("0.02"), Decimal("0.04"), Decimal("0.95"), 6
        ),
    ),
    (
        "dev-floor-098-slow12x48-v1",
        MinuteOverlayPolicy(
            12, 48, 24, Decimal("0.02"), Decimal("0.04"), Decimal("0.98"), 6
        ),
    ),
)


def _canonical_sha256(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _development_split(
    trading_days: tuple[date, ...],
) -> tuple[tuple[date, ...], tuple[date, ...]]:
    expected = DEVELOPMENT_SESSION_COUNT + RESERVED_TAIL_SESSION_COUNT
    if len(trading_days) != expected:
        raise ValueError(f"development search requires exactly {expected} sessions")
    return (
        trading_days[:DEVELOPMENT_SESSION_COUNT],
        trading_days[DEVELOPMENT_SESSION_COUNT:],
    )


def _observed_leader(candidates: list[dict[str, object]]) -> str:
    constrained: list[dict[str, object]] = []
    for row in candidates:
        execution = cast(dict[str, object], row["execution"])
        if (
            float(str(row["drawdown_improvement"])) >= 0.02
            and float(str(execution["mean_exposure"])) >= 0.90
            and float(str(execution["mean_turnover_per_symbol"])) <= 100
        ):
            constrained.append(row)
    if not constrained:
        raise ValueError("development search has no candidate satisfying its constraints")
    constrained.sort(
        key=lambda row: (
            -float(str(row["return_delta_vs_base"])),
            -float(str(row["drawdown_improvement"])),
            float(
                str(
                    cast(dict[str, object], row["execution"])[
                        "mean_turnover_per_symbol"
                    ]
                )
            ),
            str(row["policy_id"]),
        )
    )
    return str(constrained[0]["policy_id"])


def _policy_payload(policy: MinuteOverlayPolicy) -> dict[str, str]:
    return {key: str(value) for key, value in asdict(policy).items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=Path("runtime/data/baostock-five-minute"),
    )
    parser.add_argument("--data-version", default=DATA_VERSION)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite development search: {args.output}")

    evidence, manifest, all_bars = _load_dataset(
        args.dataset_root,
        args.data_version,
        expected_classification="free_historical_qfq_5m_current_non_pit_not_eligible",
        expected_interval="5m",
    )
    trading_days = tuple(date.fromisoformat(value) for value in evidence["trading_days"])
    development_days, reserved_tail_days = _development_split(trading_days)
    development_set = frozenset(development_days)
    bars = tuple(bar for bar in all_bars if bar.trading_day in development_set)
    expected_bar_count = (
        int(evidence["symbol_count"])
        * len(development_days)
        * len(SINA_FIVE_MINUTE_SESSION_TIMES)
    )
    if len(bars) != expected_bar_count:
        raise ValueError("development search bar subset is inconsistent")

    candidates: list[dict[str, object]] = []
    for policy_id, policy in DEVELOPMENT_POLICIES:
        result = run_minute_overlay_backtest(
            bars,
            policy_id=policy_id,
            policy=policy,
            cost_bps=5,
        )
        metrics = result.metrics(days=set(development_days))
        execution = result.execution_metrics(days=set(development_days))
        base = metrics["base"]
        overlay = metrics["overlay"]
        candidates.append(
            {
                "policy_id": policy_id,
                "policy_hash": result.policy_hash,
                "policy": _policy_payload(policy),
                "base": _metrics(base),
                "overlay": _metrics(overlay),
                "return_delta_vs_base": overlay.total_return - base.total_return,
                "drawdown_improvement": (
                    abs(base.maximum_drawdown) - abs(overlay.maximum_drawdown)
                ),
                "execution": execution,
            }
        )

    generated_at = datetime.now(SHANGHAI)
    leader_id = _observed_leader(candidates)
    unsigned: dict[str, Any] = {
        "schema_version": 1,
        "report_id": "baostock-retrospective-development-policy-search-v1",
        "classification": (
            "RETROSPECTIVE_DEVELOPMENT_ONLY_POLICY_SEARCH_NOT_INDEPENDENT"
        ),
        "generated_at": generated_at.isoformat(),
        "dataset": {
            "data_version": manifest.data_version,
            "checksum": manifest.checksum,
            "evidence_sha256": _canonical_sha256(evidence),
            "bar_interval": evidence["bar_interval"],
            "symbol_count": evidence["symbol_count"],
            "sector_count": evidence["sector_count"],
            "point_in_time": evidence["point_in_time"],
            "future_adjustment_knowledge_possible": evidence[
                "future_adjustment_knowledge_possible"
            ],
        },
        "development_contract": {
            "session_count": len(development_days),
            "observed_start": development_days[0].isoformat(),
            "observed_end": development_days[-1].isoformat(),
            "processed_bar_count": len(bars),
            "cost_bps": 5,
        },
        "reserved_tail": {
            "session_count": len(reserved_tail_days),
            "trading_days": [value.isoformat() for value in reserved_tail_days],
            "entered_strategy_engine": False,
            "entered_candidate_metrics": False,
        },
        "contamination_disclosure": {
            "prior_reserved_tail_evidence_available_before_candidate_design": True,
            "candidate_search_is_not_blind": True,
            "observed_leader_selection_is_post_hoc": True,
        },
        "candidate_count": len(candidates),
        "candidates": candidates,
        "observed_leader": {
            "policy_id": leader_id,
            "selection_timing": "POST_HOC_DEVELOPMENT_ONLY",
            "constraints": {
                "minimum_drawdown_improvement": 0.02,
                "minimum_mean_exposure": 0.90,
                "maximum_mean_turnover_per_symbol": 100,
            },
            "objective": "maximum return delta versus base after constraints",
        },
        "future_observation_lock": {
            "locked_at": generated_at.isoformat(),
            "policy_id": leader_id,
            "first_eligible_trading_day": "2026-08-26",
            "minimum_complete_future_sessions": 20,
            "partial_same_day_observations_enter_metrics": False,
            "parameter_changes_before_completion": False,
        },
        "causal_contract": {
            "signal_observed_at": "FIVE_MINUTE_BAR_CLOSE_T",
            "exposure_change_at": "NEXT_FIVE_MINUTE_BAR_OPEN",
            "same_bar_signal_execution": False,
            "reduction_only": True,
            "maximum_exposure": 1,
        },
        "realtime_readiness": False,
        "real_data_paper_readiness": False,
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": [
            "RETROSPECTIVE_POST_HOC_DEVELOPMENT_SEARCH",
            "PRIOR_RESERVED_TAIL_EVIDENCE_ALREADY_VISIBLE",
            "FUTURE_TWENTY_SESSION_OBSERVATION_INCOMPLETE",
            "CURRENT_NON_POINT_IN_TIME_UNIVERSE",
            "QFQ_MAY_USE_FUTURE_ADJUSTMENT_KNOWLEDGE",
            "NO_PAPER_OR_SHADOW_SESSION_EVIDENCE",
        ],
        "external_order_transmission": False,
    }
    report = {**unsigned, "evidence_sha256": _canonical_sha256(unsigned)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
