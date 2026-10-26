"""Bounded continuous day/5min collector using the existing calendar and local queue."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from qtrade.api.unified_data import validate_publication
from qtrade.market_data.akshare import SHANGHAI
from qtrade.market_data.sina_incremental import (
    IncrementalBar,
    completed_klines,
    completed_quotes,
    reconcile_day,
)
from qtrade.market_data.sina_minute import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from scripts.atomic_json import write_json_atomic
from scripts.unified_queue import UnifiedCollectionQueue
from scripts.unified_store import UnifiedBatchStore

REPO = Path(__file__).resolve().parents[1]
KLINE = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
QUOTE = "https://hq.sinajs.cn/"
Source = Callable[[str, dict[str, str]], dict[str, Any]]


def fetch(source_id: str, params: dict[str, str]) -> dict[str, Any]:
    if source_id not in {"sina_klines", "sina_quote"}:
        raise ValueError("UNAPPROVED_SOURCE")
    url = (KLINE + "?" + urlencode(params) if source_id == "sina_klines"
           else QUOTE + "list=" + params["list"])
    body, status, error = b"", None, None
    complete = False
    try:
        with urlopen(Request(url, headers={
            "User-Agent": "QTrade noncommercial historical research",
            "Referer": "https://finance.sina.com.cn/",
        }), timeout=12) as response:
            status, body = response.status, response.read(2_000_001)
        if len(body) > 2_000_000:
            body = body[:2_000_000]
            raise ValueError("SOURCE_SIZE_LIMIT")
        complete = True
    except HTTPError as exc:
        status, body = exc.code, exc.read(2_000_001)
        complete = len(body) <= 2_000_000
        body = body[:2_000_000]
    except Exception as exc:
        error = type(exc).__name__
    return {"source_id": source_id, "request": params, "received_at": datetime.now(UTC).isoformat(),
            "status_code": status, "transport_error": error, "body": body,
            "body_complete": complete}


def require_download(capture: dict[str, Any]) -> bytes:
    if capture["transport_error"] is not None or not capture["body_complete"]:
        raise ValueError("SOURCE_TRANSPORT_ERROR")
    if capture["status_code"] != 200:
        raise ValueError("SOURCE_HTTP_ERROR")
    return bytes(capture["body"])


def error_code(error: Exception) -> str:
    value = str(error).split(":", 1)[0]
    return value if re.fullmatch(r"[A-Z_]{1,100}", value) else "SOURCE_PARSE_OR_FIELDS_FAILED"


def collect_batch(
    store: UnifiedBatchStore, batch: dict[str, Any], calendar: Calendar, *,
    source: Source = fetch, workers: int = 4,
) -> dict[str, Any]:
    if store.batch_path(batch["batch_id"]).exists():
        return store.load(batch["batch_id"])
    spec, kinds = batch["spec"], batch["security_types"]
    target = datetime.fromisoformat(spec["target_end"]).astimezone(SHANGHAI)
    day, freq = target.date(), spec["frequency"]
    symbols = spec["symbols"]
    codes = {s: ("sh" if s.startswith(("5", "6")) else "sz") + s for s in symbols}
    quote_capture = None
    if freq == "day":
        quote_capture = source("sina_quote", {"list": ",".join(codes.values())})

    def request_symbol(symbol: str) -> dict[str, Any]:
        after = spec["after_by_symbol"][symbol]
        count = 96 if after is None else max(2, sum(
            datetime.fromisoformat(after) < datetime.combine(day, t, SHANGHAI) <= target
            for t in SINA_FIVE_MINUTE_SESSION_TIMES
        ) + 2)
        return source("sina_klines", {"symbol": codes[symbol], "scale": "240" if freq == "day"
                                      else "5", "ma": "no", "datalen": "2" if freq == "day"
                                      else str(count)})

    with ThreadPoolExecutor(max_workers=min(4, max(1, workers))) as pool:
        responses = list(pool.map(request_symbol, symbols))
    captures = ([quote_capture] if quote_capture is not None else []) + responses
    raw_ref = store.capture(captures)  # No response is parsed before its bytes are durable.
    quote_lines = {}
    if quote_capture is not None:
        try:
            body = require_download(quote_capture)
            for line in body.splitlines():
                match = re.match(rb'var hq_str_(?:sh|sz)(\d{6})=', line)
                if not match or match[1].decode() not in symbols:
                    raise ValueError("QUOTE_UNKNOWN_IDENTITY")
                symbol = match[1].decode()
                if symbol in quote_lines:
                    raise ValueError("QUOTE_DUPLICATE_IDENTITY")
                quote_lines[symbol] = line
        except Exception:
            quote_lines = {}
    rows: list[IncrementalBar] = []
    outcomes: dict[str, dict[str, Any]] = {}
    for symbol, response in zip(symbols, responses, strict=True):
        decoded_ok, parsed_ok, same_vendor_matched = False, False, False
        try:
            body = require_download(response)
            json.loads(body)
            decoded_ok = True
            parsed = completed_klines(
                body, symbol=symbol, security_type=kinds[symbol],
                frequency=freq, requested_day=day,
                observed_at=datetime.fromisoformat(response["received_at"]), calendar=calendar,
            )
            parsed_ok = True
            after = spec["after_by_symbol"][symbol]
            selected = [r for r in parsed if r.bar.timestamp <= target and (
                after is None or r.bar.timestamp > datetime.fromisoformat(after))]
            warnings = []
            if freq == "day":
                candidate = None
                if symbol in quote_lines and quote_capture is not None:
                    with suppress(Exception):
                        candidate = completed_quotes(
                            quote_lines[symbol], requested={symbol: kinds[symbol]},
                            requested_day=day,
                            observed_at=datetime.fromisoformat(quote_capture["received_at"]),
                            calendar=calendar,
                        )[symbol]
                if candidate is not None:
                    selected = [reconcile_day(selected[0], candidate)]
                    same_vendor_matched = True
                else:
                    raise ValueError("QUOTE_AMOUNT_UNAVAILABLE")
            else:
                expected = {datetime.combine(day, t, SHANGHAI)
                            for t in SINA_FIVE_MINUTE_SESSION_TIMES
                            if datetime.combine(day, t, SHANGHAI) <= target and (
                                after is None or datetime.combine(day, t, SHANGHAI)
                                > datetime.fromisoformat(after))}
                if {r.bar.timestamp for r in selected} != expected:
                    raise ValueError("MINUTE_WINDOW_GAP_REASON_UNKNOWN")
                if any(r.amount is None for r in selected):
                    warnings.append("VENDOR_MINUTE_AMOUNT_UNAVAILABLE")
            if not selected or selected[-1].bar.timestamp != target:
                raise ValueError("STALE_SOURCE_END")
            rows.extend(selected)
            outcomes[symbol] = {"state": "SUCCEEDED", "error_code": None, "warnings": warnings,
                                "decoded": decoded_ok, "parsed": parsed_ok,
                                "same_vendor_matched": same_vendor_matched}
        except Exception as exc:
            outcomes[symbol] = {"state": "FAILED", "error_code": error_code(exc), "warnings": [],
                                "decoded": decoded_ok, "parsed": parsed_ok,
                                "same_vendor_matched": False}
    completed = max(datetime.fromisoformat(c["received_at"]) for c in captures)
    return store.commit(batch["batch_id"], spec, raw_ref, rows, outcomes, completed_at=completed)


def run_pass(
    base: Path, database: Path, *, code_revision: str, seconds: float = 240,
    maximum_symbols: int = 3000, group_size: int = 25, stop: Event | None = None,
    source: Source = fetch, publish_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if re.fullmatch(r"[a-f0-9]{40}", code_revision) is None:
        raise ValueError("COLLECTION_CODE_REVISION_REQUIRED")
    if not 0 < seconds <= 7200 or not 1 <= maximum_symbols <= 6000 or not 1 <= group_size <= 50:
        raise ValueError("INVALID_COLLECTION_RESOURCE_BUDGET")
    stop = stop or Event()
    publication = json.loads((base / "unified-data/public/unified.json").read_text())
    validate_publication(publication)
    master = publication["master"]
    calendar = Calendar(REPO / "configs/observer_calendar_2026.json")
    store = UnifiedBatchStore(base / "unified-data/lake")
    queue = UnifiedCollectionQueue(database)
    recovery = queue.recover(store, now=datetime.now(UTC))
    recovery["amount_rechecks"] = queue.recheck_daily_amounts(store, now=datetime.now(UTC))
    ends = queue.plan(master, calendar, datetime.now(UTC))
    started, attempted, batches = time.monotonic(), 0, 0
    state = {**AUTHORITY, "schema_version": "qtrade.unified-collection-progress.v1",
             "started_at": datetime.now(UTC).isoformat(),
             "universe_version": master["universe_version"],
             "code_revision": code_revision, "required_through": ends, "recovery": recovery}
    state["stage_counts"] = {key: 0 for key in (
        "requested", "downloaded", "parsed_symbols", "validated_symbols",
        "admitted_symbols", "admitted_rows",
        "same_vendor_endpoint_matched", "independent_source_reconciled",
    )}
    last_publication = -1000.0

    def save() -> None:
        nonlocal last_publication
        state.update(updated_at=datetime.now(UTC).isoformat(), attempted_symbols=attempted,
                     completed_batches=batches,
                     elapsed_seconds=round(time.monotonic() - started, 3),
                     free_bytes=shutil.disk_usage(base).free,
                     **queue.snapshot(master["universe_version"]))
        write_json_atomic(base / "unified-data/collection-progress.json", signed(state))
        if publish_progress and (time.monotonic() - last_publication >= 60
                                 or state.get("state") != "COLLECTING"):
            publish_progress(state)
            last_publication = time.monotonic()

    while (not stop.is_set() and time.monotonic() - started < seconds
           and attempted < maximum_symbols):
        any_claimed = False
        for freq in ("day", "5min"):
            if freq not in ends or stop.is_set() or attempted >= maximum_symbols:
                continue
            if shutil.disk_usage(base).free < 100 * 1024**3:
                state["state"] = "STORAGE_RESERVE_PAUSED"
                save()
                return state
            batch = queue.claim(master["universe_version"], freq,
                                datetime.fromisoformat(ends[freq]).date(), now=datetime.now(UTC),
                                code_revision=code_revision, limit=min(group_size,
                                                                      maximum_symbols - attempted))
            if batch is None:
                continue
            any_claimed = True
            state["state"] = "COLLECTING"
            state["active_batch"] = batch["batch_id"]
            save()
            manifest = collect_batch(store, batch, calendar, source=source)
            queue.finish(manifest, now=datetime.now(UTC))
            for key, count in manifest["stage_counts"].items():
                state["stage_counts"][key] += count
            attempted += len(batch["spec"]["symbols"])
            batches += 1
            state["active_batch"] = None
            save()
        if not any_claimed:
            break
    state["state"] = "STOPPED" if stop.is_set() else "PASS_FINISHED"
    save()
    return state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--seconds", type=float, default=240)
    parser.add_argument("--maximum-symbols", type=int, default=3000)
    parser.add_argument("--group-size", type=int, default=25)
    args = parser.parse_args()
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("live trading must remain disabled")
    if os.environ.get("QTRADE_AKSHARE_NONCOMMERCIAL_ACK", "").lower() != "true":
        raise RuntimeError("noncommercial research acknowledgement required")
    result = run_pass(args.base, args.database, code_revision=args.source_revision,
                      seconds=args.seconds, maximum_symbols=args.maximum_symbols,
                      group_size=args.group_size)
    print(json.dumps({key: result[key] for key in (
        "state", "attempted_symbols", "completed_batches", "elapsed_seconds",
    )}))


if __name__ == "__main__":
    main()
