"""Tune on three sessions and evaluate a causal minute overlay on two held-out sessions."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from statistics import mean
from typing import Any

from qtrade.data import DataManifest, ParquetBarStore, QualityStatus
from qtrade.research import MinuteCurveMetrics, run_minute_overlay_backtest
from qtrade.runtime import MinuteOverlayPolicy

POLICIES: tuple[tuple[str, MinuteOverlayPolicy], ...] = (
    (
        "balanced-v1",
        MinuteOverlayPolicy(5, 20, 20, Decimal("0.0025"), Decimal("0.02"), Decimal("0.5"), 10),
    ),
    (
        "fast-v1",
        MinuteOverlayPolicy(3, 12, 12, Decimal("0.0025"), Decimal("0.02"), Decimal("0.5"), 6),
    ),
    (
        "slow-v1",
        MinuteOverlayPolicy(10, 30, 30, Decimal("0.0025"), Decimal("0.02"), Decimal("0.5"), 15),
    ),
    (
        "vol-tight-v1",
        MinuteOverlayPolicy(5, 20, 20, Decimal("0.0015"), Decimal("0.02"), Decimal("0.5"), 10),
    ),
    (
        "drawdown-tight-v1",
        MinuteOverlayPolicy(5, 20, 20, Decimal("0.0025"), Decimal("0.01"), Decimal("0.5"), 15),
    ),
    (
        "reduction-hard-v1",
        MinuteOverlayPolicy(5, 20, 20, Decimal("0.0025"), Decimal("0.02"), Decimal("0.25"), 10),
    ),
    (
        "reduction-soft-v1",
        MinuteOverlayPolicy(5, 20, 20, Decimal("0.0025"), Decimal("0.02"), Decimal("0.75"), 10),
    ),
)


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


def _load_dataset(
    root: Path,
    version: str,
    *,
    expected_classification: str = "noncommercial_recent_historical_1m_non_pit_not_eligible",
    expected_interval: str | None = None,
) -> tuple[dict[str, Any], DataManifest, list[Any]]:
    path = root / version / "manifest.json"
    signed = json.loads(path.read_text(encoding="utf-8"))
    digest = signed.get("evidence_sha256")
    unsigned = {key: value for key, value in signed.items() if key != "evidence_sha256"}
    if not isinstance(digest, str) or _canonical_sha256(unsigned) != digest:
        raise ValueError("minute dataset evidence signature mismatch")
    value = unsigned.get("manifest")
    if not isinstance(value, dict):
        raise ValueError("minute dataset manifest is malformed")
    manifest = DataManifest(
        data_version=str(value["data_version"]),
        source=str(value["source"]),
        schema_version=str(value["schema_version"]),
        ingestion_time=datetime.fromisoformat(str(value["ingestion_time"])),
        checksum=str(value["checksum"]),
        quality_status=QualityStatus(str(value["quality_status"])),
    )
    if (
        manifest.data_version != version
        or manifest.quality_status is not QualityStatus.PASSED
        or unsigned.get("classification") != expected_classification
        or expected_interval is not None
        and unsigned.get("bar_interval") != expected_interval
    ):
        raise ValueError("minute dataset identity is not eligible for this diagnostic")
    bars = ParquetBarStore(root).read(manifest)
    return unsigned, manifest, bars


def _metrics(value: MinuteCurveMetrics) -> dict[str, object]:
    return asdict(value)


def _policy(value: MinuteOverlayPolicy) -> dict[str, object]:
    return {key: str(item) for key, item in asdict(value).items()}


def _write_json(path: Path, value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True, default=str
    ).encode("utf-8")
    path.write_bytes(encoded)
    return sha256(encoded).hexdigest()


def _stock_sector_groups(
    evidence: dict[str, Any], available_symbols: set[str]
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    grouped: dict[str, dict[str, object]] = {}
    for stock in evidence["universe"]["stocks"]:
        code = str(stock["sector_code"])
        name = str(stock["sector_name"])
        symbol = str(stock["symbol"])
        if symbol not in available_symbols:
            raise ValueError(f"minute stock is absent from synchronized bars: {symbol}")
        value = grouped.setdefault(code, {"name": name, "symbols": []})
        if value["name"] != name:
            raise ValueError(f"minute sector name is inconsistent: {code}")
        symbols = value["symbols"]
        assert isinstance(symbols, list)
        symbols.append(symbol)
    return tuple(
        (code, str(value["name"]), tuple(value["symbols"]))
        for code, value in sorted(grouped.items())
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset-root", type=Path, default=Path("runtime/data/sina-minute")
    )
    parser.add_argument("--data-version", required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("runtime/research/sina-minute-overlay-v1")
    )
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite minute report: {args.output}")

    evidence, manifest, bars = _load_dataset(args.dataset_root, args.data_version)
    trading_days = tuple(date.fromisoformat(value) for value in evidence["trading_days"])
    if len(trading_days) != 5:
        raise ValueError("minute walk-forward diagnostic requires exactly five sessions")
    train_days = set(trading_days[:3])
    test_days = set(trading_days[3:])
    candidate_rows: list[dict[str, object]] = []
    results = {}
    for policy_id, policy in POLICIES:
        result = run_minute_overlay_backtest(
            bars, policy_id=policy_id, policy=policy, cost_bps=5
        )
        metrics = result.metrics(days=train_days)
        base = metrics["base"]
        overlay = metrics["overlay"]
        score = (
            overlay.total_return
            - base.total_return
            + 2 * (abs(base.maximum_drawdown) - abs(overlay.maximum_drawdown))
        )
        candidate_rows.append(
            {
                "policy_id": policy_id,
                "policy_hash": result.policy_hash,
                "policy": _policy(policy),
                "selection_score": score,
                "train_base": _metrics(base),
                "train_overlay": _metrics(overlay),
            }
        )
        results[policy_id] = result
    candidate_rows.sort(
        key=lambda row: (-float(str(row["selection_score"])), str(row["policy_id"]))
    )
    selected_id = str(candidate_rows[0]["policy_id"])
    selected_policy = dict(POLICIES)[selected_id]
    selected = results[selected_id]
    test_metrics = selected.metrics(days=test_days)

    cost_rows = []
    for cost_bps in (0.0, 5.0, 10.0, 20.0):
        result = (
            selected
            if cost_bps == 5
            else run_minute_overlay_backtest(
                bars,
                policy_id=selected_id,
                policy=selected_policy,
                cost_bps=cost_bps,
            )
        )
        cost_metrics = result.metrics(days=test_days)
        cost_rows.append(
            {
                "cost_bps": cost_bps,
                "base": _metrics(cost_metrics["base"]),
                "overlay": _metrics(cost_metrics["overlay"]),
                "mean_turnover": mean(result.turnover_by_symbol.values()),
                "mean_transaction_cost": mean(
                    result.transaction_cost_by_symbol.values()
                ),
            }
        )

    requested_etfs = set(evidence["universe"]["etfs"])
    etfs = set(selected.symbols) & requested_etfs
    stocks = set(selected.symbols) - etfs
    subgroup_rows = []
    for name, subset in (("ETF", etfs), ("STOCK", stocks)):
        subgroup_metrics = selected.metrics(days=test_days, symbols=subset)
        subgroup_rows.append(
            {
                "group": name,
                "symbol_count": len(subset),
                "base": _metrics(subgroup_metrics["base"]),
                "overlay": _metrics(subgroup_metrics["overlay"]),
            }
        )

    all_test_overlay = test_metrics["overlay"].total_return
    sector_rows = []
    for sector_code, sector_name, excluded_symbols in _stock_sector_groups(
        evidence, set(selected.symbols)
    ):
        remaining = set(selected.symbols) - set(excluded_symbols)
        sector_metrics = selected.metrics(days=test_days, symbols=remaining)["overlay"]
        sector_rows.append(
            {
                "excluded_sector_code": sector_code,
                "excluded_sector_name": sector_name,
                "excluded_symbols": list(excluded_symbols),
                "excluded_symbol_count": len(excluded_symbols),
                "retained_symbol_count": len(remaining),
                "overlay": _metrics(sector_metrics),
                "return_delta_vs_all": sector_metrics.total_return - all_test_overlay,
            }
        )

    day_rows = []
    for trading_day in trading_days:
        daily_metrics = selected.metrics(days={trading_day})
        day_rows.append(
            {
                "trading_day": trading_day.isoformat(),
                "partition": "TRAIN" if trading_day in train_days else "TEST",
                "base": _metrics(daily_metrics["base"]),
                "overlay": _metrics(daily_metrics["overlay"]),
            }
        )

    args.output.mkdir(parents=True)
    artifact_hashes = {
        "candidates.json": _write_json(args.output / "candidates.json", candidate_rows),
        "cost_stress.json": _write_json(args.output / "cost_stress.json", cost_rows),
        "subgroups.json": _write_json(args.output / "subgroups.json", subgroup_rows),
        "leave_one_sector_out.json": _write_json(
            args.output / "leave_one_sector_out.json", sector_rows
        ),
        "daily.json": _write_json(args.output / "daily.json", day_rows),
        "curve.json": _write_json(
            args.output / "curve.json",
            [
                {
                    "timestamp": timestamp.isoformat(),
                    "base_equity": base,
                    "overlay_equity": overlay,
                }
                for timestamp, base, overlay in zip(
                    selected.timestamps,
                    selected.base_equity,
                    selected.overlay_equity,
                    strict=True,
                )
            ],
        ),
    }
    report = {
        "schema_version": 1,
        "report_id": "sina-minute-overlay-five-session-v2",
        "generated_at": datetime.now().astimezone().isoformat(),
        "dataset": {
            "data_version": manifest.data_version,
            "checksum": manifest.checksum,
            "evidence_sha256": evidence["evidence_sha256"]
            if "evidence_sha256" in evidence
            else _canonical_sha256(evidence),
            "bar_count": evidence["bar_count"],
            "symbol_count": evidence["symbol_count"],
            "etf_count": evidence["etf_count"],
            "stock_count": evidence["stock_count"],
            "sector_count": evidence["sector_count"],
            "classification": evidence["classification"],
        },
        "causal_contract": {
            "daily_target": "constant unit target known before each session",
            "signal_observed_at": "BAR_CLOSE_T",
            "exposure_change_at": "BAR_OPEN_T_PLUS_1",
            "same_bar_signal_execution": False,
            "maximum_exposure": selected.maximum_exposure,
            "external_order_transmission": False,
        },
        "split": {
            "train_days": [value.isoformat() for value in trading_days[:3]],
            "test_days": [value.isoformat() for value in trading_days[3:]],
            "candidate_count": len(POLICIES),
            "selection_objective": (
                "overlay_return_minus_base_return_plus_twice_drawdown_improvement"
            ),
        },
        "selected": {
            "policy_id": selected_id,
            "policy_hash": selected.policy_hash,
            "policy": _policy(selected_policy),
            "cost_bps": selected.cost_bps,
            "test_base": _metrics(test_metrics["base"]),
            "test_overlay": _metrics(test_metrics["overlay"]),
            "test_excess_return": (
                test_metrics["overlay"].total_return
                - test_metrics["base"].total_return
            ),
            "mean_turnover": mean(selected.turnover_by_symbol.values()),
            "mean_transaction_cost": mean(
                selected.transaction_cost_by_symbol.values()
            ),
            "reason_counts": selected.reason_counts,
        },
        "robustness": {
            "cost_scenarios": len(cost_rows),
            "subgroups": len(subgroup_rows),
            "leave_one_sector_out_scenarios": len(sector_rows),
            "daily_partitions": len(day_rows),
        },
        "artifacts": artifact_hashes,
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": [
            "ONLY_FIVE_RECENT_SESSIONS",
            "ONLY_TWO_HELD_OUT_SESSIONS",
            "CURRENT_NON_POINT_IN_TIME_UNIVERSE",
            "PUBLIC_WEB_SOURCE_NOT_AUTHORIZED_REALTIME_FEED",
            "NO_PAPER_OR_SHADOW_SESSION_EVIDENCE",
        ],
        "realtime_readiness": False,
        "real_data_paper_readiness": False,
        "external_order_transmission": False,
    }
    signed_report = {**report, "evidence_sha256": _canonical_sha256(report)}
    _write_json(args.output / "report.json", signed_report)
    print(json.dumps(signed_report, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
