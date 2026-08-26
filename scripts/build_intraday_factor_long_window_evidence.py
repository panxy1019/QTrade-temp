"""Build one immutable 120-session intraday-factor robustness report."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from qtrade.api.app import (
    IntradayFactorBacktestRequest,
    _intraday_factor_preview,
    _load_intraday_factor_bars,
    _load_verified_minute_dataset,
)
from qtrade.market_data import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.research import (
    OpeningTripleBullishSpec,
    run_opening_triple_bullish_research,
)
from scripts.atomic_json import write_json_immutable

SHANGHAI = ZoneInfo("Asia/Shanghai")
REPORT_ID = "opening-triple-bullish-long-window-robustness-v1"
CLASSIFICATION = "SIGNED_REUSED_LONG_WINDOW_INTRADAY_FACTOR_DIAGNOSTIC"
EXPECTED_SESSION_COUNT = 368
SELECTED_SESSION_COUNT = 120


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


def _module_sha256(module_name: str) -> str:
    module = import_module(module_name)
    module_path = getattr(module, "__file__", None)
    if not isinstance(module_path, str):
        raise RuntimeError(f"module source unavailable: {module_name}")
    return sha256(Path(module_path).read_bytes()).hexdigest()


def build_report(
    manifest_path: Path,
    *,
    generated_at: datetime | None = None,
) -> dict[str, object]:
    observed_at = generated_at or datetime.now(SHANGHAI)
    if observed_at.tzinfo is None:
        raise ValueError("report generation time must be timezone-aware")
    dataset = _load_verified_minute_dataset(
        manifest_path,
        expected_classification=(
            "free_historical_qfq_5m_current_non_pit_not_eligible"
        ),
        expected_interval="5m",
        expected_session_count=EXPECTED_SESSION_COUNT,
        expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
    )
    if dataset is None:
        raise ValueError("verified long five-minute dataset unavailable")
    trading_days = cast(list[str], dataset["trading_days"])
    if len(trading_days) != EXPECTED_SESSION_COUNT:
        raise ValueError("long-window trading-day identity is inconsistent")
    selected_days = trading_days[-(SELECTED_SESSION_COUNT + 1) : -1]
    required_days = trading_days[-(SELECTED_SESSION_COUNT + 1) :]
    if len(selected_days) != SELECTED_SESSION_COUNT or len(required_days) != 121:
        raise ValueError("long-window factor range is incomplete")
    instruments = cast(list[dict[str, object]], dataset["instruments"])
    stocks = [row for row in instruments if row.get("instrument_type") == "STOCK"]
    sectors = {
        cast(str, row["symbol"]): (
            cast(str, row["sector_code"]),
            cast(str, row["sector_name"]),
        )
        for row in stocks
    }
    if len(sectors) != 75 or len(set(sectors.values())) != 15:
        raise ValueError("long-window factor universe must remain 75 stocks / 15 sectors")
    bars = _load_intraday_factor_bars(
        manifest_path,
        trading_days=required_days,
        symbols=sorted(sectors),
        data_version=str(dataset["data_version"]),
        expected_checksum=str(dataset["checksum"]),
        expected_bar_count=cast(int, dataset["bar_count"]),
        expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
    )
    if bars is None:
        raise ValueError("verified long-window factor source bars failed validation")
    payload = IntradayFactorBacktestRequest(
        factor="opening_three_ten_minute_bullish",
        start=selected_days[0],
        end=selected_days[-1],
        cost_bps="5",
        minimum_body_return_bps="0",
    )
    result = run_opening_triple_bullish_research(
        bars,
        sectors=sectors,
        start=payload.start,
        end=payload.end,
        spec=OpeningTripleBullishSpec(
            cost_bps=payload.cost_bps,
            minimum_body_return_bps=payload.minimum_body_return_bps,
        ),
    )
    preview = _intraday_factor_preview(
        payload=payload,
        result=result,
        dataset=dataset,
        required_trading_days=required_days,
        source_bar_count=len(bars),
    )
    preview_backtest = cast(dict[str, object], preview["backtest"])
    preview_robustness = cast(
        dict[str, object], preview["robustness_validation"]
    )
    robustness = result.robustness_validation
    summary: dict[str, object] = {
        "observation_count": result.event_summary.observation_count,
        "signal_count": result.event_summary.signal_count,
        "rank_ic": str(result.factor_metrics.rank_ic),
        "full_window_strategy_metrics": preview_backtest["strategy_metrics"],
        "fold_count": len(robustness.folds),
        "positive_fold_count": sum(
            fold.test_strategy_metrics.total_return > 0
            for fold in robustness.folds
        ),
        "selected_thresholds_bps": [
            str(fold.selected_minimum_body_return_bps)
            for fold in robustness.folds
        ],
        "stitched_strategy_metrics": preview_robustness[
            "stitched_strategy_metrics"
        ],
        "threshold_sensitivity": [
            {
                "minimum_body_return_bps": str(score.minimum_body_return_bps),
                "signal_count": score.signal_count,
                "total_return": str(score.strategy_metrics.total_return),
                "excess_return": str(score.strategy_metrics.excess_return),
            }
            for score in robustness.threshold_sensitivity
        ],
        "robustness_passed": robustness.passed,
        "research_gate_passed": result.research_gate.passed,
    }
    unsigned: dict[str, object] = {
        "schema_version": 1,
        "report_id": REPORT_ID,
        "classification": CLASSIFICATION,
        "generated_at": observed_at.astimezone(SHANGHAI).isoformat(),
        "implementation": {
            "factor_module_sha256": _module_sha256(
                "qtrade.research.intraday_factor"
            ),
            "api_module_sha256": _module_sha256("qtrade.api.app"),
        },
        "dataset": {
            "data_version": dataset["data_version"],
            "checksum": dataset["checksum"],
            "evidence_sha256": dataset["evidence_sha256"],
            "bar_interval": dataset["bar_interval"],
            "session_count": dataset["session_count"],
            "symbol_count": dataset["symbol_count"],
            "sector_count": dataset["sector_count"],
            "point_in_time": dataset["point_in_time"],
            "future_adjustment_knowledge_possible": dataset[
                "future_adjustment_knowledge_possible"
            ],
        },
        "evaluation_contract": {
            "selected_session_count": SELECTED_SESSION_COUNT,
            "selected_start": selected_days[0],
            "selected_end": selected_days[-1],
            "exit_completion_day": required_days[-1],
            "candidate_thresholds_bps": ["0", "5", "10", "20"],
            "initial_training_session_count": 60,
            "fold_count": 6,
            "test_sessions_per_fold": 10,
            "test_returns_enter_same_fold_selection": False,
            "reused_history_not_independent_confirmation": True,
        },
        "parameters": preview["parameters"],
        "result_preview_hash": preview["preview_hash"],
        "summary": summary,
        "result": preview,
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": [
            "REUSED_HISTORICAL_WINDOW_NOT_INDEPENDENT_CONFIRMATION",
            "CURRENT_UNIVERSE_NON_PIT",
            "QFQ_MAY_CONTAIN_FUTURE_ADJUSTMENT_KNOWLEDGE",
            "NO_AUTHORIZED_FORWARD_PAPER_EVIDENCE",
            "NO_AUTHORIZED_REALTIME_OR_L2_PROVIDER",
        ],
        "paper_readiness": "NOT_ELIGIBLE",
        "live_readiness": "DISABLED_REQUIRES_SEPARATE_AUTHORIZATION",
        "state_mutation": False,
        "external_order_transmission": False,
    }
    return {**unsigned, "evidence_sha256": _canonical_sha256(unsigned)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(
            "deployment/evidence/long-five-minute-market-data/manifest.json"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "deployment/evidence/intraday-factor-long-window/report.json"
        ),
    )
    args = parser.parse_args()
    report = build_report(args.manifest)
    write_json_immutable(args.output, report)
    print(
        json.dumps(
            {
                "status": "WRITTEN_IMMUTABLE",
                "report_id": report["report_id"],
                "result_preview_hash": report["result_preview_hash"],
                "evidence_sha256": report["evidence_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
