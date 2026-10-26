"""An offline, source-bound repair dossier. Tasks are evidence gaps, not download orders."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.pit_quality import status_quality
from qtrade.research.workflow import digest


def build_audit(
    *, dataset: dict[str, Any], coverage: dict[str, Any], master: dict[str, Any],
    statuses: dict[str, dict[str, Any]], status_errors: dict[str, str],
    attempts: dict[str, dict[str, Any]], schedule: dict[str, Any], now: datetime,
    official: dict[str, Any] | None = None, observed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if now.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED")
    symbols = [r["symbol"] for r in dataset["stocks"]]
    if len(set(symbols)) != len(symbols) or any(
        re.fullmatch(r"[036][0-9]{5}", s) is None for s in symbols
    ) or dataset["accepted_symbols"] != len(symbols):
        raise ValueError("PIT_AUDIT_DATASET_SCOPE_INVALID")
    if (set(statuses) | set(status_errors)) - set(symbols):
        raise ValueError("PIT_AUDIT_STATUS_OUTSIDE_DATASET")
    vendor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in master["rows"]:
        vendor[row.get("code", "")].append(row)
    global_retry = _clock(schedule.get("next_attempt_at"))
    records, tasks = [], []

    def task(symbol: str, requirement: str, *, not_before: datetime | None = None) -> None:
        body = {"symbol": symbol, "requirement": requirement,
                "not_before": not_before.isoformat() if not_before else None,
                "execution_allowed": False}
        tasks.append({"task_sha256": digest(body), **body})

    for symbol in symbols:
        code = ("sh." if symbol.startswith("6") else "sz.") + symbol
        matches = vendor[code]
        issues = []
        if len(matches) != 1:
            issues.append("MASTER_IDENTITY_MISSING_OR_DUPLICATE")
        else:
            row = matches[0]
            if row.get("type") != "1":
                issues.append("MASTER_SECURITY_TYPE_UNKNOWN_OR_CONFLICT")
            if row.get("status") not in {"0", "1"}:
                issues.append("MASTER_CURRENT_STATUS_UNKNOWN")
            dates = {}
            for key in ("ipoDate", "outDate"):
                value = row.get(key)
                if not value:
                    if key == "ipoDate":
                        issues.append("MASTER_LISTING_DATE_UNKNOWN")
                else:
                    try:
                        if date.fromisoformat(value).isoformat() != value:
                            raise ValueError
                        dates[key] = value
                    except (TypeError, ValueError):
                        issues.append("MASTER_" + key.upper() + "_INVALID")
            if (dates.get("ipoDate") and dates.get("outDate")
                    and dates["ipoDate"] > dates["outDate"]):
                issues.append("MASTER_DATE_ORDER_INVALID")
            if row.get("status") == "1" and row.get("outDate"):
                issues.append("MASTER_ACTIVE_WITH_DELISTING_CLAIM")
        quality = None
        if symbol in status_errors:
            issues.append(status_errors[symbol])
        elif symbol in statuses:
            try:
                quality = status_quality(statuses[symbol], symbol, now,
                                         start="2016-10-24", end=dataset["end"])
            except ValueError:
                issues.append("STATUS_CHECKPOINT_CONTENT_INVALID")
        if quality is None:
            deadlines = [value for value in (
                global_retry, _clock(attempts.get(symbol, {}).get("next_attempt_at"))
            ) if value is not None]
            task(symbol, "ACQUIRE_VALIDATED_VENDOR_STATUS",
                 not_before=max(deadlines) if deadlines else None)
        if issues:
            task(symbol, "REVIEW_SOURCE_IDENTITY_OR_CONTENT")
        if quality and (quality["unknown_trade_state_rows"] or quality["unknown_st_state_rows"]):
            task(symbol, "RESOLVE_UNKNOWN_HISTORICAL_STATE")
        if quality and quality["missing_price_rows"]:
            task(symbol, "RECONCILE_MISSING_RAW_PRICE_FIELDS")
        # A populated source row never discharges these independent requirements.
        task(symbol, "PROVE_HISTORICAL_INFORMATION_AVAILABILITY")
        task(symbol, "RECONCILE_EXACT_RULES_AND_CORPORATE_ACTIONS")
        records.append({"symbol": symbol, "vendor_code": code, "master_matches": len(matches),
                        "issues": issues, "status_quality": quality,
                        "historical_available_at": None, "historical_pit_certified": False})
    official_records = official["records"] if official else []
    official_symbols = sorted({row["symbol"] for row in official_records if row["symbol"]})
    for symbol in official_symbols:
        task(symbol, "ACQUIRE_AND_RECONCILE_DELISTED_PRICE_HISTORY")
    official_review = sorted({row["symbol"] for row in official_records
                              if row["symbol"] and row["issues"]})
    for symbol in official_review:
        task(symbol, "REVIEW_OFFICIAL_MASTER_DATE_OR_IDENTITY_CONFLICT")
    qualities = [r["status_quality"] for r in records if r["status_quality"] is not None]
    observed_rows = observed["securities"] if observed else []
    observed_summary = {
        "state": "AUDITED_CURRENT_OBSERVATION" if observed else "NOT_PROVIDED",
        "source_observed_at": observed.get("observed_at") if observed else None,
        "master_count": len(observed_rows),
        "target_count": sum(bool(r["in_target"]) for r in observed_rows),
        "unknown_status_count": sum(r["status"] == "UNKNOWN" for r in observed_rows),
        "quarantined_count": sum(r["data_quality_status"] == "QUARANTINED"
                                 for r in observed_rows),
        "historical_pit_certified_count": 0,
    }
    return signed({
        **AUTHORITY, "schema_version": "qtrade.public-pit-audit.v1",
        "audited_at": now.isoformat(), "state": "INCOMPLETE_PUBLIC_SOURCE_PIT",
        "historical_available_at": None, "historical_pit_certified": False,
        "complete_historical_pit_symbols": 0, "exact_rules_certified_symbols": 0,
        "dataset_sha256": digest(dataset), "coverage_sha256": digest(coverage),
        "master_sha256": digest(master), "master_observed_at": master["observed_at"],
        "official_reconciliation_sha256": digest(official) if official else None,
        "observed_master_sha256": digest(observed) if observed else None,
        "schedule_sha256": digest(schedule), "attempts_sha256": digest(attempts),
        "global_not_before": global_retry.isoformat() if global_retry else None,
        "scope_symbols": len(symbols),
        "declared_status_symbols": coverage.get("status_symbols"),
        "declared_status_rows": coverage.get("status_rows"),
        "content_validated_status_symbols": len(qualities),
        "content_validated_status_rows": sum(q["rows"] for q in qualities),
        "status_quality_counts": {key: sum(q[key] for q in qualities) for key in (
            "unknown_trade_state_rows", "unknown_st_state_rows", "missing_price_rows",
            "source_suspended_rows", "source_st_rows",
        )},
        "issue_counts": dict(Counter(i for r in records for i in r["issues"])),
        "observed_master": observed_summary,
        "official_delisting": {
            "state": "AUDITED_UNCERTIFIED_RECONCILIATION" if official else "NOT_PROVIDED",
            "raw_rows": len(official_records), "unique_a_identities": len(official_symbols),
            "current_sample_overlap": len(set(official_symbols) & {
                ("sh." if s.startswith("6") else "sz.") + s for s in symbols
            }),
            "review_identities": len(official_review),
            "master_matches_current_source": official is not None and official.get(
                "master_sha256"
            ) == digest(master),
        },
        "repair_plan_mode": "NON_EXECUTING_EVIDENCE_GAP_QUEUE",
        "repair_task_counts": dict(Counter(t["requirement"] for t in tasks)),
        "records": records, "repair_tasks": tasks,
        "limitations": [
            "OBSERVATION_TIME_IS_NOT_HISTORICAL_AVAILABILITY",
            "CURRENT_SAMPLE_SURVIVORSHIP_AND_SELECTION_BIAS_REMAINS",
            "DATE_BOUNDS_ARE_NOT_SESSION_OR_LISTING_COVERAGE_CERTIFICATION",
            "MISSING_PRICE_IS_NOT_EVIDENCE_OF_SUSPENSION",
            "OFFICIAL_DELISTING_DATE_IS_NOT_LAST_TRADE_OR_PUBLICATION_TIME",
            "STATUS_AND_OFFICIAL_LISTS_DO_NOT_RECONSTRUCT_DELISTED_PRICES",
            "EXACT_LIMITS_AUCTIONS_SETTLEMENT_AND_CORPORATE_ACTIONS_UNCERTIFIED",
            "TASKS_DO_NOT_AUTHORIZE_DOWNLOADS_OR_OVERRIDE_GLOBAL_VENDOR_BACKOFF",
        ],
    })


def _clock(value: Any) -> datetime | None:
    if value is None:
        return None
    clock = datetime.fromisoformat(value)
    if clock.tzinfo is None:
        raise ValueError("PIT_AUDIT_RETRY_CLOCK_INVALID")
    return clock
