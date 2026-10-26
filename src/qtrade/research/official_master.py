"""Offline official delisting/master reconciliation; never PIT certification."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.workflow import digest


def _date(value: Any) -> str | None:
    if value in (None, "", "-"):
        return None
    if not isinstance(value, str):
        raise ValueError("DATE_STRING_REQUIRED")
    if re.fullmatch(r"[0-9]{8}", value):
        value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("NONCANONICAL_DATE")
    return parsed.isoformat()


def reconcile(
    sse_rows: list[dict[str, Any]], szse_rows: list[dict[str, Any]],
    master: list[dict[str, Any]], universe: set[str],
) -> dict[str, Any]:
    """Keep every source row, quarantine ambiguous identities and compare exact keys."""
    records: list[dict[str, Any]] = []
    vendor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in master:
        if isinstance(row.get("code"), str):
            vendor[row["code"]].append(row)
    for exchange, rows in (("SSE", sse_rows), ("SZSE", szse_rows)):
        if not rows:
            raise ValueError("EMPTY_OFFICIAL_SOURCE")
        for index, row in enumerate(rows):
            issues = []
            if exchange == "SSE":
                code = row.get("COMPANY_CODE")
                is_a = isinstance(code, str) and re.fullmatch(r"6[0-9]{5}", code)
                identity_known = is_a and row.get("A_STOCK_CODE") == code
                source_type = row.get("STOCK_TYPE")
                if identity_known and source_type not in {"1", "8"}:
                    issues.append("SHARE_CLASS_DATE_SEMANTICS_UNVERIFIED")
                listed, delisted = row.get("LIST_DATE"), row.get("DELIST_DATE")
            else:
                code = row.get("证券代码")
                is_a = isinstance(code, str) and re.fullmatch(r"(?:00|30)[0-9]{4}", code)
                identity_known = is_a
                source_type = None
                listed, delisted = row.get("上市日期"), row.get("终止上市日期")
            symbol = (("sh." if exchange == "SSE" else "sz.") + code
                      if identity_known and isinstance(code, str) else None)
            if symbol is None:
                issues.append("NON_A_OR_UNRESOLVED_IDENTITY")
            try:
                listing_date, delisting_date = _date(listed), _date(delisted)
                if listing_date is None or delisting_date is None:
                    issues.append("OFFICIAL_DATE_MISSING")
                elif listing_date > delisting_date:
                    issues.append("OFFICIAL_DATE_ORDER_INVALID")
            except ValueError:
                listing_date, delisting_date = None, None
                issues.append("OFFICIAL_DATE_INVALID")
            records.append({
                "exchange": exchange, "source_row": index, "raw_row_sha256": digest(row),
                "source_stock_type": source_type, "symbol": symbol,
                "listing_date": listing_date, "reported_delisting_date": delisting_date,
                "raw_listing_date": listed, "raw_delisting_date": delisted,
                "in_current_research_sample": symbol is not None and symbol[3:] in universe,
                "issues": issues,
            })
    counts = Counter(row["symbol"] for row in records if row["symbol"] is not None)
    for row in records:
        symbol, issues = row["symbol"], row["issues"]
        matches = vendor.get(symbol, [])
        row["vendor_match_count"] = len(matches)
        if symbol is None:
            row["state"] = "QUARANTINED_IDENTITY"
            continue
        if counts[symbol] != 1:
            issues.append("DUPLICATE_OFFICIAL_IDENTITY")
        if len(matches) != 1:
            issues.append("MISSING_VENDOR_IDENTITY" if not matches else "DUPLICATE_VENDOR_IDENTITY")
        else:
            match = matches[0]
            row["vendor"] = {k: match.get(k) for k in ("type", "status", "ipoDate", "outDate")}
            if match.get("type") != "1":
                issues.append("VENDOR_SECURITY_TYPE_MISMATCH")
            if match.get("status") != "0":
                issues.append("VENDOR_NOT_MARKED_INACTIVE")
            for official, field in (("listing_date", "ipoDate"),
                                    ("reported_delisting_date", "outDate")):
                try:
                    value = _date(match.get(field))
                except ValueError:
                    issues.append("INVALID_VENDOR_" + field.upper())
                    continue
                if value is None:
                    issues.append("MISSING_VENDOR_" + field.upper())
                elif row[official] is not None and row[official] != value:
                    issues.append("CONFLICT_" + field.upper())
        row["state"] = "REQUIRES_REVIEW" if issues else "DATES_AGREE_UNCERTIFIED"
    return signed({
        **AUTHORITY, "schema_version": "qtrade.official-master-reconciliation.v1",
        "historical_available_at": None, "historical_pit_certified": False,
        "used_in_qlib": False, "source_sample_bias_removed": False,
        "official_rows": len(records), "a_identity_rows": sum(counts.values()),
        "unique_a_identities": len(counts), "current_sample_symbols": len(universe),
        "state_counts": dict(Counter(row["state"] for row in records)),
        "issue_counts": dict(Counter(issue for row in records for issue in row["issues"])),
        "records": records,
        "limitations": ["CURRENT_LIST_NOT_HISTORICAL_ASOF_SNAPSHOT",
                        "REPORTED_DELISTING_DATE_NOT_LAST_TRADE_OR_PUBLICATION_TIME",
                        "PRICE_CORPORATE_ACTION_ST_SUSPENSION_AND_EXACT_RULES_NOT_REPAIRED"],
    })
