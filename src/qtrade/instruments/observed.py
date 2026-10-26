"""Current observation identities and source claims, never historical PIT eligibility."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed

SCHEMA = "qtrade.observed-security-master.v1"


def instrument_id(symbol: str, security_type: str) -> str:
    prefixes = ("0", "3", "6") if security_type == "STOCK" else ("15", "16", "5")
    if (
        security_type not in {"STOCK", "ETF"}
        or re.fullmatch(r"\d{6}", symbol) is None or not symbol.startswith(prefixes)
    ):
        raise ValueError("UNSUPPORTED_OBSERVED_SECURITY_IDENTITY")
    return ("SSE." if symbol.startswith(("5", "6")) else "SZSE.") + symbol


def build_observed_master(
    *, vendor_rows: list[dict[str, str]], vendor_sha256: str,
    source_groups: list[dict[str, Any]], observed_at: datetime, target_count: int = 1500,
) -> dict[str, Any]:
    if observed_at.tzinfo is None or not 1 <= target_count <= 6000:
        raise ValueError("INVALID_OBSERVED_MASTER_REQUEST")
    if re.fullmatch(r"[a-f0-9]{64}", vendor_sha256) is None:
        raise ValueError("INVALID_MASTER_SOURCE_HASH")
    catalog: dict[str, dict[str, Any]] = {}
    seen_vendor: set[str] = set()

    def record(symbol: str, kind: str) -> dict[str, Any]:
        identity = instrument_id(symbol, kind)
        if identity not in catalog:
            catalog[identity] = {
                "instrument_id": identity, "symbol": symbol,
                "exchange": identity.split(".")[0], "security_type": kind,
                "name": "", "listing_date": None, "delisting_date": None,
                "status": "UNKNOWN", "source": [], "observed_at": observed_at.isoformat(),
                "valid_from": None, "valid_to": None, "available_at": None,
                "valid_time_basis": "unverified vendor claim; not historical knowledge",
                "data_quality_status": "LIMITED_RESEARCH", "issues": [],
                "in_target": False, "legacy_sources": [], "candidate_rank": None,
            }
        row = catalog[identity]
        if row["security_type"] != kind:
            row["issues"].append("SECURITY_TYPE_CONFLICT")
        return row

    etfs = {r["symbol"] for g in source_groups for r in g["rows"]
            if r.get("security_type") == "ETF"}
    for vendor in vendor_rows:
        code = vendor.get("code", "")
        if re.fullmatch(r"(?:sh|sz)\.\d{6}", code) is None:
            continue
        symbol = code[3:]
        kind = "ETF" if symbol in etfs else "STOCK"
        if kind == "STOCK" and (vendor.get("type") != "1" or symbol[0] not in "036"):
            continue
        row = record(symbol, kind)
        if (code[:2] == "sh") != (row["exchange"] == "SSE"):
            row["issues"].append("VENDOR_EXCHANGE_CONFLICT")
        identity = row["instrument_id"]
        if identity in seen_vendor:
            row["issues"].append("DUPLICATE_VENDOR_IDENTITY")
        seen_vendor.add(identity)
        for key in ("ipoDate", "outDate"):
            if vendor.get(key):
                date.fromisoformat(vendor[key])
        if (vendor.get("ipoDate") and vendor.get("outDate")
                and vendor["ipoDate"] > vendor["outDate"]):
            row["issues"].append("INVALID_VENDOR_DATE_INTERVAL")
        row.update(
            name=vendor.get("code_name", ""), listing_date=vendor.get("ipoDate") or None,
            delisting_date=vendor.get("outDate") or None,
            valid_from=vendor.get("ipoDate") or None, valid_to=vendor.get("outDate") or None,
            status={"1": "ACTIVE_OBSERVED", "0": "INACTIVE_OBSERVED"}.get(
                vendor.get("status", ""), "UNKNOWN"),
        )
        if kind == "ETF" and vendor.get("type") != "5":
            row["issues"].append("VENDOR_SECURITY_TYPE_CONFLICT")
        if row["status"] == "ACTIVE_OBSERVED" and row["delisting_date"]:
            row["issues"].append("ACTIVE_WITH_DELISTING_CLAIM")
        if row["listing_date"] and row["listing_date"] > str(observed_at.date()):
            row["issues"].append("LISTING_AFTER_OBSERVATION")
        row["source"].append({"source_id": "vendor_master", "sha256": vendor_sha256,
                              "vendor_code": code, "available_at": None})

    legacy_ids: set[str] = set()
    candidates: list[str] = []
    source_counts: dict[str, int] = {}
    for group in source_groups:
        if re.fullmatch(r"[a-f0-9]{64}", group["sha256"]) is None:
            raise ValueError("INVALID_OBSERVATION_SOURCE_HASH")
        ids = set()
        for ordinal, claim in enumerate(group["rows"]):
            row = record(claim["symbol"], claim["security_type"])
            identity = row["instrument_id"]
            if identity in ids:
                raise ValueError("DUPLICATE_SOURCE_IDENTITY")
            ids.add(identity)
            row["source"].append({"source_id": group["source_id"], "sha256": group["sha256"],
                                  "source_known_at": group.get("known_at"),
                                  "available_at": None})
            if not row["name"]:
                row["name"] = claim.get("name", "")
            if group["role"] == "legacy":
                legacy_ids.add(identity)
                row["legacy_sources"].append(group["source_id"])
            elif group["role"] == "candidate":
                row["candidate_rank"] = ordinal + 1
                candidates.append(identity)
            else:
                raise ValueError("INVALID_OBSERVATION_SOURCE_ROLE")
        source_counts[group["source_id"]] = len(ids)
    eligible = {key for key, r in catalog.items()
                if r["status"] == "ACTIVE_OBSERVED" and not r["issues"]}
    selected = sorted(legacy_ids & eligible)
    for identity in candidates:
        if len(selected) >= target_count:
            break
        if identity in eligible and identity not in selected:
            row = catalog[identity]
            if "ST" in row["name"].upper() or "退" in row["name"]:
                continue
            selected.append(identity)
    for key, row in catalog.items():
        row["in_target"] = key in selected
        row["issues"] = sorted(set(row["issues"]))
        if row["issues"]:
            row["data_quality_status"] = "QUARANTINED"
    rows = [catalog[key] for key in sorted(catalog)]
    result = signed({
        **AUTHORITY, "schema_version": SCHEMA, "observed_at": observed_at.isoformat(),
        "target_count": target_count, "target_actual": len(selected),
        "state": "TARGET_REACHED" if len(selected) >= target_count else "PARTIAL",
        "selection_policy": "legacy union then frozen current liquidity candidates; non-PIT",
        "legacy_union_count": len(legacy_ids), "legacy_active_retained": len(legacy_ids & eligible),
        "legacy_unresolved_or_inactive": sorted(legacy_ids - eligible),
        "top_up_count": len(set(selected) - legacy_ids), "source_counts": source_counts,
        "master_count": len(rows), "target_by_type": dict(Counter(
            catalog[key]["security_type"] for key in selected)),
        "historical_pit_certified_count": 0,
        "limitations": ["Current selection has survivorship and liquidity selection bias",
                         "Vendor listing/delisting dates are unverified source claims",
                         "Source known_at is not historical row available_at",
                         "Day/5min collection plans do not certify data coverage"],
        "securities": rows,
    })
    # Membership identity is independent of later coverage audits and refresh clocks.
    version = signed({
        "schema_version": "qtrade.observed-universe.v1", "target_count": target_count,
        "selected": sorted(selected), "vendor_sha256": vendor_sha256,
        "sources": [{"source_id": g["source_id"], "sha256": g["sha256"]} for g in source_groups],
    })["evidence_sha256"]
    for row in rows:
        row["universe_version"] = version
    return signed({**{k: v for k, v in result.items() if k != "evidence_sha256"},
                   "universe_version": version})
