"""Compile source-bound delisted day probes without certifying historical eligibility."""

from __future__ import annotations

import calendar
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any

from qtrade.market_data.backfill_plan import month_window
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.pit_quality import SHANGHAI
from qtrade.research.workflow import digest

SCHEMA = "qtrade.public-pit-delisted-plan.v1"
SOURCE = "BAOSTOCK_RAW_ADJUSTFLAG_3"


def _research_only(value: dict[str, Any]) -> bool:
    return all(value.get(k) is False if v is False else value.get(k) == v
               for k, v in AUTHORITY.items())


def _signed(value: dict[str, Any]) -> bool:
    return signed({k: v for k, v in value.items() if k != "evidence_sha256"}) == value


def build_delisted_probe_plan(
    reconciliation: dict[str, Any], master: dict[str, Any], dataset: dict[str, Any], *,
    requested_start: date, requested_end: date, now: datetime, code_revision: str,
) -> dict[str, Any]:
    """Dates select a bounded search anchor; they never trim authoritative membership.

    Only unique, issue-free, independently reproduced reconciliation rows are
    candidates. The caller must replay the preserved official raw files first.
    The plan deliberately cannot authorize a separate vendor executor.
    """
    if (now.tzinfo is None or requested_end >= now.astimezone(SHANGHAI).date()
            or not 0 <= (requested_end - requested_start).days <= 3660
            or re.fullmatch(r"[a-f0-9]{40}", code_revision) is None):
        raise ValueError("DELISTED_PLAN_CLOCK_RANGE_OR_REVISION_INVALID")
    if (not _signed(reconciliation) or not _research_only(reconciliation)
            or reconciliation.get("schema_version") != "qtrade.official-master-reconciliation.v1"
            or reconciliation.get("historical_pit_certified") is not False
            or reconciliation.get("historical_available_at") is not None
            or reconciliation.get("used_in_qlib") is not False
            or reconciliation.get("master_sha256") != digest(master)
            or reconciliation.get("dataset_sha256") != digest(dataset)
            or master.get("source") != "BaoStock query_stock_basic"
            or master.get("historical_available_at") is not None):
        raise ValueError("DELISTED_PLAN_SOURCE_IDENTITY_INVALID")
    for value in (reconciliation["observed_at"], master["observed_at"]):
        clock = datetime.fromisoformat(value)
        if clock.tzinfo is None or clock > now:
            raise ValueError("DELISTED_PLAN_SOURCE_CLOCK_INVALID")
    source_day = datetime.fromisoformat(reconciliation["observed_at"]).astimezone(SHANGHAI).date()
    rows = reconciliation["records"]
    if (not isinstance(rows, list) or not 1 <= len(rows) <= 1000
            or len(rows) != reconciliation["official_rows"]):
        raise ValueError("DELISTED_PLAN_OFFICIAL_ROWS_INVALID")
    counts = Counter(row["symbol"] for row in rows if row["symbol"] is not None)
    vendor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in master["rows"]:
        vendor[row.get("code", "")].append(row)
    decisions, tasks = [], []
    for index, row in enumerate(rows):
        symbol = row["symbol"]
        reasons = list(row["issues"])
        valid_identity = isinstance(symbol, str) and re.fullmatch(
            r"(?:sh\.6[0-9]{5}|sz\.(?:00|30)[0-9]{4})", symbol
        ) is not None
        if not valid_identity:
            reasons.append("NON_A_OR_UNRESOLVED_IDENTITY")
        elif counts[symbol] != 1:
            reasons.append("DUPLICATE_OFFICIAL_IDENTITY")
        if row["state"] != "DATES_AGREE_UNCERTIFIED":
            reasons.append("OFFICIAL_RECONCILIATION_REQUIRES_REVIEW")
        matches = vendor.get(symbol, [])
        if len(matches) != 1:
            reasons.append("VENDOR_IDENTITY_MISSING_OR_DUPLICATE")
        elif matches[0].get("type") != "1" or matches[0].get("status") != "0":
            reasons.append("VENDOR_INACTIVE_STOCK_IDENTITY_UNVERIFIED")
        listing, delisting = None, None
        try:
            listing = date.fromisoformat(row["listing_date"])
            delisting = date.fromisoformat(row["reported_delisting_date"])
            if (str(listing) != row["listing_date"] or str(delisting) != row[
                    "reported_delisting_date"] or listing > delisting):
                raise ValueError
            if delisting > source_day:
                reasons.append("DELISTING_CLAIM_AFTER_SOURCE_OBSERVATION")
            if len(matches) == 1 and (matches[0].get("ipoDate") != str(listing)
                                      or matches[0].get("outDate") != str(delisting)):
                reasons.append("FROZEN_VENDOR_DATE_DISAGREEMENT")
        except (TypeError, ValueError):
            reasons.append("SOURCE_DATE_UNKNOWN_OR_INVALID")
        task_id = None
        state = "QUARANTINED_REQUIRES_REVIEW"
        if not reasons:
            assert valid_identity and isinstance(symbol, str) and delisting is not None
            # Search the entire reported month, including after the reported date.
            # Reported delisting is not used as a last-trade boundary.
            anchor_end = min(requested_end, delisting.replace(day=calendar.monthrange(
                delisting.year, delisting.month)[1]))
            if anchor_end < requested_start:
                state = "OUTSIDE_REQUESTED_RESEARCH_HORIZON"
                reasons.append("REPORTED_DELISTING_MONTH_PRECEDES_REQUESTED_HORIZON")
            else:
                initial = month_window(requested_start, anchor_end)
                assert initial is not None
                instrument = ("SSE." if symbol.startswith("sh.") else "SZSE.") + symbol[3:]
                definition = {
                    "instrument_id": instrument, "symbol": symbol[3:], "security_type": "STOCK",
                    "frequency": "day", "requested_start": str(requested_start),
                    "requested_end": str(anchor_end), "initial_window": [str(v) for v in initial],
                    "execution_source": SOURCE, "source_reconciliation_sha256": digest(
                        reconciliation), "source_record_sha256": digest(row),
                    "source_master_sha256": digest(master),
                    "source_dataset_sha256": digest(dataset),
                    "vendor_listing_date_claim": str(listing),
                    "official_reported_delisting_date_claim": str(delisting),
                    "date_claims_are_probe_anchors_only": True,
                    "listing_claim_used_as_authoritative_boundary": False,
                    "delisting_claim_used_as_last_trade_boundary": False,
                    "historical_available_at": None, "historical_pit_certified": False,
                    "execution_enabled": False,
                }
                task_id = digest(definition)
                tasks.append({"task_id": task_id, **definition})
                state = "BOUNDED_DAY_PROBE_COMPILED_NOT_EXECUTED"
        decisions.append({
            "reconciliation_row": index, "symbol": symbol, "source_record_sha256": digest(row),
            "state": state, "reasons": sorted(set(reasons)), "task_id": task_id,
        })
    return signed({
        **AUTHORITY, "schema_version": SCHEMA,
        "state": "BOUNDED_PROBE_TASKS_COMPILED_NO_DOWNLOAD", "execution_enabled": False,
        "source_reconciliation_sha256": digest(reconciliation),
        "source_master_sha256": digest(master), "source_dataset_sha256": digest(dataset),
        "source_observed_at": reconciliation["observed_at"],
        "vendor_master_observed_at": master["observed_at"], "code_revision": code_revision,
        "requested_start": str(requested_start), "requested_end": str(requested_end),
        "source_rows": len(rows), "unique_a_identities": len(counts),
        "probe_task_count": len(tasks), "decision_counts": dict(Counter(
            row["state"] for row in decisions)),
        "reason_counts": dict(Counter(reason for row in decisions for reason in row["reasons"])),
        "requested_queries": 0, "downloaded_rows": 0, "admitted_price_rows": 0,
        "complete_historical_pit_symbols": 0, "historical_available_at": None,
        "historical_pit_certified": False,
        "adapter_contract": {
            "candidate": "scripts.history_backfill_store.request_for/day adjustflag=3",
            "maximum_calendar_days_per_request": 31,
            "price_basis": "BAOSTOCK_RAW_ADJUSTFLAG_3_VENDOR_CLAIM_UNCERTIFIED",
            "existing_active_plan_compatible": False,
            "required_before_execution": [
                "EXPLICIT_DELISTED_SCOPE_VALIDATOR_AND_ISOLATED_ARCHIVE",
                "SHARED_HISTORY_WRITER_AND_VENDOR_SESSION_LEASES",
                "LIVE_PIT_AND_HISTORY_GLOBAL_SOURCE_BACKOFF_ACROSS_ROOTS",
                "CURRENT_COLLECTION_PRIORITY_AND_STORAGE_RESERVE",
                "BOUNDED_CHILD_DEADLINES_AND_IMMUTABLE_RECEIPT_REPLAY",
            ],
        },
        "limitations": [
            "RECONCILED_DATES_DO_NOT_CERTIFY_PAST_UNIVERSE_OR_INFORMATION_AVAILABILITY",
            "CURRENT_OFFICIAL_LIST_MAY_NOT_INCLUDE_EVERY_HISTORICAL_SECURITY",
            "SYMBOL_AND_DATE_AGREEMENT_DO_NOT_CERTIFY_IMMUTABLE_HISTORICAL_SECURITY_IDENTITY",
            "DATE_ANCHORS_DO_NOT_ASSERT_LAST_TRADE_OR_SOURCE_COVERAGE",
            "HISTORY_DAY_CAPTURE_DOES_NOT_CAPTURE_ISST_OR_EXACT_PRICE_LIMITS",
            "EMPTY_PROBE_IS_NOT_SOURCE_UNAVAILABILITY_OR_SUSPENSION_EVIDENCE",
            "NO_CURRENT_UNIVERSE_PROVIDER_MODEL_OR_ARCHIVE_IS_MODIFIED",
        ],
        "decisions": decisions, "tasks": sorted(tasks, key=lambda row: row["instrument_id"]),
    })


def initial_probe_scope(task: dict[str, Any]) -> dict[str, Any]:
    """Exact adapter request scope, with no lease acquisition or vendor operation."""
    start, end = task["initial_window"]
    return {"instrument_id": task["instrument_id"], "symbol": task["symbol"],
            "security_type": task["security_type"], "frequency": task["frequency"],
            "start": start, "end": end}
