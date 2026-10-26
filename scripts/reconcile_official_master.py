"""Reconcile preserved official files offline; no network, mutation or promotion."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import signed
from qtrade.research.official_master import reconcile
from qtrade.research.workflow import digest
from scripts.atomic_json import write_json_immutable
from scripts.collect_pit_evidence import verified_master


def verified_raw(root: Path, receipt: dict[str, Any], suffix: str) -> bytes:
    identity = receipt["raw_sha256"]
    if not isinstance(identity, str) or len(identity) != 64 or any(
        char not in "0123456789abcdef" for char in identity
    ):
        raise ValueError("INVALID_OFFICIAL_SOURCE_HASH")
    payload = (root / (identity + suffix)).read_bytes()
    if hashlib.sha256(payload).hexdigest() != identity or len(payload) != receipt["raw_bytes"]:
        raise ValueError("OFFICIAL_SOURCE_BYTES_MISMATCH")
    observed = datetime.fromisoformat(receipt["observed_at"])
    if observed.tzinfo is None or observed > datetime.now(UTC) or receipt.get(
        "historical_available_at"
    ) is not None:
        raise ValueError("INVALID_OFFICIAL_SOURCE_CLOCK")
    return payload


def run(base: Path, receipt_path: Path) -> dict[str, Any]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if signed({k: v for k, v in receipt.items() if k != "evidence_sha256"}) != receipt or (
        receipt.get("schema_version") != "qtrade.official-source-probe.v1"
        or receipt.get("historical_pit_certified") is not False
        or receipt.get("used_in_qlib") is not False
    ):
        raise ValueError("INVALID_OFFICIAL_PROBE_RECEIPT")
    root = base / "pit-independent-sources"
    sse = json.loads(verified_raw(root, receipt["SSE"], ".json"))
    sse_rows = sse["result"]
    if len(sse_rows) != receipt["SSE"]["rows"] or sse["pageHelp"]["total"] != len(sse_rows):
        raise ValueError("INCOMPLETE_SSE_PAGE")
    import io

    import pandas as pd

    szse_bytes = verified_raw(root, receipt["SZSE"], ".xlsx")
    table = pd.read_excel(io.BytesIO(szse_bytes), dtype=str, keep_default_na=False)
    if set(table.columns) != {"证券代码", "证券简称", "上市日期", "终止上市日期"} or (
        len(table) != receipt["SZSE"]["rows"]
    ):
        raise ValueError("SZSE_WORKBOOK_SCHEMA_OR_COUNT_MISMATCH")
    szse_rows = table.to_dict(orient="records")
    master = verified_master(base / "pit-repair")
    if master is None:
        raise ValueError("VERIFIED_VENDOR_MASTER_REQUIRED")
    dataset = json.loads((base / "qlib-data-20261023/dataset.json").read_text())
    result = reconcile(sse_rows, szse_rows, master[0]["rows"],
                       {row["symbol"] for row in dataset["stocks"]})
    result.pop("evidence_sha256")
    result.update({"observed_at": datetime.now(UTC).isoformat(),
                   "probe_evidence_sha256": receipt["evidence_sha256"],
                   "master_sha256": master[1], "master_observed_at": master[0]["observed_at"],
                   "dataset_sha256": digest(dataset),
                   "raw_sources": {key: receipt[key] for key in ("SSE", "SZSE")}})
    result = signed(result)
    output = root / "reconciliation" / (result["evidence_sha256"] + ".json")
    write_json_immutable(output, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.base, args.receipt)
    print(json.dumps({k: v for k, v in result.items() if k not in {"records", "raw_sources"}},
                     ensure_ascii=False, indent=2))
