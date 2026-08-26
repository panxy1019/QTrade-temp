"""Score the frozen overlay on immutable complete future BaoStock sessions."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from qtrade.data import DataManifest, ParquetBarStore, QualityStatus
from qtrade.research import run_minute_overlay_backtest
from qtrade.runtime import MinuteOverlayPolicy
from scripts.prepare_baostock_forward_preregistration import (
    _canonical_sha256,
    _load_signed,
)
from scripts.run_sina_minute_overlay_backtest import _load_dataset, _metrics

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _policy(value: dict[str, Any]) -> MinuteOverlayPolicy:
    return MinuteOverlayPolicy(
        short_window=int(value["short_window"]),
        long_window=int(value["long_window"]),
        volatility_window=int(value["volatility_window"]),
        maximum_minute_volatility=Decimal(str(value["maximum_minute_volatility"])),
        maximum_intraday_drawdown=Decimal(str(value["maximum_intraday_drawdown"])),
        reduction_multiplier=Decimal(str(value["reduction_multiplier"])),
        cooldown_bars=int(value["cooldown_bars"]),
    )


def _load_forward_sessions(
    root: Path,
    *,
    preregistration: dict[str, Any],
) -> tuple[list[Any], list[dict[str, Any]]]:
    reports: list[dict[str, Any]] = []
    bars: list[Any] = []
    if not root.exists():
        return bars, reports
    for path in sorted(root.glob("*/manifest.json")):
        report = _load_signed(path, "evidence_sha256")
        manifest_value = report.get("manifest")
        if (
            report.get("report_id") != "baostock-complete-forward-session-v1"
            or report.get("preregistration_sha256")
            != preregistration["preregistration_sha256"]
            or report.get("complete_session") is not True
            or report.get("partial_session") is not False
            or report.get("symbol_count") != 75
            or report.get("sector_count") != 15
            or report.get("bars_per_symbol") != 48
            or report.get("bar_count") != 3600
            or report.get("anchor_exact_match") is not True
            or not isinstance(manifest_value, dict)
        ):
            raise ValueError(f"forward session identity is invalid: {path}")
        manifest = DataManifest(
            data_version=str(manifest_value["data_version"]),
            source=str(manifest_value["source"]),
            schema_version=str(manifest_value["schema_version"]),
            ingestion_time=datetime.fromisoformat(str(manifest_value["ingestion_time"])),
            checksum=str(manifest_value["checksum"]),
            quality_status=QualityStatus(str(manifest_value["quality_status"])),
        )
        loaded = ParquetBarStore(root).read(manifest)
        target_day = date.fromisoformat(str(report["target_day"]))
        if len(loaded) != 3600 or {bar.trading_day for bar in loaded} != {target_day}:
            raise ValueError(f"forward session parquet is inconsistent: {path}")
        reports.append(report)
        bars.extend(loaded)
    days = [str(report["target_day"]) for report in reports]
    first_day = cast(dict[str, Any], preregistration["collection_lock"])[
        "first_eligible_trading_day"
    ]
    if days != sorted(set(days)) or any(day < first_day for day in days):
        raise ValueError("forward session sequence is not unique and chronological")
    return bars, reports


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--preregistration",
        type=Path,
        default=Path(
            "docs/preregistrations/baostock-locked-forward-observation-v1.json"
        ),
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=Path("runtime/data/baostock-five-minute"),
    )
    parser.add_argument(
        "--forward-root",
        type=Path,
        default=Path("runtime/data/baostock-forward-sessions"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite forward observation: {args.output}")
    preregistration = _load_signed(
        args.preregistration, "preregistration_sha256"
    )
    warmup = cast(dict[str, Any], preregistration["warmup_dataset"])
    _, base_manifest, warmup_bars = _load_dataset(
        args.dataset_root,
        str(warmup["data_version"]),
        expected_classification="free_historical_qfq_5m_current_non_pit_not_eligible",
        expected_interval="5m",
    )
    if base_manifest.checksum != warmup["checksum"]:
        raise ValueError("forward warmup checksum is inconsistent")
    forward_bars, session_reports = _load_forward_sessions(
        args.forward_root, preregistration=preregistration
    )
    lock = cast(dict[str, Any], preregistration["development_evidence"])
    policy = _policy(cast(dict[str, Any], lock["parameters"]))
    minimum = int(cast(dict[str, Any], preregistration["collection_lock"])[
        "minimum_complete_future_sessions"
    ])
    evaluation_reports = session_reports[:minimum]
    session_days = tuple(
        date.fromisoformat(str(row["target_day"])) for row in evaluation_reports
    )
    cost_rows: list[dict[str, Any]] = []
    sector_rows: list[dict[str, Any]] = []
    gates: list[dict[str, Any]] = []
    if session_days:
        evaluation_days = set(session_days)
        all_bars = [
            *warmup_bars,
            *(bar for bar in forward_bars if bar.trading_day in evaluation_days),
        ]
        results = {
            cost: run_minute_overlay_backtest(
                all_bars,
                policy_id=str(lock["policy_id"]),
                policy=policy,
                cost_bps=float(cost),
            )
            for cost in (0, 5)
        }
        for cost, result in results.items():
            metrics = result.metrics(days=set(session_days))
            cost_rows.append(
                {
                    "cost_bps": cost,
                    "base": _metrics(metrics["base"]),
                    "overlay": _metrics(metrics["overlay"]),
                    "excess_return": (
                        metrics["overlay"].total_return
                        - metrics["base"].total_return
                    ),
                    "execution": result.execution_metrics(days=set(session_days)),
                }
            )
        five = next(row for row in cost_rows if row["cost_bps"] == 5)
        universe = cast(dict[str, Any], _load_signed(
            args.dataset_root / str(warmup["data_version"]) / "manifest.json",
            "evidence_sha256",
        )["universe"])
        stocks = cast(list[dict[str, Any]], universe["stocks"])
        all_symbols = {str(row["symbol"]) for row in stocks}
        five_result = results[5]
        for sector in cast(list[dict[str, Any]], universe["sectors"]):
            excluded = {
                str(row["symbol"])
                for row in stocks
                if row["sector_code"] == sector["sector_code"]
            }
            metrics = five_result.metrics(
                days=set(session_days), symbols=all_symbols - excluded
            )
            sector_rows.append(
                {
                    "excluded_sector_code": sector["sector_code"],
                    "excluded_sector_name": sector["sector_name"],
                    "base": _metrics(metrics["base"]),
                    "overlay": _metrics(metrics["overlay"]),
                    "excess_return": (
                        metrics["overlay"].total_return
                        - metrics["base"].total_return
                    ),
                }
            )
        base = cast(dict[str, Any], five["base"])
        overlay = cast(dict[str, Any], five["overlay"])
        execution = cast(dict[str, Any], five["execution"])
        gate_values = {
            "OVERLAY_DRAWDOWN_NOT_WORSE_AT_5_BPS": (
                float(overlay["maximum_drawdown"])
                >= float(base["maximum_drawdown"])
            ),
            "RETURN_SHORTFALL_WITHIN_50_BPS_AT_5_BPS": (
                float(five["excess_return"]) >= -0.005
            ),
            "MEAN_EXPOSURE_AT_LEAST_90_PERCENT": (
                float(execution["mean_exposure"]) >= 0.90
            ),
            "MEAN_TURNOVER_PER_SYMBOL_AT_MOST_FIVE": (
                float(execution["mean_turnover_per_symbol"]) <= 5
            ),
            "LEAVE_ONE_SECTOR_OUT_STABILITY": (
                sum(float(row["excess_return"]) >= -0.01 for row in sector_rows)
                >= 12
            ),
        }
        gates = [
            {"gate_id": gate_id, "passed": passed}
            for gate_id, passed in gate_values.items()
        ]
    complete = len(session_reports) >= minimum
    all_passed = complete and bool(gates) and all(row["passed"] for row in gates)
    unsigned: dict[str, Any] = {
        "schema_version": 1,
        "report_id": "baostock-locked-forward-observation-v1",
        "classification": "LOCKED_FUTURE_COMPLETE_SESSION_OBSERVATION",
        "generated_at": datetime.now(SHANGHAI).isoformat(),
        "preregistration_sha256": preregistration["preregistration_sha256"],
        "policy_id": lock["policy_id"],
        "policy_hash": lock["policy_hash"],
        "policy": {key: str(value) for key, value in asdict(policy).items()},
        "warmup_dataset": {
            "data_version": base_manifest.data_version,
            "checksum": base_manifest.checksum,
            "session_count": warmup["session_count"],
            "warmup_returns_enter_metrics": False,
        },
        "future_session_count": len(session_reports),
        "accepted_future_session_count": len(session_reports),
        "evaluated_future_session_count": len(session_days),
        "minimum_complete_future_sessions": minimum,
        "future_sessions": [
            {
                "target_day": row["target_day"],
                "evidence_sha256": row["evidence_sha256"],
                "bar_count": row["bar_count"],
                "anchor_exact_match": row["anchor_exact_match"],
            }
            for row in session_reports
        ],
        "observation_status": "COMPLETE" if complete else "INCOMPLETE",
        "all_required_gates_passed": all_passed,
        "gates": gates,
        "cost_stress": cost_rows,
        "leave_one_sector_out": sector_rows,
        "causal_contract": preregistration["causal_contract"],
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": [
            *([] if complete else ["FUTURE_TWENTY_SESSION_OBSERVATION_INCOMPLETE"]),
            *(
                ["PREDECLARED_FORWARD_GATES_FAILED"]
                if complete and not all_passed
                else []
            ),
            "CURRENT_NON_POINT_IN_TIME_UNIVERSE",
            "QFQ_MAY_USE_FUTURE_ADJUSTMENT_KNOWLEDGE",
            "NO_PAPER_OR_SHADOW_SESSION_EVIDENCE",
        ],
        "realtime_readiness": False,
        "real_data_paper_readiness": False,
        "external_order_transmission": False,
    }
    report = {**unsigned, "evidence_sha256": _canonical_sha256(unsigned)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": report["observation_status"],
                "future_session_count": report["future_session_count"],
                "evidence_sha256": report["evidence_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
