"""Publish small observation JSON; bulk datasets and write authority stay local."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any
from urllib.request import urlopen

from qtrade.api.public_observations import SCHEMA, validate_snapshot
from qtrade.data import Bar, DataManifest, ParquetBarStore, QualityStatus
from qtrade.market_data.akshare import SHANGHAI
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts.atomic_json import write_json_atomic
from scripts.run_data_observer import read_report

REPO = Path(__file__).resolve().parents[1]


def build_snapshot(root: Path, experiment_root: Path) -> dict[str, Any]:
    with urlopen("http://127.0.0.1:8010/data-freshness", timeout=10) as response:
        observer = json.load(response)
    state = json.loads((root / "collector-state.json").read_text())
    datasets: dict[str, Any] = {}
    for kind in ("daily", "minute"):
        report_path = Path(state[kind]["report_path"]).resolve()
        if not report_path.is_relative_to((root / "data" / kind).resolve()):
            raise ValueError("REPORT_OUTSIDE_DATA_ROOT")
        report = read_report(report_path)
        manifest = DataManifest(
            report["manifest"]["data_version"],
            report["manifest"]["source"],
            report["manifest"]["schema_version"],
            datetime.fromisoformat(report["completed_at"]),
            report["manifest"]["checksum"],
            QualityStatus.PASSED,
        )
        bars = ParquetBarStore(root / "data" / kind).read(manifest)
        latest: dict[str, Bar] = {}
        for bar in bars:
            if bar.symbol not in latest or bar.timestamp > latest[bar.symbol].timestamp:
                latest[bar.symbol] = bar
        if observer["streams"][kind].get("data_version") != manifest.data_version:
            raise ValueError("OBSERVATION_CHANGED_DURING_PUBLICATION")
        rows = []
        for symbol, bar in sorted(latest.items()):
            rows.append(
                {
                    "symbol": symbol,
                    "exchange": bar.exchange,
                    "timestamp": bar.timestamp.astimezone(SHANGHAI).isoformat(),
                    "trading_day": str(bar.trading_day),
                    **{
                        name: str(getattr(bar, name))
                        for name in ("open", "high", "low", "close", "volume")
                    },
                }
            )
        datasets[kind] = {"report": report, "latest_bars": rows}
    experiments = []
    for path in sorted(experiment_root.glob("*/publication.json")):
        # Only complete, checksum-verified archives can enter the public catalog.
        from scripts.persist_research_experiment import verify_archive

        verify_archive(path.parent)
        experiments.append(json.loads(path.read_text()))
    snapshot = signed(
        {
            **AUTHORITY,
            "schema_version": SCHEMA,
            "published_at": datetime.now(UTC).isoformat(),
            "calendar": json.loads((REPO / "configs/observer_calendar_2026.json").read_text()),
            "observer": {key: observer[key] for key in ("heartbeat_at", "worker_alive", "streams")},
            "datasets": datasets,
            "experiments": experiments,
        }
    )
    validate_snapshot(snapshot)
    return snapshot


def publish(root: Path, experiment_root: Path, publication: Path) -> None:
    if not (publication / ".git").is_dir():
        raise ValueError("DEDICATED_PUBLICATION_REPOSITORY_REQUIRED")
    remote = subprocess.check_output(
        ["git", "-C", str(publication), "remote", "get-url", "origin"], text=True
    ).strip()
    if remote != "ssh://git@ssh.github.com:443/panxy1019/QTrade-observations.git":
        raise ValueError("PUBLICATION_DESTINATION_MISMATCH")
    snapshot = build_snapshot(root, experiment_root)
    write_json_atomic(publication / "latest.json", snapshot)
    (publication / "README.md").write_text(
        "# QTrade public research observations\n\n"
        "Read-only latest market observations and persisted experiment summaries.\n"
        "Public-source historical data, non-PIT, not trading or promotion authority.\n"
        "Bulk Parquet and complete experiment artifacts stay on the research cluster.\n"
        "Published every five minutes; consumers must check source clocks and publication age.\n"
        "No credentials, accounts, order state or arbitrary executable code are published.\n"
    )
    commands = [
        ["add", "--", "latest.json", "README.md"],
        ["commit", "-m", "Publish research observation " + snapshot["published_at"]],
        ["push", "origin", "HEAD:refs/heads/main"],
    ]
    for arguments in commands:
        subprocess.run(
            ["git", "-C", str(publication), *arguments],
            check=True,
            timeout=90,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    write_json_atomic(
        root / "publication-state.json",
        {
            "outcome": "SUCCEEDED",
            "published_at": snapshot["published_at"],
            "evidence_sha256": snapshot["evidence_sha256"],
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--experiment-root", type=Path, required=True)
    parser.add_argument("--publication", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("live trading must remain disabled")
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        try:
            publish(args.root, args.experiment_root, args.publication)
            print("PUBLICATION_SUCCEEDED", flush=True)
        except Exception as error:
            write_json_atomic(
                args.root / "publication-state.json",
                {
                    "outcome": "FAILED",
                    "attempted_at": datetime.now(UTC).isoformat(),
                    "error": type(error).__name__,
                },
            )
            print("PUBLICATION_FAILED:" + type(error).__name__, flush=True)
            if args.once:
                raise SystemExit(1) from None
        if args.once:
            break
        stop.wait(300)


if __name__ == "__main__":
    main()
