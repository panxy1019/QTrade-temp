"""Publish small observation JSON; bulk datasets and write authority stay local."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import time
from contextlib import suppress
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


def _cleanup_git_group(process: subprocess.Popen[bytes]) -> None:
    """Stop the owned Git session even if its leader exited before SSH/send-pack."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        process.poll()  # Reap the direct child; an exited leader does not prove group exit.
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        raise RuntimeError('PUBLICATION_GIT_CLEANUP_FAILED') from None


def run_publication_git(publication: Path, arguments: list[str], *, timeout: float = 90) -> None:
    """Run fixed publisher Git arguments with a 90s command / 4s cleanup bound.

    Git and its ordinary SSH/send-pack descendants share a newly owned POSIX
    session. No shell, output capture or global Git/SSH configuration is involved.
    Windows has no equivalent process-group kill here and fails before spawning.
    """
    if os.name != 'posix':
        raise RuntimeError('PUBLICATION_GIT_PROCESS_GROUP_REQUIRES_POSIX')
    if not 0 < timeout <= 90:
        raise ValueError('PUBLICATION_GIT_TIMEOUT_BOUND')
    # New alias paths reuse existing blobs; a sparse walk may resend those blobs.
    command = ['git', '-c', 'pack.useSparse=false', '-C', str(publication), *arguments]
    process = subprocess.Popen(command, start_new_session=True, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        returncode = process.wait(timeout=timeout)
    finally:
        # Cleanup on timeout, interruption and normal/nonzero leader exit. In
        # particular, wait() returning cannot leave inherited-channel children.
        _cleanup_git_group(process)
    if returncode:
        raise subprocess.CalledProcessError(returncode, command)


def online_publication(source: Path) -> dict[str, Any]:
    from qtrade.api.online_research import unavailable_catalog, validate_catalog

    try:
        if source.stat().st_size > 3_000_000:
            raise ValueError('ONLINE_PUBLICATION_TOO_LARGE')
        return validate_catalog(json.loads(source.read_bytes()))
    except (ValueError, KeyError, TypeError, OSError, AttributeError):
        # Optional research state must not stop independent market publication.
        return unavailable_catalog()


def etf_shadow_publication(source: Path) -> dict[str, Any]:
    from qtrade.research.etf_shadow_publication import validate_etf_shadow_publication

    try:
        raw = source.read_bytes()
        if len(raw) > 1_000_000:
            raise ValueError('ETF_SHADOW_PUBLICATION_TOO_LARGE')
        return validate_etf_shadow_publication(json.loads(raw))
    except (OSError, ValueError, KeyError, TypeError):
        return signed({**AUTHORITY, 'state': 'NO_VERIFIED_ETF_SHADOW_PUBLICATION'})


def etf_history_publication(source: Path) -> dict[str, Any]:
    from qtrade.research.etf_history_publication import validate_etf_history_publication

    try:
        if source.is_symlink() or source.stat().st_size > 3_000_000:
            raise ValueError('ETF_HISTORY_PUBLICATION_TOO_LARGE')
        return validate_etf_history_publication(json.loads(source.read_bytes()))
    except (OSError, ValueError, KeyError, TypeError):
        return signed({**AUTHORITY, 'state': 'NO_VERIFIED_ETF_HISTORY_PUBLICATION'})


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
    experiments.sort(
        key=lambda row: (row["persistence_kind"] != "FORMAL_IMMUTABLE_RUN", row["experiment_id"])
    )
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


def publish(root: Path, experiment_root: Path, publication: Path, *,
            unified_market_database: Path | None = None) -> None:
    if not (publication / ".git").is_dir():
        raise ValueError("DEDICATED_PUBLICATION_REPOSITORY_REQUIRED")
    remote = subprocess.check_output(
        ["git", "-C", str(publication), "remote", "get-url", "origin"], text=True
    ).strip()
    if remote != "ssh://git@ssh.github.com:443/panxy1019/QTrade-observations.git":
        raise ValueError("PUBLICATION_DESTINATION_MISMATCH")
    snapshot = build_snapshot(root, experiment_root)
    current_files: list[str] = []
    from scripts.publish_unified_market import publish_market_files

    current_files.extend(publish_market_files(root.parent, publication, unified_market_database))
    workflow_root = root.parent / "workflow/public"
    workflow_catalog = workflow_root / "workflow.json"
    if workflow_catalog.is_file():
        workflow = json.loads(workflow_catalog.read_text())
        if signed({k: v for k, v in workflow.items() if k != "evidence_sha256"}) != workflow:
            raise ValueError("WORKFLOW_CATALOG_HASH_MISMATCH")
        for item in workflow["experiments"]:
            if item.get("state") != "COMPLETE":
                continue
            relative = item["detail_file"]
            import re

            if not re.fullmatch(r"workflow-results/[a-f0-9]{64}\.json", relative):
                raise ValueError("WORKFLOW_RESULT_PATH_INVALID")
            value = json.loads((workflow_root / relative).read_text())
            if (
                value.get("evidence_sha256") != item["detail_sha256"]
                or signed({k: v for k, v in value.items() if k != "evidence_sha256"}) != value
            ):
                raise ValueError("WORKFLOW_RESULT_HASH_MISMATCH")
            target = publication / relative
            target.parent.mkdir(exist_ok=True)
            if not target.exists():
                shutil.copyfile(workflow_root / relative, target)
            current_files.append(relative)
        write_json_atomic(publication / "workflow.json", workflow)
        current_files.append("workflow.json")
    from qtrade.research.qlib_publication import build_publication

    calendar = json.loads((REPO / "configs/observer_calendar_2026.json").read_text())
    try:
        qlib = build_publication(root.parent, calendar)
    except Exception:
        # A broken optional model archive must not stop market freshness updates.
        # The separate result channel visibly fails closed without an old curve.
        from qtrade.research.qlib_publication import SCHEMA as QLIB_SCHEMA

        qlib = signed(
            {
                **AUTHORITY,
                "schema_version": QLIB_SCHEMA,
                "published_at": datetime.now(UTC).isoformat(),
                "calendar": calendar,
                "state": "FAILED",
                "progress": {},
                "run": None,
                "artifact_manifest_sha256": None,
            }
        )
    write_json_atomic(publication / "qlib.json", qlib)
    current_files.append("qlib.json")
    current_root = root.parent / "current-data" / "public"
    if (current_root / "current.json").is_file():
        from hashlib import sha256

        from qtrade.api.current_datasets import validate_catalog

        current = json.loads((current_root / "current.json").read_text())
        validate_catalog(json.loads(json.dumps(current)))
        report = snapshot["datasets"]["minute"]["report"]
        manifest = report["manifest"]
        source = root / "data/minute" / manifest["data_version"] / "bars.parquet"
        import pyarrow.parquet as pq

        clocks = pq.read_table(source, columns=["trading_day", "timestamp"]).to_pylist()
        latest = {s: v[:10] for s, v in report["latest_by_symbol"].items()}
        current["intraday"] = {
            **current["minute"],
            "data_version": manifest["data_version"],
            "checksum": manifest["checksum"],
            "bar_count": report["bar_count"],
            "latest_by_symbol": latest,
            "minimum_latest_day": min(latest.values()),
            "observed_start": min(b["timestamp"] for b in clocks),
            "observed_end": max(b["timestamp"] for b in clocks),
            "trading_days": sorted({str(b["trading_day"]) for b in clocks}),
            "observation_report": report,
        }
        current["observer_healthy"] = validate_snapshot(snapshot)["healthy"]
        relative = "datasets/" + manifest["checksum"] + ".parquet"
        if sha256(source.read_bytes()).hexdigest() != manifest["checksum"]:
            raise ValueError("INTRADAY_PUBLICATION_CHECKSUM_MISMATCH")
        (publication / "datasets").mkdir(parents=True, exist_ok=True)
        if not (publication / relative).exists():
            shutil.copyfile(source, publication / relative)
        current_files.append(relative)
        for kind in ("daily", "minute"):
            relative = "datasets/" + current[kind]["checksum"] + ".parquet"
            content = (current_root / relative).read_bytes()
            if (
                len(content) > 20_000_000
                or sha256(content).hexdigest() != current[kind]["checksum"]
            ):
                raise ValueError("CURRENT_PUBLICATION_PARQUET_MISMATCH")
            target = publication / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copyfile(current_root / relative, target)
            current_files.append(relative)
        current = signed({k: v for k, v in current.items() if k != "evidence_sha256"})
        write_json_atomic(publication / "current.json", current)
        current_files.append("current.json")
    unified_file = root.parent / "unified-data/public/unified.json"
    if unified_file.is_file():
        from qtrade.api.unified_data import validate_publication

        raw_unified = unified_file.read_bytes()
        if len(raw_unified) > 12_000_000:
            raise ValueError("UNIFIED_PUBLICATION_TOO_LARGE")
        unified = validate_publication(json.loads(raw_unified))
        write_json_atomic(publication / "unified.json", unified, compact=True)
        current_files.append("unified.json")
    write_json_atomic(publication / "latest.json", snapshot)
    online_file = workflow_root/'online.json'
    write_json_atomic(publication/'online.json', online_publication(online_file))
    current_files.append('online.json')
    try:
        from qtrade.research.signal_publication import validate_signal_publication

        signal_bytes = (workflow_root/'signals.json').read_bytes()
        if len(signal_bytes) > 3_000_000:
            raise ValueError('SIGNAL_PUBLICATION_TOO_LARGE')
        signals = validate_signal_publication(json.loads(signal_bytes))
    except (OSError, ValueError, KeyError, TypeError):
        signals = signed({**AUTHORITY, 'state': 'NO_VERIFIED_SIGNAL_PUBLICATION'})
    write_json_atomic(publication/'signals.json', signals, compact=True)
    current_files.append('signals.json')
    try:
        from qtrade.research.strategy_comparison import validate_comparison_publication

        raw_comparison = (workflow_root/'comparison.json').read_bytes()
        if len(raw_comparison) > 3_000_000:
            raise ValueError('COMPARISON_PUBLICATION_TOO_LARGE')
        comparison = validate_comparison_publication(json.loads(raw_comparison))
    except (OSError, ValueError, KeyError, TypeError):
        comparison = signed({**AUTHORITY, 'state': 'NO_VERIFIED_STRATEGY_COMPARISON'})
    write_json_atomic(publication/'comparison.json', comparison, compact=True)
    current_files.append('comparison.json')
    try:
        from qtrade.research.forward_publication import validate_forward_publication

        raw_forward = (workflow_root/'forward.json').read_bytes()
        if len(raw_forward) > 2_000_000:
            raise ValueError('FORWARD_PUBLICATION_TOO_LARGE')
        forward = validate_forward_publication(json.loads(raw_forward))
    except (OSError, ValueError, KeyError, TypeError):
        forward = signed({**AUTHORITY, 'state': 'NO_VERIFIED_FORWARD_PUBLICATION'})
    write_json_atomic(publication/'forward.json', forward, compact=True)
    current_files.append('forward.json')
    try:
        from qtrade.research.etf_trend import validate_etf_trend_publication

        raw_etf = (workflow_root/'etf-trend.json').read_bytes()
        if len(raw_etf) > 300_000:
            raise ValueError('ETF_PUBLICATION_TOO_LARGE')
        etf = validate_etf_trend_publication(json.loads(raw_etf))
    except (OSError, ValueError, KeyError, TypeError):
        etf = signed({**AUTHORITY, 'state': 'NO_VERIFIED_ETF_DIAGNOSTIC'})
    write_json_atomic(publication/'etf-trend.json', etf, compact=True)
    current_files.append('etf-trend.json')
    write_json_atomic(publication/'etf-forward.json',
                      etf_shadow_publication(workflow_root/'etf-forward.json'), compact=True)
    current_files.append('etf-forward.json')
    write_json_atomic(publication/'etf-history.json',
                      etf_history_publication(workflow_root/'etf-history.json'), compact=True)
    current_files.append('etf-history.json')
    (publication / "README.md").write_text(
        "# QTrade public research observations\n\n"
        "Read-only latest market observations and persisted experiment summaries.\n"
        "Public-source historical data, non-PIT, not trading or promotion authority.\n"
        "Bounded current-chart Parquet is public; full research archives stay on the cluster.\n"
        "Publication interval is configured; consumers must check source clocks and age.\n"
        "Historical simulation ledgers are explicitly labeled and carry no execution authority.\n"
        "No credentials, live account state or arbitrary executable code are published.\n"
    )
    commands = [
        ["add", "--", "latest.json", "README.md", *current_files],
        ["commit", "-m", "Publish research observation " + snapshot["published_at"]],
        ["push", "origin", "HEAD:refs/heads/main"],
    ]
    for arguments in commands:
        run_publication_git(publication, arguments)
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
    parser.add_argument('--unified-market-database', type=Path)
    parser.add_argument('--interval-seconds', type=int, default=300, choices=range(60, 301))
    args = parser.parse_args()
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("live trading must remain disabled")
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        cycle_started = time.monotonic()
        try:
            publish(args.root, args.experiment_root, args.publication,
                    unified_market_database=args.unified_market_database)
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
        stop.wait(max(0, args.interval_seconds-(time.monotonic()-cycle_started)))


if __name__ == "__main__":
    main()
