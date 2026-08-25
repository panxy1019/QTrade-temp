"""Run the one-shot, preregistered BaoStock reserved-tail diagnostic."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from qtrade.research import MinuteBacktestResult, run_minute_overlay_backtest
from qtrade.runtime import MinuteOverlayPolicy

try:
    from scripts.run_sina_five_minute_walk_forward import (
        FIVE_MINUTE_POLICIES,
        _selected_curve,
    )
    from scripts.run_sina_minute_overlay_backtest import (
        _canonical_sha256,
        _load_dataset,
        _metrics,
        _stock_sector_groups,
        _write_json,
    )
except ModuleNotFoundError:
    from run_sina_five_minute_walk_forward import (  # type: ignore[no-redef]
        FIVE_MINUTE_POLICIES,
        _selected_curve,
    )
    from run_sina_minute_overlay_backtest import (  # type: ignore[no-redef]
        _canonical_sha256,
        _load_dataset,
        _metrics,
        _stock_sector_groups,
        _write_json,
    )

DEFAULT_PREREGISTRATION = Path(
    "docs/preregistrations/baostock-reserved-tail-high-exposure-v1.json"
)
DEFAULT_DATASET_ROOT = Path("runtime/data/baostock-five-minute")
DEFAULT_OUTPUT = Path("runtime/research/baostock-reserved-tail-high-exposure-v1")
EXPECTED_CLASSIFICATION = "free_historical_qfq_5m_current_non_pit_not_eligible"


def _load_preregistration(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("reserved-tail preregistration must be an object")
    digest = value.get("preregistration_sha256")
    unsigned = {key: item for key, item in value.items() if key != "preregistration_sha256"}
    if (
        value.get("schema_version") != 1
        or value.get("status") != "LOCKED_BEFORE_RESERVED_TAIL_ENGINE_READ"
        or not isinstance(digest, str)
        or _canonical_sha256(unsigned) != digest
    ):
        raise ValueError("reserved-tail preregistration signature is invalid")
    evaluation = value.get("evaluation_lock")
    interpretation = value.get("interpretation_lock")
    warmup = value.get("warmup_contract")
    if (
        not isinstance(evaluation, dict)
        or evaluation.get("selection_after_tail_read") is not False
        or evaluation.get("parameter_changes_after_tail_read") is not False
        or not isinstance(interpretation, dict)
        or interpretation.get("promotion") != "NOT_ELIGIBLE"
        or interpretation.get("no_second_tail_tuning_iteration") is not True
        or not isinstance(warmup, dict)
        or warmup.get("candidate_and_comparator_receive_identical_warmup") is not True
        or warmup.get("warmup_returns_enter_tail_metrics") is not False
        or warmup.get("reserved_tail_enters_parameter_selection") is not False
    ):
        raise ValueError("reserved-tail preregistration lock is incomplete")
    return value


def _candidate_policy(preregistration: dict[str, object]) -> tuple[str, str, MinuteOverlayPolicy]:
    candidate = preregistration.get("candidate")
    if not isinstance(candidate, dict) or not isinstance(candidate.get("parameters"), dict):
        raise ValueError("reserved-tail candidate is malformed")
    parameters = candidate["parameters"]
    policy_id = str(candidate["policy_id"])
    policy_hash = str(candidate["policy_hash"])
    policy = MinuteOverlayPolicy(
        short_window=int(parameters["short_window"]),
        long_window=int(parameters["long_window"]),
        volatility_window=int(parameters["volatility_window"]),
        maximum_minute_volatility=Decimal(str(parameters["maximum_minute_volatility"])),
        maximum_intraday_drawdown=Decimal(str(parameters["maximum_intraday_drawdown"])),
        reduction_multiplier=Decimal(str(parameters["reduction_multiplier"])),
        cooldown_bars=int(parameters["cooldown_bars"]),
    )
    return policy_id, policy_hash, policy


def _gate_rows(
    *,
    candidate_five: dict[str, object],
    comparator_five: dict[str, object],
    candidate_zero: dict[str, object],
    sector_wins: int,
    sector_count: int,
) -> list[dict[str, object]]:
    candidate_overlay = candidate_five["overlay"]
    comparator_overlay = comparator_five["overlay"]
    baseline = candidate_five["base"]
    candidate_execution = candidate_five["execution"]
    comparator_execution = comparator_five["execution"]
    assert isinstance(candidate_overlay, dict)
    assert isinstance(comparator_overlay, dict)
    assert isinstance(baseline, dict)
    assert isinstance(candidate_execution, dict)
    assert isinstance(comparator_execution, dict)
    values = (
        (
            "CANDIDATE_BEATS_COMPARATOR_AT_5_BPS",
            float(candidate_overlay["total_return"])
            > float(comparator_overlay["total_return"]),
        ),
        (
            "CANDIDATE_DRAWDOWN_NOT_WORSE_THAN_BASELINE_AT_5_BPS",
            float(candidate_overlay["maximum_drawdown"])
            >= float(baseline["maximum_drawdown"]),
        ),
        (
            "MEAN_EXPOSURE_AT_LEAST_85_PERCENT",
            float(candidate_execution["mean_exposure"]) >= 0.85,
        ),
        (
            "TURNOVER_AT_MOST_HALF_COMPARATOR",
            float(candidate_execution["mean_turnover_per_symbol"])
            <= 0.5 * float(comparator_execution["mean_turnover_per_symbol"]),
        ),
        (
            "POSITIVE_AT_ZERO_AND_FIVE_BPS",
            float(candidate_zero["overlay"]["total_return"]) > 0
            and float(candidate_overlay["total_return"]) > 0,
        ),
        ("LEAVE_ONE_SECTOR_OUT_MAJORITY", sector_wins >= 12 and sector_count == 15),
    )
    return [{"gate_id": gate_id, "passed": passed} for gate_id, passed in values]


def _tail_curves(
    result: MinuteBacktestResult, tail_days: set[date]
) -> tuple[tuple[datetime, ...], tuple[float, ...], tuple[float, ...]]:
    timestamps, base = _selected_curve(result, kind="base", days=tail_days)
    overlay_timestamps, overlay = _selected_curve(
        result, kind="overlay", days=tail_days
    )
    if timestamps != overlay_timestamps:
        raise ValueError("reserved-tail curves are not aligned")
    return timestamps, base, overlay


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preregistration", type=Path, default=DEFAULT_PREREGISTRATION)
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite reserved-tail result: {args.output}")

    preregistration = _load_preregistration(args.preregistration)
    dataset_lock = preregistration["dataset"]
    evaluation = preregistration["evaluation_lock"]
    comparator_lock = preregistration["comparator"]
    assert isinstance(dataset_lock, dict)
    assert isinstance(evaluation, dict)
    assert isinstance(comparator_lock, dict)
    data_version = str(dataset_lock["data_version"])
    evidence, manifest, bars = _load_dataset(
        args.dataset_root,
        data_version,
        expected_classification=EXPECTED_CLASSIFICATION,
        expected_interval="5m",
    )
    tail_day_strings = evaluation["reserved_tail_days"]
    if (
        manifest.checksum != dataset_lock["checksum"]
        or _canonical_sha256(evidence) != dataset_lock["evidence_sha256"]
        or evidence["trading_days"][-8:] != tail_day_strings
        or len(bars) != 1_324_800
    ):
        raise ValueError("reserved-tail dataset identity changed after preregistration")
    tail_days = {date.fromisoformat(value) for value in tail_day_strings}
    candidate_id, candidate_hash, candidate_policy = _candidate_policy(preregistration)
    comparator_id = str(comparator_lock["policy_id"])
    comparator_policy = dict(FIVE_MINUTE_POLICIES)[comparator_id]
    costs = [float(value) for value in evaluation["cost_bps"]]
    cost_rows: list[dict[str, object]] = []
    candidate_five_result: MinuteBacktestResult | None = None
    comparator_five_result: MinuteBacktestResult | None = None
    for cost_bps in costs:
        candidate_result = run_minute_overlay_backtest(
            bars,
            policy_id=candidate_id,
            policy=candidate_policy,
            cost_bps=cost_bps,
        )
        comparator_result = run_minute_overlay_backtest(
            bars,
            policy_id=comparator_id,
            policy=comparator_policy,
            cost_bps=cost_bps,
        )
        if (
            candidate_result.policy_hash != candidate_hash
            or comparator_result.policy_hash != comparator_lock["policy_hash"]
        ):
            raise ValueError("reserved-tail policy identity changed after preregistration")
        candidate_metrics = candidate_result.metrics(days=tail_days)
        comparator_metrics = comparator_result.metrics(days=tail_days)
        if candidate_metrics["base"] != comparator_metrics["base"]:
            raise ValueError("reserved-tail policy baselines differ")
        cost_rows.append(
            {
                "cost_bps": cost_bps,
                "base": _metrics(candidate_metrics["base"]),
                "candidate": _metrics(candidate_metrics["overlay"]),
                "comparator": _metrics(comparator_metrics["overlay"]),
                "candidate_execution": candidate_result.execution_metrics(days=tail_days),
                "comparator_execution": comparator_result.execution_metrics(days=tail_days),
            }
        )
        if cost_bps == 5:
            candidate_five_result = candidate_result
            comparator_five_result = comparator_result
    if candidate_five_result is None or comparator_five_result is None:
        raise ValueError("reserved-tail diagnostic requires the preregistered 5 bps case")

    all_symbols = set(candidate_five_result.symbols)
    sector_rows = []
    for sector_code, sector_name, excluded in _stock_sector_groups(evidence, all_symbols):
        retained = all_symbols - set(excluded)
        candidate = candidate_five_result.metrics(days=tail_days, symbols=retained)
        comparator = comparator_five_result.metrics(days=tail_days, symbols=retained)
        sector_rows.append(
            {
                "excluded_sector_code": sector_code,
                "excluded_sector_name": sector_name,
                "excluded_symbol_count": len(excluded),
                "retained_symbol_count": len(retained),
                "candidate": _metrics(candidate["overlay"]),
                "comparator": _metrics(comparator["overlay"]),
                "candidate_beats_comparator": (
                    candidate["overlay"].total_return
                    > comparator["overlay"].total_return
                ),
            }
        )

    daily_rows = []
    for trading_day in sorted(tail_days):
        candidate = candidate_five_result.metrics(days={trading_day})
        comparator = comparator_five_result.metrics(days={trading_day})
        daily_rows.append(
            {
                "trading_day": trading_day.isoformat(),
                "base": _metrics(candidate["base"]),
                "candidate": _metrics(candidate["overlay"]),
                "comparator": _metrics(comparator["overlay"]),
            }
        )

    timestamps, base_curve, candidate_curve = _tail_curves(
        candidate_five_result, tail_days
    )
    comparator_timestamps, _, comparator_curve = _tail_curves(
        comparator_five_result, tail_days
    )
    if timestamps != comparator_timestamps:
        raise ValueError("reserved-tail comparator timestamps differ")
    curve_rows = [
        {
            "timestamp": timestamp.isoformat(),
            "base_equity": base,
            "candidate_equity": candidate,
            "comparator_equity": comparator,
        }
        for timestamp, base, candidate, comparator in zip(
            timestamps, base_curve, candidate_curve, comparator_curve, strict=True
        )
    ]

    candidate_five = next(row for row in cost_rows if row["cost_bps"] == 5)
    candidate_zero = next(row for row in cost_rows if row["cost_bps"] == 0)
    gate_rows = _gate_rows(
        candidate_five={
            "base": candidate_five["base"],
            "overlay": candidate_five["candidate"],
            "execution": candidate_five["candidate_execution"],
        },
        comparator_five={
            "overlay": candidate_five["comparator"],
            "execution": candidate_five["comparator_execution"],
        },
        candidate_zero={"overlay": candidate_zero["candidate"]},
        sector_wins=sum(bool(row["candidate_beats_comparator"]) for row in sector_rows),
        sector_count=len(sector_rows),
    )
    args.output.mkdir(parents=True)
    artifacts = {
        "cost_stress.json": _write_json(args.output / "cost_stress.json", cost_rows),
        "daily.json": _write_json(args.output / "daily.json", daily_rows),
        "leave_one_sector_out.json": _write_json(
            args.output / "leave_one_sector_out.json", sector_rows
        ),
        "curve.json": _write_json(args.output / "curve.json", curve_rows),
    }
    report = {
        "schema_version": 1,
        "report_id": "baostock-reserved-tail-high-exposure-v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "classification": "PREREGISTERED_PARTIALLY_OBSERVED_RESERVED_TAIL_DIAGNOSTIC",
        "preregistration_sha256": preregistration["preregistration_sha256"],
        "dataset": dataset_lock,
        "warmup_session_count": len(evidence["trading_days"]) - len(tail_days),
        "test_session_count": len(tail_days),
        "processed_bar_count": len(bars),
        "test_days": tail_day_strings,
        "candidate": {"policy_id": candidate_id, "policy_hash": candidate_hash},
        "comparator": comparator_lock,
        "five_bps": candidate_five,
        "gates": gate_rows,
        "all_required_gates_passed": all(bool(row["passed"]) for row in gate_rows),
        "sector_win_count": sum(
            bool(row["candidate_beats_comparator"]) for row in sector_rows
        ),
        "artifacts": artifacts,
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": [
            "ONLY_EIGHT_RESERVED_SESSIONS",
            "TAIL_PRICES_PARTIALLY_OBSERVED_DURING_FRONTEND_QA",
            "CURRENT_NON_POINT_IN_TIME_UNIVERSE",
            "QFQ_MAY_USE_FUTURE_ADJUSTMENT_KNOWLEDGE",
            "NO_PAPER_OR_SHADOW_SESSION_EVIDENCE",
        ],
        "external_order_transmission": False,
    }
    signed = {**report, "evidence_sha256": _canonical_sha256(report)}
    _write_json(args.output / "report.json", signed)
    print(json.dumps(signed, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
