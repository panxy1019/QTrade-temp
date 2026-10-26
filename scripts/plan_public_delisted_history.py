"""Replay preserved official inputs and seal bounded delisted day tasks; never downloads."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import re
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.delisted_probe_plan import build_delisted_probe_plan, initial_probe_scope
from qtrade.research.official_master import reconcile
from scripts.atomic_json import write_json_immutable
from scripts.history_backfill_store import request_for
from scripts.reconcile_official_master import verified_raw

FILES = {"plan.json", "reconciliation.json", "master.json", "dataset.json",
         "official-raw.json", "request-adapter.json"}
MAX_INPUT_BYTES = 15_000_000
MAX_RAW_BYTES = 5_000_000


def read_json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("DELISTED_INPUT_SIZE_LIMIT")
    value: dict[str, Any] = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("DELISTED_INPUT_OBJECT_REQUIRED")
    return value


def verified_identity(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ValueError("DELISTED_INPUT_HASH_INVALID")
    return value


def replay_official(
    reconciliation: dict[str, Any], master: dict[str, Any], dataset: dict[str, Any],
    raw: dict[str, Any], *, now: datetime,
) -> None:
    """Reproduce every official row, including disagreements and quarantined identities."""
    if set(raw) != {"SSE", "SZSE"}:
        raise ValueError("DELISTED_OFFICIAL_SOURCE_SET_INVALID")
    decoded = {}
    for exchange, item in raw.items():
        payload = base64.b64decode(item["bytes_base64"], validate=True)
        receipt = reconciliation["raw_sources"][exchange]
        if (not 0 < len(payload) <= MAX_RAW_BYTES or len(payload) != receipt["raw_bytes"]
                or hashlib.sha256(payload).hexdigest() != receipt["raw_sha256"]):
            raise ValueError("DELISTED_OFFICIAL_RAW_BYTES_MISMATCH")
        observed = datetime.fromisoformat(receipt["observed_at"])
        if (observed.tzinfo is None or observed > now
                or receipt.get("historical_available_at") is not None):
            raise ValueError("DELISTED_OFFICIAL_OBSERVATION_CLOCK_INVALID")
        decoded[exchange] = payload
    sse = json.loads(decoded["SSE"])
    sse_rows = sse["result"]
    if (len(sse_rows) != reconciliation["raw_sources"]["SSE"]["rows"]
            or sse["pageHelp"]["total"] != len(sse_rows)):
        raise ValueError("DELISTED_OFFICIAL_SSE_PAGE_INCOMPLETE")
    import pandas as pd  # type: ignore[import-untyped]

    with zipfile.ZipFile(io.BytesIO(decoded["SZSE"])) as archive:
        entries = archive.infolist()
        if len(entries) > 100 or sum(item.file_size for item in entries) > 25_000_000:
            raise ValueError("DELISTED_OFFICIAL_WORKBOOK_DECOMPRESSION_LIMIT")
    frame = pd.read_excel(io.BytesIO(decoded["SZSE"]), dtype=str, keep_default_na=False)
    if (set(frame.columns) != {"证券代码", "证券简称", "上市日期", "终止上市日期"}
            or len(frame) != reconciliation["raw_sources"]["SZSE"]["rows"]):
        raise ValueError("DELISTED_OFFICIAL_SZSE_SCHEMA_OR_COUNT_INVALID")
    expected = reconcile(sse_rows, frame.to_dict(orient="records"), master["rows"],
                         {row["symbol"] for row in dataset["stocks"]})
    if any(reconciliation.get(key) != value for key, value in expected.items()
           if key != "evidence_sha256"):
        raise ValueError("DELISTED_OFFICIAL_RECONCILIATION_REPLAY_MISMATCH")


def adapter(plan: dict[str, Any]) -> dict[str, Any]:
    requests = [{"task_id": task["task_id"], "scope": initial_probe_scope(task),
                 "request": request_for(initial_probe_scope(task))} for task in plan["tasks"]]
    return signed({**AUTHORITY, "schema_version": "qtrade.public-pit-probe-adapter.v1",
                   "plan_sha256": plan["evidence_sha256"], "execution_enabled": False,
                   "state": "REQUESTS_VALIDATED_OFFLINE_NOT_SUBMITTED", "requests": requests})


def verify_bundle(root: Path, *, now: datetime) -> dict[str, Any]:
    manifest = read_json(root / "manifest.json")
    sealed_at = datetime.fromisoformat(manifest["sealed_at"])
    if (signed({k: v for k, v in manifest.items() if k != "evidence_sha256"}) != manifest
            or manifest.get("schema_version") != "qtrade.public-pit-plan-bundle.v1"
            or manifest.get("execution_enabled") is not False
            or manifest.get("historical_pit_certified") is not False
            or manifest.get("downloaded_rows") != 0 or manifest.get("admitted_price_rows") != 0
            or any((manifest.get(k) is not False) if v is False else manifest.get(k) != v
                   for k, v in AUTHORITY.items())
            or sealed_at.tzinfo is None or sealed_at > now
            or set(manifest["files"]) != FILES):
        raise ValueError("DELISTED_BUNDLE_MANIFEST_INVALID")
    values = {}
    for name, identity in manifest["files"].items():
        path = root / name
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError("DELISTED_INPUT_SIZE_LIMIT")
        if hashlib.sha256(path.read_bytes()).hexdigest() != verified_identity(identity):
            raise ValueError("DELISTED_BUNDLE_FILE_HASH_MISMATCH")
        values[name] = read_json(path)
    plan = values["plan.json"]
    replay_official(values["reconciliation.json"], values["master.json"], values["dataset.json"],
                    values["official-raw.json"], now=now)
    expected = build_delisted_probe_plan(
        values["reconciliation.json"], values["master.json"], values["dataset.json"],
        requested_start=date.fromisoformat(plan["requested_start"]),
        requested_end=date.fromisoformat(plan["requested_end"]), now=now,
        code_revision=plan["code_revision"],
    )
    if (plan != expected or root.name != plan["evidence_sha256"]
            or values["request-adapter.json"] != adapter(plan)
            or manifest.get("plan_sha256") != plan["evidence_sha256"]
            or manifest.get("probe_task_count") != plan["probe_task_count"]):
        raise ValueError("DELISTED_BUNDLE_PLAN_OR_ADAPTER_REPLAY_MISMATCH")
    if sealed_at < datetime.fromisoformat(plan["source_observed_at"]):
        raise ValueError("DELISTED_BUNDLE_SEALED_BEFORE_SOURCE_OBSERVATION")
    return manifest


def run(
    base: Path, reconciliation_path: Path, *, requested_start: date, requested_end: date,
    code_revision: str, now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    result = read_json(reconciliation_path)
    identity = verified_identity(result["master_sha256"])
    master = read_json(base / "pit-repair/master" / (identity + ".json"))
    dataset = read_json(base / "qlib-data-20261023/dataset.json")
    raw = {}
    for exchange, suffix in (("SSE", ".json"), ("SZSE", ".xlsx")):
        receipt = result["raw_sources"][exchange]
        if type(receipt["raw_bytes"]) is not int or not 0 < receipt["raw_bytes"] <= MAX_RAW_BYTES:
            raise ValueError("DELISTED_OFFICIAL_SOURCE_SIZE_INVALID")
        payload = verified_raw(base / "pit-independent-sources", receipt, suffix)
        raw[exchange] = {"bytes_base64": base64.b64encode(payload).decode("ascii")}
    replay_official(result, master, dataset, raw, now=now)
    plan = build_delisted_probe_plan(result, master, dataset, requested_start=requested_start,
                                     requested_end=requested_end, now=now,
                                     code_revision=code_revision)
    root = base / "pit-repair/delisted-plans" / plan["evidence_sha256"]
    if (root / "manifest.json").exists():
        return verify_bundle(root, now=now)
    values = {"plan.json": plan, "reconciliation.json": result, "master.json": master,
              "dataset.json": dataset, "official-raw.json": raw,
              "request-adapter.json": adapter(plan)}
    for name, value in values.items():
        path = root / name
        if path.exists():
            if read_json(path) != value:
                raise ValueError("DELISTED_PARTIAL_BUNDLE_INPUT_CONFLICT")
        else:
            write_json_immutable(path, value)
    manifest = signed({
        **AUTHORITY, "schema_version": "qtrade.public-pit-plan-bundle.v1",
        "sealed_at": now.isoformat(), "plan_sha256": plan["evidence_sha256"],
        "execution_enabled": False, "probe_task_count": plan["probe_task_count"],
        "downloaded_rows": 0, "admitted_price_rows": 0, "historical_pit_certified": False,
        "files": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                  for name in sorted(values)},
        "implementation_sha256": {
            "scripts/plan_public_delisted_history.py": hashlib.sha256(
                Path(__file__).read_bytes()).hexdigest(),
            "src/qtrade/research/delisted_probe_plan.py": hashlib.sha256(
                Path(build_delisted_probe_plan.__code__.co_filename).read_bytes()).hexdigest(),
        },
    })
    write_json_immutable(root / "manifest.json", manifest)
    return verify_bundle(root, now=now)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--reconciliation", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.base, args.reconciliation, requested_start=args.start,
                         requested_end=args.end, code_revision=args.source_revision),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
