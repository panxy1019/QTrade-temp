"""Atomically publish a strictly advancing verified forward-observation report."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, time
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

from qtrade.api.app import (
    _is_sha256,
    _load_verified_development_policy_search,
    _load_verified_forward_observation_report,
    _load_verified_forward_preregistration,
    _load_verified_minute_dataset,
)
from qtrade.data import Bar, BarQualityGate
from qtrade.market_data import SINA_FIVE_MINUTE_SESSION_TIMES
from scripts.prepare_baostock_forward_preregistration import (
    _canonical_sha256,
    _load_signed,
)
from scripts.run_baostock_locked_forward_observation import _load_forward_sessions

_FROZEN_FORWARD_ANCHOR_DAY = "2026-08-25"
_FROZEN_FORWARD_ANCHOR_SHA256 = (
    "e057f6e638aebcc9cb02b2ffb106913628734f12730e5506620829ab609e6a29"
)
_FROZEN_FORWARD_PROVIDER_VERSION = "0.9.3"
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _valid_source_session_metadata(
    report: dict[str, object],
    candidate: dict[str, object],
    *,
    validation_time: datetime,
) -> bool:
    manifest = report.get("manifest")
    provider_version = report.get("provider_version")
    if not isinstance(manifest, dict) or not isinstance(provider_version, str):
        return False
    try:
        target_day = date.fromisoformat(str(report.get("target_day")))
        collected_at = datetime.fromisoformat(str(report.get("collected_at")))
        ingestion_time = datetime.fromisoformat(str(manifest.get("ingestion_time")))
        candidate_generated_at = datetime.fromisoformat(
            str(candidate.get("generated_at"))
        )
    except ValueError:
        return False
    if (
        collected_at.tzinfo is None
        or ingestion_time.tzinfo is None
        or candidate_generated_at.tzinfo is None
    ):
        return False
    collected_china = collected_at.astimezone(_SHANGHAI)
    return (
        report.get("schema_version") == 1
        and report.get("report_id") == "baostock-complete-forward-session-v1"
        and report.get("target_day") == target_day.isoformat()
        and target_day > date.fromisoformat(_FROZEN_FORWARD_ANCHOR_DAY)
        and collected_china.date() >= target_day
        and (
            collected_china.date() != target_day
            or collected_china.time() >= time(15, 10)
        )
        and ingestion_time <= collected_at <= candidate_generated_at
        and candidate_generated_at <= validation_time
        and manifest.get("data_version")
        == f"baostock-forward-{target_day.isoformat()}-qfq-v1"
        and manifest.get("source")
        == f"BaoStock {provider_version} native 5-minute; adjustflag=2"
        and manifest.get("schema_version") == "qtrade.bar.v1"
        and _is_sha256(manifest.get("checksum"))
        and manifest.get("quality_status") == "PASSED"
        and report.get("anchor_day") == _FROZEN_FORWARD_ANCHOR_DAY
        and report.get("anchor_sha256") == _FROZEN_FORWARD_ANCHOR_SHA256
        and report.get("preregistration_id")
        == "baostock-locked-forward-observation-v1"
        and report.get("preregistration_sha256")
        == candidate.get("preregistration_sha256")
        and report.get("provider") == "BaoStock"
        and provider_version == _FROZEN_FORWARD_PROVIDER_VERSION
        and report.get("frequency") == "5"
        and report.get("adjustflag") == "2"
        and report.get("point_in_time") is False
        and report.get("future_adjustment_knowledge_possible") is True
        and report.get("realtime_readiness") is False
        and report.get("real_data_paper_readiness") is False
        and report.get("promotion") == "NOT_ELIGIBLE"
        and report.get("external_order_transmission") is False
    )


def _valid_source_bar_grid(
    source_bars: list[Bar],
    source_reports: list[dict[str, object]],
    expected_symbols: tuple[str, ...],
) -> bool:
    try:
        target_days = tuple(
            date.fromisoformat(str(report.get("target_day")))
            for report in source_reports
        )
    except ValueError:
        return False
    if (
        len(expected_symbols) != 75
        or len(set(expected_symbols)) != len(expected_symbols)
        or len(target_days) != len(set(target_days))
        or BarQualityGate().validate(
            source_bars,
            expected_trading_days=set(target_days),
        )
    ):
        return False
    expected_keys = {
        (symbol, trading_day)
        for trading_day in target_days
        for symbol in expected_symbols
    }
    observed_counts = Counter(
        (bar.symbol, bar.trading_day) for bar in source_bars
    )
    expected_times = set(SINA_FIVE_MINUTE_SESSION_TIMES)
    observed_times: dict[tuple[str, date], set[time]] = {}
    for bar in source_bars:
        local = bar.timestamp.astimezone(_SHANGHAI)
        if (
            bar.symbol not in expected_symbols
            or bar.exchange != ("SSE" if bar.symbol.startswith("6") else "SZSE")
            or local.date() != bar.trading_day
            or local.time() not in expected_times
        ):
            return False
        observed_times.setdefault((bar.symbol, bar.trading_day), set()).add(
            local.time()
        )
    return (
        len(source_bars)
        == len(target_days) * len(expected_symbols) * len(expected_times)
        and observed_counts
        == Counter({key: len(expected_times) for key in expected_keys})
        and observed_times
        == {key: expected_times for key in expected_keys}
    )


def validate_source_sessions(
    candidate: dict[str, object],
    source_reports: list[dict[str, object]],
    *,
    validation_time: datetime | None = None,
    source_bars: list[Bar] | None = None,
    expected_symbols: tuple[str, ...] | None = None,
) -> None:
    checked_at = validation_time or datetime.now(_SHANGHAI)
    if checked_at.tzinfo is None:
        raise ValueError("forward source validation time must be timezone-aware")
    expected = [
        {
            "target_day": report.get("target_day"),
            "evidence_sha256": report.get("evidence_sha256"),
            "bar_count": report.get("bar_count"),
            "anchor_exact_match": report.get("anchor_exact_match"),
        }
        for report in source_reports
    ]
    if (
        candidate.get("accepted_future_session_count") != len(source_reports)
        or candidate.get("future_sessions") != expected
        or not all(
            _valid_source_session_metadata(
                report,
                candidate,
                validation_time=checked_at,
            )
            for report in source_reports
        )
    ):
        raise ValueError("candidate does not match verified forward source sessions")
    if (source_bars is not None or expected_symbols is not None) and (
        source_bars is None
        or expected_symbols is None
        or not _valid_source_bar_grid(
            source_bars,
            source_reports,
            expected_symbols,
        )
    ):
        raise ValueError("candidate source bar grid is not the frozen universe")


def validate_progression(
    current: dict[str, object], candidate: dict[str, object]
) -> None:
    current_count = current.get("accepted_future_session_count")
    candidate_count = candidate.get("accepted_future_session_count")
    current_sessions = current.get("future_sessions")
    candidate_sessions = candidate.get("future_sessions")
    if (
        not isinstance(current_count, int)
        or isinstance(current_count, bool)
        or not isinstance(candidate_count, int)
        or isinstance(candidate_count, bool)
        or not isinstance(current_sessions, list)
        or not isinstance(candidate_sessions, list)
    ):
        raise ValueError("forward observation progression fields are invalid")
    if candidate_count <= current_count:
        raise ValueError("candidate must add at least one complete future session")
    if candidate_sessions[:current_count] != current_sessions:
        raise ValueError("candidate rewrites an already published future session")
    if (
        candidate.get("preregistration_sha256")
        != current.get("preregistration_sha256")
        or candidate.get("policy_hash") != current.get("policy_hash")
    ):
        raise ValueError("candidate changes the frozen observation identity")
    current_generated_at = datetime.fromisoformat(str(current.get("generated_at")))
    candidate_generated_at = datetime.fromisoformat(
        str(candidate.get("generated_at"))
    )
    if (
        current_generated_at.tzinfo is None
        or candidate_generated_at.tzinfo is None
        or candidate_generated_at <= current_generated_at
    ):
        raise ValueError("candidate generation time must strictly advance")


def _load_verified_report(
    report_path: Path,
    *,
    preregistration_path: Path,
    dataset_manifest_path: Path,
    development_report_path: Path,
) -> dict[str, object]:
    dataset = _load_verified_minute_dataset(
        dataset_manifest_path,
        expected_classification=(
            "free_historical_qfq_5m_current_non_pit_not_eligible"
        ),
        expected_interval="5m",
        expected_session_count=368,
        expected_times=SINA_FIVE_MINUTE_SESSION_TIMES,
    )
    development = _load_verified_development_policy_search(
        development_report_path, dataset
    )
    preregistration = _load_verified_forward_preregistration(
        preregistration_path, dataset, development
    )
    report = _load_verified_forward_observation_report(
        report_path, preregistration
    )
    if report is None:
        raise ValueError(f"forward observation verification failed: {report_path}")
    return report


def _atomic_publish(
    candidate_path: Path,
    destination: Path,
    *,
    expected_evidence_sha256: str | None = None,
    source_revalidator: Callable[[], None] | None = None,
) -> None:
    payload = candidate_path.read_bytes()
    if expected_evidence_sha256 is not None:
        candidate = json.loads(payload)
        if not isinstance(candidate, dict):
            raise ValueError("candidate payload changed after verification")
        digest = candidate.get("evidence_sha256")
        unsigned = {
            key: value
            for key, value in candidate.items()
            if key != "evidence_sha256"
        }
        if (
            digest != expected_evidence_sha256
            or _canonical_sha256(unsigned) != digest
        ):
            raise ValueError("candidate payload changed after verification")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if source_revalidator is not None:
            source_revalidator()
        temporary_path.replace(destination)
        directory_descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("deployment/evidence/forward-observation/report.json"),
    )
    parser.add_argument(
        "--preregistration",
        type=Path,
        default=Path("deployment/evidence/forward-observation/preregistration.json"),
    )
    parser.add_argument(
        "--dataset-manifest",
        type=Path,
        default=Path("deployment/evidence/long-five-minute-market-data/manifest.json"),
    )
    parser.add_argument(
        "--development-report",
        type=Path,
        default=Path("deployment/evidence/development-policy-search/report.json"),
    )
    parser.add_argument(
        "--forward-root",
        type=Path,
        default=Path("deployment/evidence/forward-session-data"),
    )
    args = parser.parse_args()
    if not args.destination.is_file():
        raise FileNotFoundError("published forward observation baseline is missing")
    if args.candidate.resolve() == args.destination.resolve():
        raise ValueError("candidate and destination must be different files")

    verification = {
        "preregistration_path": args.preregistration,
        "dataset_manifest_path": args.dataset_manifest,
        "development_report_path": args.development_report,
    }
    current = _load_verified_report(args.destination, **verification)
    candidate = _load_verified_report(args.candidate, **verification)
    validate_progression(current, candidate)
    raw_preregistration = _load_signed(
        args.preregistration, "preregistration_sha256"
    )
    raw_dataset = _load_signed(args.dataset_manifest, "evidence_sha256")
    warmup = raw_preregistration.get("warmup_dataset")
    eligible_symbols = raw_dataset.get("eligible_symbols")
    if (
        not isinstance(warmup, dict)
        or raw_dataset.get("evidence_sha256") != warmup.get("evidence_sha256")
        or not isinstance(eligible_symbols, list)
        or len(eligible_symbols) != 75
        or not all(isinstance(symbol, str) for symbol in eligible_symbols)
        or len(set(eligible_symbols)) != len(eligible_symbols)
    ):
        raise ValueError("frozen forward symbol universe is invalid")
    expected_symbols = tuple(eligible_symbols)
    source_bars, source_reports = _load_forward_sessions(
        args.forward_root, preregistration=raw_preregistration
    )
    validate_source_sessions(
        candidate,
        source_reports,
        source_bars=source_bars,
        expected_symbols=expected_symbols,
    )

    def revalidate_sources() -> None:
        current_source_bars, current_source_reports = _load_forward_sessions(
            args.forward_root,
            preregistration=raw_preregistration,
        )
        validate_source_sessions(
            candidate,
            current_source_reports,
            source_bars=current_source_bars,
            expected_symbols=expected_symbols,
        )

    _atomic_publish(
        args.candidate,
        args.destination,
        expected_evidence_sha256=str(candidate["evidence_sha256"]),
        source_revalidator=revalidate_sources,
    )
    published = _load_verified_report(args.destination, **verification)
    if published.get("evidence_sha256") != candidate.get("evidence_sha256"):
        raise RuntimeError("published report does not match the verified candidate")
    print(
        json.dumps(
            {
                "status": "PUBLISHED_VERIFIED_FORWARD_OBSERVATION",
                "accepted_future_session_count": published[
                    "accepted_future_session_count"
                ],
                "evidence_sha256": published["evidence_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
