"""Adapt admitted daily batches to a new frozen Qlib dataset, retaining old versions."""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import shutil
import sqlite3
import time
import urllib.request
from contextlib import closing
from datetime import UTC, date, datetime
from datetime import time as wall_time
from hashlib import sha256
from pathlib import Path
from typing import Any

from qtrade.market_data.akshare import SHANGHAI
from qtrade.market_data.sina_incremental import completed_klines
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.qlib_adapter import extend_normalized_daily
from scripts.atomic_json import write_json_atomic, write_json_immutable
from scripts.pit_collection_queue import (
    begin_attempt,
    end_attempt,
    queue_progress,
    read_attempts,
    save_attempts,
    select_batch,
)
from scripts.unified_store import UnifiedBatchStore

REPO = Path(__file__).resolve().parents[1]


def checksum(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def load_signed(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_bytes())
    if signed({k: v for k, v in value.items() if k != 'evidence_sha256'}) != value:
        raise ValueError('INCREMENTAL_DATASET_INPUT_HASH_MISMATCH')
    return value


def daily_tasks(
    base: Path, dataset: dict[str, Any], day: str, universe: str, database: Path | None
) -> list[dict[str, Any]] | None:
    """Snapshot an exact terminal daily scope; rotating minute progress is not daily proof."""
    required = {row["symbol"] for row in dataset["stocks"]}
    master_path = base / "unified-data/public/unified.json"
    if master_path.exists():
        master = load_signed(master_path)["master"]
        if load_signed_document(master) is False or master["universe_version"] != universe:
            return None
        target = [r for r in master["securities"] if r["in_target"] is True]
        required = {r["symbol"] for r in target}
        if not required or len(required) != len(target):
            raise ValueError("FORWARD_COLLECTION_MASTER_SCOPE_INVALID")
    if database is not None:
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as c:
            c.row_factory = sqlite3.Row
            c.execute("BEGIN")
            tasks = [
                dict(r)
                for r in c.execute(
                    "SELECT symbol,security_type,state,desired_end,"
                    "completed_end,last_success_batch FROM collection_tasks WHERE "
                    "universe_version=? AND frequency='day' AND trading_day=? LIMIT 6001",
                    (universe, day),
                )
            ]
    else:
        try:
            progress = load_signed(base / "unified-data/collection-progress.json")
        except FileNotFoundError:
            return None
        if not load_signed_document(progress) or progress.get("universe_version") != universe:
            return None
        daily_end = progress.get("required_through", {}).get("day")
        if daily_end is None or datetime.fromisoformat(daily_end).date().isoformat() != day:
            return None
        tasks = [
            r
            for r in progress.get("tasks", [])
            if r["frequency"] == "day" and r["trading_day"] == day
        ]
    symbols = {r["symbol"] for r in tasks}
    if (
        not 1 <= len(tasks) <= 6000
        or len(symbols) != len(tasks)
        or not required <= symbols
        or any(r["state"] not in {"SUCCEEDED", "FAILED"} for r in tasks)
    ):
        return None
    close_at = datetime.combine(date.fromisoformat(day), wall_time(15), SHANGHAI)
    for task in tasks:
        if database is not None:
            desired = datetime.fromisoformat(task["desired_end"])
            if desired.tzinfo is None or desired != close_at:
                raise ValueError("FORWARD_COLLECTION_DESIRED_DAY_MISMATCH")
            if task["state"] == "SUCCEEDED":
                completed = datetime.fromisoformat(task["completed_end"])
                if completed.tzinfo is None or completed != close_at:
                    raise ValueError("FORWARD_COLLECTION_COMPLETED_DAY_MISMATCH")
        if (
            task["state"] == "SUCCEEDED"
            and re.fullmatch("[a-f0-9]{32}", str(task["last_success_batch"])) is None
        ):
            raise ValueError("FORWARD_COLLECTION_SUCCESS_BATCH_REQUIRED")
    return sorted(tasks, key=lambda r: r["symbol"])


def load_signed_document(value: dict[str, Any]) -> bool:
    return signed({k: v for k, v in value.items() if k != "evidence_sha256"}) == value and all(
        type(value.get(k)) is type(v) and value[k] == v for k, v in AUTHORITY.items()
    )


def preparation_request(
    base: Path,
    dataset: dict[str, Any],
    *,
    day: str,
    revision: str,
    universe: str,
    collection_database: Path | None,
    store: UnifiedBatchStore,
) -> dict[str, Any] | None:
    context = {
        "base_dataset_sha256": dataset["evidence_sha256"],
        "universe_version": universe,
        "day": day,
        "code_revision": revision,
    }
    scope = base / "unified-data/qlib-preparation/input-scopes" / signed(context)["evidence_sha256"]
    frozen_path, request_path = scope / "collection.json", scope / "request.json"
    if frozen_path.exists():
        snapshot = load_signed(frozen_path)
        if not load_signed_document(snapshot) or snapshot["context"] != context:
            raise ValueError("FORWARD_FROZEN_COLLECTION_CONTEXT_CHANGED")
    else:
        tasks = daily_tasks(base, dataset, day, universe, collection_database)
        if tasks is None:
            return None
        snapshot = signed({**AUTHORITY, "context": context, "tasks": tasks,
            'frozen_at': datetime.now(UTC).isoformat(),
            'source': 'COLLECTION_LEDGER' if collection_database is not None else 'SIGNED_PROGRESS',
        })
        write_json_immutable(frozen_path, snapshot)
    if request_path.exists():
        request = load_signed(request_path)
        if (
            not load_signed_document(request)
            or any(request[k] != v for k, v in context.items())
            or request["collection_snapshot_sha256"] != snapshot["evidence_sha256"]
        ):
            raise ValueError("FORWARD_FROZEN_PREPARATION_CONTEXT_CHANGED")
        return request
    frozen_tasks = {r["symbol"]: r for r in snapshot["tasks"]}
    batches: dict[str, Any] = {}
    binding, excluded, manifests = {}, {}, {}
    close_at = datetime.combine(date.fromisoformat(day), wall_time(15), SHANGHAI)
    for row in dataset["stocks"]:
        symbol = row["symbol"]
        task = frozen_tasks.get(symbol)
        if task is None or task["state"] != "SUCCEEDED":
            excluded[symbol] = "NO_QUALIFIED_CURRENT_DAILY_BATCH"
            continue
        try:
            identity = task["last_success_batch"]
            if identity not in batches:
                batches[identity] = store.load(identity)
            batch = batches[identity]
            completed = datetime.fromisoformat(batch["completed_at"])
            if (
                batch["spec"]["frequency"] != "day"
                or batch["spec"]["universe_version"] != universe
                or datetime.fromisoformat(batch["spec"]["target_end"]) != close_at
                or completed.tzinfo is None
                or not close_at <= completed <= datetime.now(UTC)
                or batch["normalized"] is None
                or symbol not in batch["spec"]["symbols"]
                or batch["outcomes"][symbol]["state"] != "SUCCEEDED"
                or batch["outcomes"][symbol]["missing_amount_bars"] != 0
            ):
                raise ValueError("FORWARD_VERIFIED_DAILY_BATCH_REQUIRED")
            binding[symbol] = identity
            manifests[identity] = batch['evidence_sha256']
        except OSError:
            # Retry frozen IDs after transient storage failures without sealing exclusions.
            raise
        except Exception:
            excluded[symbol] = "CURRENT_DAILY_BATCH_VERIFICATION_FAILED"
    request = signed(
        {
            **AUTHORITY,
            **context,
            "batches": binding,
            "collection_snapshot_sha256": snapshot["evidence_sha256"],
            "excluded_current_inputs": excluded,
            'batch_manifests': manifests,
        }
    )
    write_json_immutable(request_path, request)
    return request


def fetch_fixed(url: str, maximum: int = 1_000_000) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(
        url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'},
    ), timeout=12) as response:
        body = response.read(maximum+1)
    if not 0 < len(body) <= maximum:
        raise ValueError('FORWARD_SOURCE_RESPONSE_SIZE_INVALID')
    return bytes(body)


def prepare_stock(
    base_dataset: Path, output: Path, row: dict[str, Any], batch: dict[str, Any],
    store: UnifiedBatchStore, calendar: Calendar,
) -> dict[str, Any]:
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    symbol = row['symbol']
    if re.fullmatch(r'\d{6}', symbol) is None:
        raise ValueError('FORWARD_STOCK_IDENTITY_INVALID')
    target = output / 'stocks' / symbol
    target.mkdir(parents=True, exist_ok=True)
    original = base_dataset / 'stocks' / symbol
    if any(checksum(original/name) != digest for name, digest in row['files'].items()):
        raise ValueError('FORWARD_FROZEN_STOCK_HASH_MISMATCH')
    checkpoint = target / 'manifest.json'
    if checkpoint.exists():
        previous = load_signed(checkpoint)
        if (previous['base_stock_sha256'] != row['evidence_sha256']
                or previous['batch_sha256'] != batch['evidence_sha256']
                or any(checksum(target / name) != digest
                       for name, digest in previous['files'].items())):
            raise ValueError('FORWARD_STOCK_CHECKPOINT_CHANGED')
        return previous
    frozen = pq.read_table(original/'daily.parquet').to_pylist()
    observations = pq.read_table(io.BytesIO(store.read_blob(batch['normalized']))).to_pylist()
    incoming = [{**r, 'date': r['trading_day']} for r in observations if r['symbol'] == symbol]
    if (len(incoming) != 1 or incoming[0]['completion_status'] != 'CLOSED'
            or incoming[0]['amount'] is None or incoming[0]['vwap'] is None
            or batch['spec']['frequency'] != 'day'
            or batch['outcomes'][symbol]['missing_amount_bars'] != 0):
        raise ValueError('FORWARD_ADMITTED_DAILY_CONTRACT_REQUIRED')
    overlap = None
    for capture in store.read_captures(batch['raw']):
        if (capture['source_id'] != 'sina_klines' or capture['request']['symbol'][2:] != symbol
                or capture['status_code'] != 200 or not capture['body_complete']):
            continue
        values = completed_klines(
            capture['body'], symbol=symbol, security_type='STOCK', frequency='day',
            requested_day=datetime.fromisoformat(frozen[-1]['date']).date(),
            observed_at=datetime.fromisoformat(capture['received_at']), calendar=calendar,
        )
        overlap = {**values[0].row(), 'date': values[0].row()['trading_day']}
    if overlap is None:
        raise ValueError('FORWARD_CAPTURE_OVERLAP_MISSING')
    code = ('sh' if symbol.startswith('6') else 'sz') + symbol
    url = f'https://finance.sina.com.cn/realstock/company/{code}/hfq.js'
    body = fetch_fixed(url)
    raw = signed({
        'source': 'Sina hfq.js', 'symbol': symbol, 'url': url,
        'received_at': datetime.now(UTC).isoformat(), 'historical_available_at': None,
        'body_sha256': sha256(body).hexdigest(), 'body_base64': base64.b64encode(body).decode(),
    })
    raw_name = 'factors-' + raw['evidence_sha256'] + '.json'
    write_json_immutable(target/raw_name, raw)
    if raw['symbol'] != symbol or sha256(body).hexdigest() != raw['body_sha256']:
        raise ValueError('FORWARD_FACTOR_CAPTURE_HASH_MISMATCH')
    factors, _ = json.JSONDecoder().raw_decode(body.decode().split('=', 1)[1].lstrip())
    normalized, proof = extend_normalized_daily(frozen, incoming, factors['data'],
                                               symbol=symbol, overlap=overlap)
    # Fresh file only; never truncate a hard link into an older data version.
    normalized_table = pa.Table.from_pylist(normalized)
    buffer = io.BytesIO()
    pq.write_table(normalized_table, buffer, compression='zstd')
    normalized_bytes = buffer.getvalue()
    parquet = target / ('daily-' + sha256(normalized_bytes).hexdigest() + '.parquet')
    if parquet.exists():
        if pq.read_table(parquet).to_pylist() != normalized:
            raise ValueError('FORWARD_INCOMPLETE_PARQUET_CONFLICT')
    else:
        with parquet.open('xb') as handle:
            handle.write(normalized_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        with parquet.open('r+b') as handle:
            os.fsync(handle.fileno())
    if pq.read_table(parquet).to_pylist()[:len(frozen)] != frozen:
        raise ValueError('FORWARD_NORMALIZED_PREFIX_CHANGED')
    for name, digest in row['files'].items():
        if name == 'daily.parquet':
            continue
        destination = target/name
        if not destination.exists():
            os.link(original/name, destination)
        if checksum(destination) != digest:
            raise ValueError('FORWARD_REUSED_RAW_FILE_CHANGED')
    result = signed({
        'symbol': symbol, 'name': row['name'], 'rows': len(normalized),
        'start': row['start'], 'end': normalized[-1]['date'],
        'base_stock_sha256': row['evidence_sha256'], 'batch_sha256': batch['evidence_sha256'],
        'daily_file': parquet.name, 'factor_capture_file': raw_name,
        'observed_at': datetime.now(UTC).isoformat(), 'normalization': proof,
        'files': {p.name: checksum(p) for p in target.iterdir()
                  if p.is_file() and p.name != 'manifest.json'},
    })
    write_json_immutable(checkpoint, result)
    return result


def run(
    base: Path,
    previous: Path,
    *,
    revision: str,
    maximum: int = 25,
    collection_database: Path | None = None,
    day: str | None = None,
    universe_version: str | None = None,
) -> dict[str, Any]:
    if not 1 <= maximum <= 50 or re.fullmatch(r'[a-f0-9]{40}', revision) is None:
        raise ValueError('BOUNDED_FORWARD_PREPARATION_REQUIRED')
    if shutil.disk_usage(base).free < 100*1024**3:
        raise RuntimeError('PERSISTENT_STORAGE_RESERVE_100_GIB')
    dataset = load_signed(previous/'dataset.json')
    if (any(dataset.get(k) != v for k, v in AUTHORITY.items())
            or len({r['symbol'] for r in dataset['stocks']}) != len(dataset['stocks'])
            or not 1 <= len(dataset['stocks']) <= 6000):
        raise ValueError('FORWARD_BASE_DATASET_AUTHORITY_OR_SCOPE_INVALID')
    calendar = Calendar(REPO/'configs/observer_calendar_2026.json')
    day = day if day is not None else str(calendar.expected(datetime.now(UTC), "daily").date())
    if str(date.fromisoformat(day)) != day:
        raise ValueError("FORWARD_EXACT_TRADING_DAY_REQUIRED")
    if day > str(calendar.expected(datetime.now(UTC), 'daily').date()):
        raise ValueError('FORWARD_COMPLETE_DAY_NOT_YET_DUE')
    if day <= dataset['end']:
        raise ValueError('FORWARD_DATASET_REQUIRES_NEW_COMPLETE_DAY')
    declared = dataset.get("incremental", {}).get("observation_universe_version")
    if (previous / "version.json").exists():
        declared_version = load_signed(previous / "version.json")["bindings"][
            "observation_universe_version"
        ]
        if declared is not None and declared != declared_version:
            raise ValueError("FORWARD_BASE_UNIVERSE_CONTEXT_CHANGED")
        declared = declared_version
    if universe_version is not None and declared is not None and universe_version != declared:
        raise ValueError("FORWARD_REQUESTED_UNIVERSE_CONTEXT_CHANGED")
    universe = universe_version or declared
    if universe is None:
        master_path = base / "unified-data/public/unified.json"
        progress_path = base / "unified-data/collection-progress.json"
        if master_path.exists():
            master = load_signed(master_path)["master"]
            if not load_signed_document(master):
                raise ValueError("FORWARD_COLLECTION_MASTER_INVALID")
            universe = master["universe_version"]
        elif collection_database is None and progress_path.exists():
            progress = load_signed(progress_path)
            if not load_signed_document(progress):
                raise ValueError("FORWARD_PROGRESS_AUTHORITY_INVALID")
            universe = progress.get("universe_version")
    waiting = signed(
        {
            **AUTHORITY,
            "day": day,
            "state": "WAITING_FOR_CURRENT_DAILY_INPUTS",
            "provider_published": False,
            "model_activated": False,
        }
    )
    if universe is None:
        return waiting
    if re.fullmatch("[a-f0-9]{64}", universe) is None:
        raise ValueError("FORWARD_COLLECTION_UNIVERSE_IDENTITY_REQUIRED")
    store = UnifiedBatchStore(base / "unified-data/lake")
    request = preparation_request(
        base,
        dataset,
        day=day,
        revision=revision,
        universe=universe,
        collection_database=collection_database,
        store=store,
    )
    if request is None:
        return waiting
    binding = request["batches"]
    output = base/'unified-data/qlib-preparation'/request['evidence_sha256']
    output.mkdir(parents=True, exist_ok=True)
    if not (output/'request.json').exists():
        write_json_immutable(output/'request.json', request)
    elif load_signed(output/'request.json') != request:
        raise ValueError('FORWARD_PREPARATION_REQUEST_CONFLICT')
    ledger_input = {'request_sha256': request['evidence_sha256'],
                    'stocks': [{'symbol': s} for s in binding]}
    entries = read_attempts(output, ledger_input, datetime.now(UTC))
    completed = {s for s in binding if (output/'stocks'/s/'manifest.json').is_file()}
    selected = set(select_batch(list(binding), completed, entries, datetime.now(UTC), maximum))
    accepted, failures, attempted = [], [], 0
    verified_batches: dict[str, Any] = {}
    started = time.monotonic()
    for stock in sorted(dataset['stocks'], key=lambda r: r['symbol'] not in selected):
        symbol = stock['symbol']
        if symbol not in binding:
            failures.append({'symbol': symbol, 'reason': request['excluded_current_inputs'].get(
                symbol, 'NO_QUALIFIED_CURRENT_DAILY_BATCH')})
            continue
        checkpoint = output/'stocks'/symbol/'manifest.json'
        if not checkpoint.exists() and (symbol not in selected or time.monotonic()-started >= 180):
            continue
        fresh = not checkpoint.exists()
        if fresh:
            attempted += 1
            begin_attempt(entries, symbol, datetime.now(UTC))
            save_attempts(output, ledger_input, entries)
        try:
            if binding[symbol] not in verified_batches:
                verified_batches[binding[symbol]] = store.load(binding[symbol])
            batch = verified_batches[binding[symbol]]
            if (
                batch["spec"]["universe_version"] != request["universe_version"]
                or batch['evidence_sha256'] != request['batch_manifests'][binding[symbol]]
                or str(datetime.fromisoformat(batch["spec"]["target_end"]).date()) != day
            ):
                raise ValueError("FORWARD_BATCH_SCOPE_CHANGED")
            accepted.append(prepare_stock(previous, output, stock, batch, store, calendar))
            if fresh:
                end_attempt(entries, symbol, None)
                save_attempts(output, ledger_input, entries)
        except Exception as error:
            message = str(error)
            failures.append({'symbol': symbol, 'reason': message if re.fullmatch(
                r'[A-Z_]{1,100}', message) else type(error).__name__})
            if fresh:
                end_attempt(entries, symbol, error)
                save_attempts(output, ledger_input, entries)
    result = signed({
        **AUTHORITY, 'schema_version': 'qtrade.forward-qlib-preparation.v1',
        'request_sha256': request['evidence_sha256'], 'day': day,
        'updated_at': datetime.now(UTC).isoformat(), 'requested_symbols': len(dataset['stocks']),
        'qualified_current_daily_inputs': len(binding), 'prepared_symbols': len(accepted),
        'attempted_new_symbols': attempted, 'failures': failures,
        'pending_symbols': len(dataset['stocks'])-len(accepted)-len(failures),
        'retry_queue': queue_progress(list(binding), {r['symbol'] for r in accepted},
                                     entries, datetime.now(UTC)),
        'state': 'PREPARATION_ONLY_NOT_A_READY_QLIB_VERSION',
        'provider_published': False, 'model_activated': False, 'historical_pit_certified': False,
        'next_gates': ['COMPLETE_BOUND_SOURCE_ADAPTATION', 'BENCHMARK_AND_CALENDAR_ADAPTATION',
                       'IMMUTABLE_PROVIDER_BUILD_AND_ATOMIC_READY_PUBLICATION'],
    })
    write_json_atomic(output/'progress.json', result)
    write_json_atomic(base/'unified-data/qlib-preparation/latest.json', result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--previous', type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--maximum-symbols', type=int, default=25)
    parser.add_argument("--collection-database", type=Path)
    parser.add_argument("--day")
    parser.add_argument("--universe-version")
    args = parser.parse_args()
    if os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
        raise RuntimeError('LIVE_TRADING_MUST_REMAIN_DISABLED')
    if os.environ.get('QTRADE_AKSHARE_NONCOMMERCIAL_ACK', '').lower() != 'true':
        raise RuntimeError('NONCOMMERCIAL_RESEARCH_ACK_REQUIRED')
    import fcntl

    lease_path = Path('/root/qtrade-local/qlib-data-preparation.lock')
    lease_path.parent.mkdir(parents=True, exist_ok=True)
    with lease_path.open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(
            json.dumps(
                run(
                    args.base,
                    args.previous,
                    revision=args.source_revision,
                    maximum=args.maximum_symbols,
                    collection_database=args.collection_database,
                    day=args.day,
                    universe_version=args.universe_version,
                )
            )
        )


if __name__ == '__main__':
    main()
