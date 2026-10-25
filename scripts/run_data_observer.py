"""Continuous public-source research collection, isolated from trading authority."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from datetime import time as wall_time
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from typing import Any

from qtrade.market_data.akshare import SHANGHAI
from scripts.atomic_json import write_json_atomic, write_json_immutable

REPO = Path(__file__).resolve().parents[1]
AUTHORITY = {
    "classification": "PUBLIC_SOURCE_RESEARCH_OBSERVATION_ONLY",
    "point_in_time": False,
    "realtime_readiness": False,
    "real_data_paper_readiness": False,
    "external_order_transmission": False,
    "live_trading_enabled": False,
    "promotion": "NOT_ELIGIBLE",
}


def signed(value: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {**value, "evidence_sha256": sha256(payload.encode()).hexdigest()}


class Calendar:
    def __init__(self, path: Path):
        self.document = json.loads(path.read_text(encoding="utf-8"))
        self.start = date.fromisoformat(self.document["valid_from"])
        self.end = date.fromisoformat(self.document["valid_through"])
        self.closed = {date.fromisoformat(day) for day in self.document["weekday_closures"]}

    def is_open(self, day: date) -> bool:
        if not self.start <= day <= self.end:
            raise ValueError("CALENDAR_OUTSIDE_VERIFIED_HORIZON")
        return day.weekday() < 5 and day not in self.closed

    def previous(self, day: date) -> date:
        while not self.is_open(day):
            day -= timedelta(days=1)
        return day

    def expected(self, now: datetime, kind: str) -> datetime:
        if now.tzinfo is None:
            raise ValueError("timezone required")
        local = now.astimezone(SHANGHAI)
        self.is_open(local.date())  # expiry never becomes a green weekend
        if kind == "daily":
            day = (
                local.date()
                if local.time() >= wall_time(16, 30)
                else local.date() - timedelta(days=1)
            )
            return datetime.combine(self.previous(day), wall_time(15), SHANGHAI)
        cutoff = local - timedelta(seconds=60)
        day = cutoff.date()
        if not self.is_open(day) or cutoff.time() < wall_time(9, 35):
            return datetime.combine(self.previous(day - timedelta(days=1)), wall_time(15), SHANGHAI)
        if cutoff.time() < wall_time(13, 5):
            minute = min(cutoff.hour * 60 + cutoff.minute, 11 * 60 + 30)
        else:
            minute = min(cutoff.hour * 60 + cutoff.minute, 15 * 60)
        minute -= minute % 5
        return datetime.combine(day, wall_time(minute // 60, minute % 60), SHANGHAI)


def read_report(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("REPORT_MUST_BE_OBJECT")
    unsigned = {key: value for key, value in report.items() if key != "evidence_sha256"}
    if report.get("evidence_sha256") != signed(unsigned)["evidence_sha256"]:
        raise ValueError("REPORT_CHECKSUM_MISMATCH")
    data = path.parent / "bars.parquet"
    if sha256(data.read_bytes()).hexdigest() != report["manifest"]["checksum"]:
        raise ValueError("PARQUET_CHECKSUM_MISMATCH")
    if any(report.get(key) != value for key, value in AUTHORITY.items()):
        raise ValueError("OBSERVATION_AUTHORITY_MISMATCH")
    return report


def freshness(
    report: dict[str, Any] | None, expected: datetime, now: datetime, kind: str
) -> dict[str, Any]:
    result: dict[str, Any] = {"expected_through": expected.isoformat(), "status": "MISSING"}
    if report is None:
        return result
    completed = datetime.fromisoformat(report["completed_at"])
    if completed.tzinfo is None or completed > now + timedelta(seconds=60):
        raise ValueError("FUTURE_OR_NAIVE_COLLECTION_CLOCK")
    latest = {
        symbol: datetime.fromisoformat(value)
        for symbol, value in report["latest_by_symbol"].items()
    }
    if not latest or any(value.tzinfo is None or value > now for value in latest.values()):
        raise ValueError("FUTURE_OR_NAIVE_MARKET_CLOCK")
    tolerance = timedelta(0) if kind == "daily" else timedelta(minutes=20)
    stale = sorted(symbol for symbol, value in latest.items() if expected - value > tolerance)
    complete = len(latest) == report["required_symbol_count"]
    result.update(
        {
            "status": "FRESH" if complete and not stale else "STALE_OR_INCOMPLETE",
            "latest_minimum": min(latest.values()).isoformat(),
            "latest_maximum": max(latest.values()).isoformat(),
            "observed_symbols": len(latest),
            "required_symbols": report["required_symbol_count"],
            "stale_symbols": stale,
            "collected_at": completed.isoformat(),
            "data_version": report["manifest"]["data_version"],
            "checksum": report["manifest"]["checksum"],
            "maximum_tolerated_lag_seconds": tolerance.total_seconds(),
        }
    )
    return result


def collect(kind: str, root: Path, run_id: str) -> None:
    from qtrade.data import DataManifest, ParquetBarStore, QualityStatus
    from qtrade.market_data import AkshareMarketDataProvider, SinaMinuteHistoryClient

    if os.environ.get("QTRADE_AKSHARE_NONCOMMERCIAL_ACK", "").lower() != "true":
        raise RuntimeError("noncommercial research acknowledgement required")
    started = datetime.now(UTC)
    local = started.astimezone(SHANGHAI)
    end_day = local.date() + timedelta(days=int(local.time() >= wall_time(16, 30)))
    end = datetime.combine(end_day, wall_time.min, SHANGHAI)
    if kind == "daily":
        universe = json.loads((REPO / "configs/akshare_etf_universe_v1.json").read_text())
        symbols = tuple(universe["symbols"])
        provider = AkshareMarketDataProvider(noncommercial_research_acknowledged=True)
        try:
            provider.connect_history(probe_symbol=symbols[0])
            bars = list(
                provider.query_history(
                    symbols,
                    start=datetime(2026, 8, 22, tzinfo=SHANGHAI),
                    end=end,
                    interval="1d",
                )
            )
        finally:
            provider.close()
        source = "AKShare 1.18.84 Sina raw daily; research backfill from 2026-08-22"
    else:
        from scripts.probe_sina_intraday_breadth import _load_frozen_stocks

        _, stocks = _load_frozen_stocks(
            REPO / "deployment/evidence/long-five-minute-market-data/manifest.json"
        )
        symbols = tuple(row["symbol"] for row in stocks)

        def fetch(symbol: str) -> tuple[Any, ...]:
            # Two complete sessions; source timestamp and adjustment semantics remain explicit.
            return SinaMinuteHistoryClient(
                datalen=96,
                scale_minutes=5,
                timeout_seconds=12,
                maximum_attempts=2,
            ).fetch(symbol)

        with ThreadPoolExecutor(max_workers=4) as pool:
            groups = list(pool.map(fetch, symbols))
        cutoff = datetime.now(UTC) - timedelta(seconds=60)
        bars = [bar for group in groups for bar in group if bar.timestamp <= cutoff]
        source = "Sina native 5m public history; adjustment not independently certified"
    bars.sort(key=lambda bar: (bar.timestamp, bar.exchange, bar.symbol))
    latest: dict[str, datetime] = {}
    for bar in bars:
        latest[bar.symbol] = max(latest.get(bar.symbol, bar.timestamp), bar.timestamp)
    if set(latest) != set(symbols):
        raise ValueError("REQUIRED_UNIVERSE_INCOMPLETE")
    version = f"observer-{kind}-{run_id}"
    store = ParquetBarStore(root / "data" / kind)
    manifest = store.write(
        bars,
        DataManifest(
            version,
            source,
            "qtrade.bar.v1",
            datetime.now(UTC),
            "pending",
            QualityStatus.PENDING,
        ),
    )
    completed = datetime.now(UTC)
    report = signed(
        {
            **AUTHORITY,
            "kind": kind,
            "started_at": started.isoformat(),
            "completed_at": completed.isoformat(),
            "required_symbol_count": len(symbols),
            "bar_count": len(bars),
            "latest_by_symbol": {k: v.isoformat() for k, v in sorted(latest.items())},
            "collection_mode": "HISTORICAL_BACKFILL_NOT_FORWARD_PAPER",
            "manifest": {
                "data_version": version,
                "source": source,
                "checksum": manifest.checksum,
                "schema_version": manifest.schema_version,
                "quality_status": manifest.quality_status.value,
            },
        }
    )
    write_json_immutable(root / "data" / kind / version / "report.json", report)
    print(
        json.dumps(
            {"version": version, "bars": len(bars), "symbols": len(symbols)}, ensure_ascii=False
        )
    )


class Observer:
    def __init__(self, root: Path, calendar: Calendar):
        self.root, self.calendar = root, calendar
        self.tasks: dict[str, dict[str, Any]] = {"daily": {}, "minute": {}}
        self.heartbeat = datetime.now(UTC)
        state_path = root / "collector-state.json"
        if state_path.exists():
            self.tasks = json.loads(state_path.read_text())

    def snapshot(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        result: dict[str, Any] = {
            **AUTHORITY,
            "checked_at": now.isoformat(),
            "heartbeat_at": self.heartbeat.isoformat(),
            "worker_alive": (now - self.heartbeat).total_seconds() < 90,
            "calendar_valid_through": self.calendar.end.isoformat(),
            "streams": {},
        }
        for kind, task in self.tasks.items():
            try:
                path = Path(task["report_path"]) if task.get("report_path") else None
                report = read_report(path) if path else None
                row = freshness(report, self.calendar.expected(now, kind), now, kind)
                row["last_attempt"] = {
                    key: task.get(key) for key in ("attempted_at", "outcome", "error")
                }
                if task.get("outcome") == "FAILED" and row["status"] == "FRESH":
                    row["status"] = "COLLECTION_FAILED_LAST_DATA_FRESH"
                result["streams"][kind] = row
            except Exception as error:
                result["streams"][kind] = {
                    "status": "INVALID",
                    "error": type(error).__name__ + ":" + str(error)[:120],
                }
        result["healthy"] = result["worker_alive"] and all(
            row["status"] == "FRESH" for row in result["streams"].values()
        )
        return result

    def run_one(self, kind: str) -> None:
        now = datetime.now(UTC)
        run_id = now.strftime("%Y%m%dT%H%M%S%fZ")
        task = self.tasks[kind]
        task.update(attempted_at=now.isoformat(), outcome="RUNNING", error=None)
        self.save()
        logs = self.root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        try:
            with (logs / f"{kind}-{run_id}.log").open("w") as log:
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "scripts.run_data_observer",
                        "--root",
                        str(self.root),
                        "--collect",
                        kind,
                        "--run-id",
                        run_id,
                    ],
                    cwd=REPO,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=360,
                    check=True,
                )
            path = self.root / "data" / kind / f"observer-{kind}-{run_id}" / "report.json"
            report = read_report(path)
            task.update(
                outcome="SUCCEEDED", report_path=str(path), last_success=report["completed_at"]
            )
        except Exception as error:
            task.update(outcome="FAILED", error=type(error).__name__)
        self.save()

    def save(self) -> None:
        write_json_atomic(self.root / "collector-state.json", self.tasks)

    def serve(self, port: int) -> None:
        observer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                status = observer.snapshot()
                if path == "/metrics/prometheus":
                    lines = [f"qtrade_observer_worker_alive {int(status['worker_alive'])}"]
                    lines += [
                        f'qtrade_observer_stream_fresh{{stream="{kind}"}} '
                        f'{int(row["status"] == "FRESH")}'
                        for kind, row in status["streams"].items()
                    ]
                    content, mime = ("\n".join(lines) + "\n").encode(), "text/plain; version=0.0.4"
                elif path in {"/", "/health", "/data-freshness"}:
                    content, mime = (
                        json.dumps(status, ensure_ascii=False).encode(),
                        "application/json",
                    )
                else:
                    self.send_error(404)
                    return
                self.send_response(503 if path == "/health" and not status["healthy"] else 200)
                self.send_header("Content-Type", mime)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        Thread(target=server.serve_forever, daemon=True).start()

    def loop(self, stop: Event) -> None:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future: Future[None] | None = None
            while not stop.is_set():
                self.heartbeat = datetime.now(UTC)
                if future is None or future.done():
                    if future is not None:
                        future.result()
                    local = self.heartbeat.astimezone(SHANGHAI)
                    active = local.weekday() < 5 and (
                        wall_time(9, 30) <= local.time() <= wall_time(15, 15)
                    )
                    for kind, task in self.tasks.items():
                        interval = (900 if active else 21600) if kind == "minute" else 3600
                        attempted = (
                            datetime.fromisoformat(task["attempted_at"])
                            if task.get("attempted_at")
                            else None
                        )
                        if (
                            attempted is None
                            or (self.heartbeat - attempted).total_seconds() >= interval
                        ):
                            future = pool.submit(self.run_one, kind)
                            break
                write_json_atomic(self.root / "freshness.json", self.snapshot())
                stop.wait(10)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--calendar", type=Path, default=REPO / "configs/observer_calendar_2026.json"
    )
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--collect", choices=("daily", "minute"))
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("LIVE_TRADING_ENABLED must remain false")
    args.root.mkdir(parents=True, exist_ok=True)
    if args.collect:
        if not args.run_id or len(args.run_id) > 64 or not args.run_id.isalnum():
            raise ValueError("bounded alphanumeric run identity required")
        collect(args.collect, args.root, args.run_id)
        return
    import fcntl  # Linux service lock stays on container-local storage, not NFS.

    with Path("/tmp/qtrade-data-observer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        observer = Observer(args.root, Calendar(args.calendar))
        observer.serve(args.port)
        stop = Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        observer.loop(stop)


if __name__ == "__main__":
    main()
