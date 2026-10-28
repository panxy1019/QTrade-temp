"""Export recent accepted collection chunks without claiming the collector's writer lease."""
from __future__ import annotations

import argparse
import json
import os
import selectors
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict, deque
from collections.abc import Generator, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import closing
from datetime import UTC, datetime
from decimal import Context, localcontext
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal, cast
from uuid import uuid4

from qtrade.api.unified_data import validate_publication
from qtrade.data.storage import _fsync_directory
from qtrade.market_data.akshare import SHANGHAI
from qtrade.market_data.sina_incremental import (
    PRICE_BASIS,
    _CapturedKlineReader,
    completed_klines,
    completed_quotes,
    reconcile_day,
)
from qtrade.market_data.sina_minute import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_versions import file_hash
from qtrade.research.unified_market import (
    BAR_FIELDS,
    INDEX_LIMIT,
    LIMITATIONS,
    MAX_TARGETS,
    SHARD_LIMIT,
    TOTAL_LIMIT,
    _validated_shard_bytes,
    encoded,
    sessions,
    validate_bar,
    validate_index,
)
from scripts.atomic_json import write_json_atomic
from scripts.unified_store import UnifiedBatchStore, _BatchMaterial

REPO = Path(__file__).resolve().parents[1]
MAX_BATCHES = 12_000
# Each immutable batch admits at most 50 symbols, and the ledger key is
# (batch_id, symbol). Bound metadata independently of source/cache/time budgets.
MAX_CHUNKS = MAX_BATCHES*50
SOURCE_BYTE_BUDGET = 512_000_000
MAX_CACHE_ROWS = 100_000
MAX_CACHE_ESTIMATED_BYTES = 128_000_000
MAX_OBJECT_IO = 16
_CACHE: dict[tuple[str, ...], tuple[dict[str, Any], list[dict[str, Any]]]] = {}
_CACHE_SIZES: dict[tuple[str, ...], int] = {}


class MarketPublicationUncertain(OSError):
    """The head write may have replaced its name without directory durability."""


# Two owned processes validate/encode only constructed shards. At most one job
# is pending per child; raw source, cache and source-byte authority stay here.
_SHARD_WORKER_MIN_TARGETS = 32
_UNREAPED_SHARD_WORKERS: list[subprocess.Popen[bytes]] = []
_WORKER_INPUT_LIMIT = INDEX_LIMIT + 4096
_WORKER_OUTPUT_LIMIT = SHARD_LIMIT + 5
_WORKER_FRAME_LIMITS = {
    b"I": _WORKER_INPUT_LIMIT,
    b"J": SHARD_LIMIT + 5,
    b"R": 16_384,
    b"O": SHARD_LIMIT + 5,
    b"E": 4096,
}
_WORKER_SOURCES = (
    "scripts/publish_unified_market.py",
    "scripts/unified_store.py",
    "src/qtrade/research/unified_market.py",
    "src/qtrade/market_data/sina_incremental.py",
    "src/qtrade/data/quality.py",
    "src/qtrade/data/models.py",
    "src/qtrade/monitoring/data_freshness.py",
    "src/qtrade/instruments/observed.py",
)


def _worker_inventory() -> dict[str, str]:
    from qtrade.data import BarQualityGate
    from qtrade.market_data.sina_incremental import number

    for function, name in [
        (_validated_shard_bytes, _WORKER_SOURCES[2]),
        (number, _WORKER_SOURCES[3]),
        (BarQualityGate.validate, _WORKER_SOURCES[4]),
        (Calendar.expected, _WORKER_SOURCES[6]),
    ]:
        if Path(function.__code__.co_filename).resolve() != (REPO / name).resolve():
            raise ValueError("UNIFIED_MARKET_WORKER_IMPORTED_SOURCE_MISMATCH")
    return {name: sha256((REPO / name).read_bytes()).hexdigest() for name in _WORKER_SOURCES}


def _worker_compact(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def _worker_frame(body: bytes, limit: int) -> bytes:
    if not 1 <= len(body) <= min(limit, _WORKER_FRAME_LIMITS.get(body[:1], 0)):
        raise ValueError("UNIFIED_MARKET_WORKER_FRAME_BOUND")
    return struct.pack("!I", len(body)) + body


def _read_worker_frame(handle: Any, limit: int) -> bytes | None:
    header = handle.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise ValueError("UNIFIED_MARKET_WORKER_INCOMPLETE_FRAME")
    (count,) = struct.unpack("!I", header)
    if not 1 <= count <= limit:
        raise ValueError("UNIFIED_MARKET_WORKER_FRAME_BOUND")
    tag = handle.read(1)
    if len(tag) != 1 or count > _WORKER_FRAME_LIMITS.get(tag, 0):
        raise ValueError("UNIFIED_MARKET_WORKER_FRAME_BOUND")
    chunks = [tag]
    count -= 1
    while count:
        raw = handle.read(count)
        if not raw:
            raise ValueError("UNIFIED_MARKET_WORKER_INCOMPLETE_FRAME")
        chunks.append(raw)
        count -= len(raw)
    return b"".join(chunks)


def _shard_worker() -> int:
    job = 0
    try:
        if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
            raise ValueError("UNIFIED_MARKET_WORKER_LIVE_DISABLED_REQUIRED")
        body = _read_worker_frame(sys.stdin.buffer, _WORKER_INPUT_LIMIT)
        if body is None or body[:1] != b"I":
            raise ValueError("UNIFIED_MARKET_WORKER_INIT_INVALID")
        context = json.loads(body[1:])
        actual = _worker_inventory()
        if set(context) != {"sources", "calendar", "clock"} or context["sources"] != actual:
            raise ValueError("UNIFIED_MARKET_WORKER_SOURCE_MISMATCH")
        calendar = Calendar(context["calendar"])
        clock = datetime.fromisoformat(context["clock"])
        if clock.tzinfo is None:
            raise ValueError("UNIFIED_MARKET_WORKER_CLOCK_INVALID")
        context_hash = sha256(
            json.dumps(
                {k: context[k] for k in ("calendar", "clock")},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        ).hexdigest()
        ready = {"sources": actual, "context_sha256": context_hash}
        sys.stdout.buffer.write(_worker_frame(b"R" + _worker_compact(ready), _WORKER_OUTPUT_LIMIT))
        sys.stdout.buffer.flush()
        while (body := _read_worker_frame(sys.stdin.buffer, _WORKER_INPUT_LIMIT)) is not None:
            if len(body) < 6 or body[:1] != b"J":
                raise ValueError("UNIFIED_MARKET_WORKER_JOB_INVALID")
            (job,) = struct.unpack("!I", body[1:5])
            shard = json.loads(body[5:])
            _, raw = _validated_shard_bytes(shard, calendar, clock)
            sys.stdout.buffer.write(
                _worker_frame(b"O" + struct.pack("!I", job) + raw, _WORKER_OUTPUT_LIMIT)
            )
            sys.stdout.buffer.flush()
    except BaseException as error:
        code = _worker_compact({"class": type(error).__name__, "code": str(error)[:256]})
        try:
            sys.stdout.buffer.write(
                _worker_frame(b"E" + struct.pack("!I", job) + code, _WORKER_OUTPUT_LIMIT)
            )
            sys.stdout.buffer.flush()
        except BaseException:
            pass
        return 1
    return 0


class _ShardEncoders:
    def __init__(self, calendar: Calendar, clock: datetime, deadline: float):
        _UNREAPED_SHARD_WORKERS[:] = [p for p in _UNREAPED_SHARD_WORKERS if p.poll() is None]
        if _UNREAPED_SHARD_WORKERS:
            raise ValueError("UNIFIED_MARKET_WORKER_PREVIOUS_CHILD_UNREAPED")
        self.deadline = deadline
        self.expected = _worker_inventory()
        self.selector = selectors.DefaultSelector()
        self.workers: list[dict[str, Any]] = []
        self.finished = False
        self.closing = False
        self.context = {"calendar": calendar.document, "clock": clock.isoformat()}
        self.context_hash = sha256(
            json.dumps(
                self.context,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        ).hexdigest()
        command = [
            sys.executable,
            "-B",
            "-m",
            "scripts.publish_unified_market",
            "--private-shard-worker",
        ]
        try:
            for _ in range(2):
                env = dict(
                    os.environ,
                    LIVE_TRADING_ENABLED="false",
                    PYTHONPATH=str(REPO / "src") + os.pathsep + str(REPO),
                )
                proc = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=0,
                    cwd=REPO,
                    env=env,
                )
                state = {
                    "process": proc,
                    "input": bytearray(),
                    "output": None,
                    "offset": 0,
                    "ready": False,
                    "job": None,
                    "result": None,
                    "eof": False,
                    "evidence": None,
                }
                self.workers.append(state)
                assert proc.stdin is not None and proc.stdout is not None
                set_blocking = os.set_blocking
                set_blocking(proc.stdin.fileno(), False)
                set_blocking(proc.stdout.fileno(), False)
                self.selector.register(proc.stdout, selectors.EVENT_READ, (state, "read"))
                self.send(state, b"I" + _worker_compact({"sources": self.expected, **self.context}))
            while not all(item["ready"] for item in self.workers):
                self.pump()
        except BaseException:
            self.close(False)
            raise

    def send(self, state: dict[str, Any], body: bytes) -> None:
        if state["output"] is not None:
            raise ValueError("UNIFIED_MARKET_WORKER_SEND_WHILE_BUSY")
        state["output"] = _worker_frame(body, _WORKER_INPUT_LIMIT)
        state["offset"] = 0
        self.selector.register(state["process"].stdin, selectors.EVENT_WRITE, (state, "write"))

    def response(self, state: dict[str, Any], body: bytes) -> None:
        if body[:1] == b"R":
            expected = {"sources": self.expected, "context_sha256": self.context_hash}
            if state["ready"] or state["job"] is not None or json.loads(body[1:]) != expected:
                raise ValueError("UNIFIED_MARKET_WORKER_HANDSHAKE_INVALID")
            state["ready"] = True
            return
        if len(body) < 5 or body[:1] not in {b"O", b"E"}:
            raise ValueError("UNIFIED_MARKET_WORKER_RESPONSE_INVALID")
        (job,) = struct.unpack("!I", body[1:5])
        if body[:1] == b"E":
            if state["ready"] and state["job"] != job or not state["ready"] and job != 0:
                raise ValueError("UNIFIED_MARKET_WORKER_JOB_BINDING_INVALID")
            error = json.loads(body[5:])
            if (
                set(error) != {"class", "code"}
                or not isinstance(error["class"], str)
                or not isinstance(error["code"], str)
            ):
                raise ValueError("UNIFIED_MARKET_WORKER_ERROR_FRAME_INVALID")
            if error["class"] == "ValueError":
                raise ValueError(error["code"])
            raise ValueError("UNIFIED_MARKET_WORKER_FAILED:" + error["class"])
        if not state["ready"] or state["job"] != job or state["result"] is not None:
            raise ValueError("UNIFIED_MARKET_WORKER_JOB_BINDING_INVALID")
        raw = body[5:]
        if not 1 <= len(raw) <= SHARD_LIMIT:
            raise ValueError("UNIFIED_MARKET_WORKER_OUTPUT_BOUND")
        value = json.loads(raw)
        if (
            value["evidence_sha256"] != state["evidence"]
            or signed({k: v for k, v in value.items() if k != "evidence_sha256"}) != value
        ):
            raise ValueError("UNIFIED_MARKET_WORKER_RESULT_EVIDENCE_MISMATCH")
        state["result"] = (value, raw)

    def pump(self) -> None:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("UNIFIED_MARKET_WORKER_TIME_BUDGET")
        events = self.selector.select(remaining)
        if not events:
            raise ValueError("UNIFIED_MARKET_WORKER_TIME_BUDGET")
        for key, _mask in events:
            state, kind = key.data
            proc = state["process"]
            if kind == "write":
                try:
                    count = os.write(
                        proc.stdin.fileno(), memoryview(state["output"])[state["offset"] :]
                    )
                except BlockingIOError:
                    continue
                if count <= 0:
                    raise ValueError("UNIFIED_MARKET_WORKER_WRITE_FAILED")
                state["offset"] += count
                if state["offset"] == len(state["output"]):
                    self.selector.unregister(proc.stdin)
                    state["output"] = None
            else:
                buffered = state["input"]
                if len(buffered) < 4:
                    needed = 4 - len(buffered)
                elif len(buffered) < 5:
                    needed = 5 - len(buffered)
                else:
                    needed = min(65_536, 4 + struct.unpack("!I", buffered[:4])[0] - len(buffered))
                try:
                    raw = os.read(proc.stdout.fileno(), needed)
                except BlockingIOError:
                    continue
                if not raw:
                    if self.closing and state["job"] is None and not buffered:
                        self.selector.unregister(proc.stdout)
                        state["eof"] = True
                        continue
                    raise ValueError("UNIFIED_MARKET_WORKER_EXITED:" + str(proc.poll()))
                state["input"].extend(raw)
                while len(state["input"]) >= 4:
                    (count,) = struct.unpack("!I", state["input"][:4])
                    if not 1 <= count <= _WORKER_OUTPUT_LIMIT:
                        raise ValueError("UNIFIED_MARKET_WORKER_FRAME_BOUND")
                    if len(state["input"]) < 5:
                        break
                    if count > _WORKER_FRAME_LIMITS.get(bytes(state["input"][4:5]), 0):
                        raise ValueError("UNIFIED_MARKET_WORKER_FRAME_BOUND")
                    if len(state["input"]) < 4 + count:
                        break
                    body = bytes(state["input"][4 : 4 + count])
                    del state["input"][: 4 + count]
                    self.response(state, body)

    def encode(self, shards: Iterable[dict[str, Any]]) -> Iterator[tuple[dict[str, Any], bytes]]:
        source = iter(shards)
        pending: deque[dict[str, Any]] = deque()
        sequence = 0

        def submit(state: dict[str, Any]) -> bool:
            nonlocal sequence
            item = next(source, None)
            if item is None:
                return False
            state["job"] = sequence
            state["result"] = None
            state["evidence"] = item.get("evidence_sha256")
            self.send(state, b"J" + struct.pack("!I", sequence) + _worker_compact(item))
            # Start validation before the next source shard is constructed.
            # Merely registering a writable pipe defers work until a later wait.
            while state["output"] is not None:
                self.pump()
            pending.append(state)
            sequence += 1
            return True

        try:
            for state in self.workers:
                submit(state)
            while pending:
                state = pending[0]
                while state["result"] is None:
                    self.pump()
                raw = state["result"]
                state["result"] = None
                state["job"] = None
                pending.popleft()
                yield cast(tuple[dict[str, Any], bytes], raw)
                submit(state)
            self.close(True)
        finally:
            try:
                self.close(False)
            finally:
                close = getattr(source, "close", None)
                if close is not None:
                    close()

    def close(self, success: bool) -> None:
        if self.finished:
            return
        self.finished = True
        cleanup_errors: list[str] = []
        try:
            if success:
                self.closing = True
                for state in self.workers:
                    state["process"].stdin.close()
                while not all(state["eof"] for state in self.workers):
                    self.pump()
                for state in self.workers:
                    remaining = self.deadline - time.monotonic()
                    if remaining <= 0:
                        raise ValueError("UNIFIED_MARKET_WORKER_TIME_BUDGET")
                    try:
                        code = state["process"].wait(timeout=remaining)
                    except subprocess.TimeoutExpired:
                        raise ValueError("UNIFIED_MARKET_WORKER_TIME_BUDGET") from None
                    if code:
                        raise ValueError("UNIFIED_MARKET_WORKER_EXIT_FAILED:" + str(code))
                if _worker_inventory() != self.expected:
                    raise ValueError("UNIFIED_MARKET_WORKER_PARENT_SOURCE_CHANGED")
                if time.monotonic() > self.deadline:
                    raise ValueError("UNIFIED_MARKET_WORKER_TIME_BUDGET")
                return
        finally:

            def live(proc: subprocess.Popen[bytes]) -> bool:
                try:
                    return proc.poll() is None
                except BaseException as error:
                    cleanup_errors.append(type(error).__name__)
                    return True

            alive = [s["process"] for s in self.workers if live(s["process"])]
            for proc in alive:
                try:
                    proc.terminate()
                except BaseException as error:
                    cleanup_errors.append(type(error).__name__)
            until = time.monotonic() + 0.5
            for proc in alive:
                try:
                    proc.wait(timeout=max(0, until - time.monotonic()))
                except subprocess.TimeoutExpired:
                    pass
                except BaseException as error:
                    cleanup_errors.append(type(error).__name__)
            alive = [proc for proc in alive if live(proc)]
            for proc in alive:
                try:
                    proc.kill()
                except BaseException as error:
                    cleanup_errors.append(type(error).__name__)
            until = time.monotonic() + 0.5
            for proc in alive:
                try:
                    proc.wait(timeout=max(0, until - time.monotonic()))
                except BaseException as error:
                    cleanup_errors.append(type(error).__name__)
            for state in self.workers:
                for pipe in (state["process"].stdin, state["process"].stdout):
                    try:
                        pipe.close()
                    except BaseException as error:
                        cleanup_errors.append(type(error).__name__)
            try:
                self.selector.close()
            except BaseException as error:
                cleanup_errors.append(type(error).__name__)
            _UNREAPED_SHARD_WORKERS.extend(
                state["process"] for state in self.workers if live(state["process"])
            )
            if cleanup_errors or _UNREAPED_SHARD_WORKERS:
                raise ValueError("UNIFIED_MARKET_WORKER_REAP_FAILED:" + ",".join(cleanup_errors))


def _parallel_shards(target_count: int) -> bool:
    # Windows pipes do not support this POSIX selector protocol. Small exports
    # avoid process startup; capability selection happens before any child starts.
    return (os.name == 'posix' and callable(getattr(os, 'set_blocking', None))
            and target_count >= _SHARD_WORKER_MIN_TARGETS)


def _encode_shards(shards: Iterable[dict[str, Any]], calendar: Calendar, clock: datetime,
                   deadline: float, parallel: bool
                   ) -> Generator[tuple[dict[str, Any], bytes], None, None]:
    source = iter(shards)
    try:
        if parallel:
            yield from _ShardEncoders(calendar, clock, deadline).encode(source)
        else:
            for shard in source:
                if time.monotonic() > deadline:
                    raise ValueError('UNIFIED_MARKET_TIME_BUDGET')
                _, raw = _validated_shard_bytes(shard, calendar, clock)
                # Ref metadata always belongs to the immutable validated bytes.
                yield json.loads(raw), raw
    finally:
        close = getattr(source, 'close', None)
        if close is not None:
            close()


def _write_object(path: Path, raw: bytes, *, _sync_directory: bool = True,
                  _parent_ready: bool = False) -> None:
    """Persist already encoded bytes with the existing immutable writer's durability contract."""
    if not _parent_ready:
        path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix='.'+path.name+'.', suffix='.tmp',
                                delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        temporary.unlink()
        temporary = None
        if _sync_directory:
            _fsync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _persist_objects(destination: Path, objects: Iterable[tuple[str, bytes]], deadline: float
                     ) -> None:
    """Bound immutable I/O to sixteen in-flight objects; publish no index on any failure."""
    directory = destination/'unified-market/objects'
    directory.mkdir(parents=True, exist_ok=True)

    def persist(digest: str, raw: bytes) -> None:
        if time.monotonic() > deadline:
            raise ValueError('UNIFIED_MARKET_TIME_BUDGET')
        path = directory/(digest+'.json')
        if path.exists():
            if path.is_symlink() or file_hash(path) != digest:
                raise ValueError('UNIFIED_MARKET_IMMUTABLE_OBJECT_CHANGED')
        else:
            _write_object(path, raw, _sync_directory=False, _parent_ready=True)

    remaining = iter(objects)
    pending: set[Future[None]] = set()
    with ThreadPoolExecutor(max_workers=MAX_OBJECT_IO, thread_name_prefix='market-object') as pool:
        def submit() -> bool:
            if time.monotonic() > deadline:
                raise ValueError('UNIFIED_MARKET_TIME_BUDGET')
            item = next(remaining, None)
            if item is None:
                return False
            pending.add(pool.submit(persist, *item))
            return True

        try:
            for _ in range(MAX_OBJECT_IO):
                if not submit():
                    break
            while pending:
                finished, pending = wait(pending, timeout=max(0, deadline-time.monotonic()),
                                         return_when=FIRST_COMPLETED)
                if not finished:
                    raise ValueError('UNIFIED_MARKET_TIME_BUDGET')
                for future in finished:
                    future.result()
                for _ in finished:
                    if not submit():
                        break
        finally:
            for future in pending:
                future.cancel()
            close = getattr(remaining, 'close', None)
            if close is not None:
                close()  # Join the one source prefetch even when object I/O fails.
    if directory.exists():
        # Every file is already fsynced. Make all immutable names and newly-created
        # ancestors durable before any index can reference them, including retries
        # whose earlier failed attempt left all object files present.
        for parent in (directory, directory.parent, destination, destination.parent):
            if time.monotonic() > deadline:
                raise ValueError('UNIFIED_MARKET_TIME_BUDGET')
            _fsync_directory(parent)


def _replay(material: _BatchMaterial, calendar: Calendar, kinds: dict[str, str],
            reader: _CapturedKlineReader | None = None) -> None:
    """Reproduce collector admission from actual receipts before caching normalized rows."""
    manifest, captures, rows = material.validate()
    spec = manifest['spec']
    target = datetime.fromisoformat(spec['target_end']).astimezone(SHANGHAI)
    completed = datetime.fromisoformat(manifest['completed_at'])
    frequency = cast(Literal['day', '5min'], spec['frequency'])
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_symbol[row['symbol']].append(row)
    klines_by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    quotes_by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    lineage_by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for capture in captures:
        if capture['source_id'] == 'sina_klines':
            code = capture['request']['symbol']
            klines_by_code[code].append(capture)
            lineage_by_code[code].append(capture)
        elif capture['source_id'] == 'sina_quote':
            for code in dict.fromkeys(capture['request']['list'].split(',')):
                quotes_by_code[code].append(capture)
                lineage_by_code[code].append(capture)
    session_ends = tuple(stamp for clock in SINA_FIVE_MINUTE_SESSION_TIMES
                         if (stamp := datetime.combine(target.date(), clock, SHANGHAI)) <= target)
    expected_windows: dict[datetime | None, set[datetime]] = {}
    for symbol, actual in by_symbol.items():
        code = ('sh' if symbol.startswith(('5', '6')) else 'sz')+symbol
        klines, quotes = klines_by_code[code], quotes_by_code[code]
        relevant = klines+quotes
        if (len(klines) != 1 or len(quotes) != (1 if frequency == 'day' else 0)
                or any(row['status_code'] != 200 or row['body_complete'] is not True
                       or row['transport_error'] is not None for row in relevant)
                or any(datetime.fromisoformat(row['received_at']).tzinfo is None
                       or datetime.fromisoformat(row['received_at']) > completed
                       for row in relevant)
                or klines[0]['request']['scale'] != ('240' if frequency == 'day' else '5')
                or klines[0]['request']['ma'] != 'no'):
            raise ValueError('UNIFIED_MARKET_RAW_SOURCE_OR_CLOCK_INVALID')
        kline = klines[0]
        parse_klines = reader.parse if reader is not None else completed_klines
        parsed = parse_klines(kline['body'], symbol=symbol, security_type=kinds[symbol],
            frequency=frequency, requested_day=target.date(),
            observed_at=datetime.fromisoformat(kline['received_at']), calendar=calendar)
        after = spec['after_by_symbol'][symbol]
        after_stamp = datetime.fromisoformat(after) if after is not None else None
        selected = [row for row in parsed if row.bar.timestamp <= target and
                    (after_stamp is None or row.bar.timestamp > after_stamp)]
        if frequency == 'day':
            quote = quotes[0]
            lines = [line for line in quote['body'].splitlines()
                     if line.startswith(f'var hq_str_{code}='.encode())]
            if len(lines) != 1 or len(selected) != 1:
                raise ValueError('UNIFIED_MARKET_RAW_DAY_AMBIGUOUS')
            candidate = completed_quotes(lines[0], requested={symbol: kinds[symbol]},
                requested_day=target.date(), observed_at=datetime.fromisoformat(
                    quote['received_at']), calendar=calendar)[symbol]
            selected = [reconcile_day(selected[0], candidate)]
        else:
            if after_stamp not in expected_windows:
                expected_windows[after_stamp] = {stamp for stamp in session_ends
                                                if after_stamp is None or stamp > after_stamp}
            expected = expected_windows[after_stamp]
            if {row.bar.timestamp for row in selected} != expected:
                raise ValueError('UNIFIED_MARKET_RAW_MINUTE_WINDOW_INCOMPLETE')
        source_hashes = [source['body_sha256'] for source in lineage_by_code[code]]
        expected_rows = [{**row.row(), 'raw_pack_sha256': manifest['raw']['sha256'],
                          'source_hashes': source_hashes} for row in selected]
        if (not selected or selected[-1].bar.timestamp != target
                or sorted(actual, key=lambda row: row['timestamp']) != expected_rows):
            raise ValueError('UNIFIED_MARKET_RAW_NORMALIZED_REPLAY_MISMATCH')


def _chunks(database: Path, universe: str, windows: dict[str, list[str]]) -> list[dict[str, Any]]:
    if database.is_symlink():
        raise ValueError('UNIFIED_MARKET_DATABASE_SYMLINK')
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True,
                                timeout=5)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        rows: list[dict[str, Any]] = []
        for freq, days in windows.items():
            rows.extend(dict(row) for row in connection.execute('''
                SELECT c.*, b.manifest_sha256 FROM collection_chunks c
                JOIN collection_batches b ON b.batch_id=c.batch_id
                WHERE c.universe_version=? AND c.frequency=? AND b.state='COMPLETE'
                AND substr(c.end_time,1,10)>=? AND substr(c.start_time,1,10)<=?
                AND NOT EXISTS (SELECT 1 FROM excluded_collection_chunks e
                                WHERE e.batch_id=c.batch_id AND e.symbol=c.symbol)
                ORDER BY c.end_time,c.batch_id,c.symbol LIMIT ?
                ''', (universe, freq, days[0], days[-1], MAX_CHUNKS-len(rows)+1)))
            if len(rows) > MAX_CHUNKS:
                raise ValueError('UNIFIED_MARKET_CHUNK_BUDGET')
        return rows


def _verify_selected_snapshot(initial: list[dict[str, Any]],
                              final: list[dict[str, Any]]) -> None:
    before = {tuple(sorted(row.items())) for row in initial}
    after = {tuple(sorted(row.items())) for row in final}
    if not before <= after:
        raise ValueError('UNIFIED_MARKET_LEDGER_OR_MASTER_CHANGED')
    added = [row for row in final if tuple(sorted(row.items())) not in before]
    if not added:
        return
    spans: dict[tuple[str, str], tuple[datetime, datetime]] = {}
    for row in initial:
        key = (row['symbol'], row['frequency'])
        start, end = (datetime.fromisoformat(row[k]) for k in ('start_time', 'end_time'))
        if key in spans:
            start, end = min(start, spans[key][0]), max(end, spans[key][1])
        spans[key] = start, end
    for row in added:
        span = spans.get((row['symbol'], row['frequency']))
        if span is not None and (datetime.fromisoformat(row['start_time']) <= span[1]
                                and datetime.fromisoformat(row['end_time']) >= span[0]):
            # New overlapping admissions can change conflict status or lineage.
            # Read them in the next generation rather than publish an unchecked S.
            raise ValueError('UNIFIED_MARKET_LEDGER_OR_MASTER_CHANGED')


class _SourceReads:
    """Account physical reads across the main thread and one bounded prefetch."""

    def __init__(self, stats: dict[str, Any], deadline: float = float('inf')):
        self._stats, self._deadline = stats, deadline
        self._lock = threading.Lock()
        self._reserved: dict[int, int] = {}

    def check(self) -> None:
        with self._lock:
            if self._stats['source_bytes_read']+sum(self._reserved.values()) > SOURCE_BYTE_BUDGET:
                raise ValueError('UNIFIED_MARKET_SOURCE_BYTE_BUDGET')

    def observe(self, count: int, stage: Literal['before', 'after', 'failed']) -> None:
        with self._lock:
            thread = threading.get_ident()
            if stage == 'before':
                if (self._stats['source_bytes_read']+sum(self._reserved.values())+count
                        > SOURCE_BYTE_BUDGET):
                    raise ValueError('UNIFIED_MARKET_SOURCE_BYTE_BUDGET')
                self._reserved[thread] = count
            else:
                self._reserved.pop(thread, None)
                if stage == 'after':
                    self._stats['source_bytes_read'] += count
                    if (self._stats['source_bytes_read']+sum(self._reserved.values())
                            > SOURCE_BYTE_BUDGET):
                        raise ValueError('UNIFIED_MARKET_SOURCE_BYTE_BUDGET')

    def material(self, store: UnifiedBatchStore, batch_id: str) -> _BatchMaterial:
        if time.monotonic() > self._deadline:
            raise ValueError('UNIFIED_MARKET_TIME_BUDGET')
        self.check()
        path = store.batch_path(batch_id)
        if path.is_symlink() or path.stat().st_size > 2_000_000:
            raise ValueError('UNIFIED_MARKET_SOURCE_MANIFEST_BOUND')
        # The observer reserves verified physical sizes, then accounts actual
        # returned bytes before parse/hash checks, even for failed/unused reads.
        return store._batch_material(batch_id, _read_observer=self.observe)


def _load(store: UnifiedBatchStore, batch_id: str, stats: dict[str, Any],
          calendar: Calendar, kinds: dict[str, str], context: tuple[str, str],
          reader: _CapturedKlineReader | None = None, *,
          _source: _SourceReads | None = None, _prepared: Future[_BatchMaterial] | None = None,
          _receipts: Any = None
          ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source = _source if _source is not None else _SourceReads(stats)
    material = source.material(store, batch_id) if _prepared is None else _prepared.result()
    manifest = material.manifest
    # Hash actual immutable object bytes every pass. Cache skips decompression and
    # Parquet decoding only; neither timestamps nor a previous schema PASS suffice.
    source.check()
    if manifest['normalized'] is None:
        raise ValueError('UNIFIED_MARKET_NORMALIZED_SOURCE_MISSING')

    def recheck_manifest() -> None:
        material.check_manifest_unchanged()
        source.check()

    key = (*material.identities(), *context)
    receipt = None
    if _receipts is not None:
        from qtrade.research.market_validation_receipts import receipt_key
        from scripts.unified_market_checkpoint import identity
        receipt = receipt_key(batch_id, material.identities(), identity(context))
        restored = _receipts.get(receipt, now=datetime.now(UTC))
        if restored is not None and restored[0] == manifest:
            recheck_manifest()
            stats['receipt_hits'] += 1
            return cast(tuple[dict[str, Any], list[dict[str, Any]]], restored)
    if _receipts is None and key in _CACHE:
        recheck_manifest()
        stats['cached_batches'] += 1
        return _CACHE[key]
    material.check_export_bounds()
    checked, _, rows = material.validate()
    with localcontext(Context(prec=28)):
        _replay(material, calendar, kinds, reader)
    recheck_manifest()
    if _receipts is not None:
        _receipts.put_success(receipt, checked, rows, validated_at=datetime.now(UTC))
        stats['receipt_writes'] += 1
        stats['replayed_batches'] += 1
        return checked, rows
    if 'cached_rows' not in stats:
        stats['cached_rows'] = sum(len(value[1]) for value in _CACHE.values())
        stats['cache_estimated_bytes'] = sum(_CACHE_SIZES.get(old, 0) for old in _CACHE)
    cached_rows, cached_bytes = stats['cached_rows'], stats['cache_estimated_bytes']
    if (len(_CACHE) < MAX_BATCHES
            and cached_rows+len(rows) <= MAX_CACHE_ROWS
            and cached_bytes+len(rows)*1024 < MAX_CACHE_ESTIMATED_BYTES):
        estimate = len(encoded({'rows': rows}))+len(rows)*1024
        if cached_bytes+estimate <= MAX_CACHE_ESTIMATED_BYTES:
            _CACHE[key] = (checked, rows)
            _CACHE_SIZES[key] = estimate
            stats['cached_rows'] += len(rows)
            stats['cache_estimated_bytes'] += estimate
    stats['replayed_batches'] += 1
    return checked, rows


def export_market(base: Path, database: Path, *, now: datetime | None = None,
                  seconds: float = 45, receipt_database: Path | None = None,
                  checkpoint_root: Path | None = None) -> dict[str, Any]:
    if (receipt_database is None) != (checkpoint_root is None):
        raise ValueError('UNIFIED_MARKET_CHECKPOINT_PATHS_REQUIRED')
    if receipt_database is None:
        return _export_market(base, database, now=now, seconds=seconds)
    from qtrade.research.market_validation_receipts import MarketValidationReceiptStore
    from scripts.unified_market_checkpoint import publication_lease
    assert checkpoint_root is not None
    with publication_lease(receipt_database), MarketValidationReceiptStore(
            receipt_database, sidecar_root=checkpoint_root/'receipts') as receipts:
        return _export_market(base, database, now=now, seconds=seconds,
                              _receipts=receipts, _checkpoint_root=checkpoint_root)


def _export_market(base: Path, database: Path, *, now: datetime | None = None,
                   seconds: float = 45, _receipts: Any = None,
                   _checkpoint_root: Path | None = None) -> dict[str, Any]:
    if os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
        raise ValueError('UNIFIED_MARKET_LIVE_DISABLED_REQUIRED')
    now = now or datetime.now(UTC)
    if now.tzinfo is None or not 1 <= seconds <= 120:
        raise ValueError('UNIFIED_MARKET_CLOCK_OR_BUDGET_INVALID')
    started = time.monotonic()
    deadline = started+seconds
    publication = base/'unified-data/public/unified.json'
    catalog = validate_publication(json.loads(publication.read_bytes()))
    master = catalog['master']
    targets = [row for row in master['securities'] if row['in_target']]
    if not 1 <= len(targets) <= MAX_TARGETS:
        raise ValueError('UNIFIED_MARKET_TARGET_BOUND')
    calendar = Calendar(master['collection']['calendar'])
    windows = {freq: sessions(calendar, calendar.expected(now,
        'daily' if freq == 'day' else 'minute').date(), 20 if freq == 'day' else 3)
        for freq in ('day', '5min')}
    chunks = _chunks(database, master['universe_version'], windows)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        grouped[chunk['batch_id']].append(chunk)
    if len(grouped) > MAX_BATCHES:
        raise ValueError('UNIFIED_MARKET_BATCH_BUDGET')
    # Keep a stable bounded working set instead of evicting it on every sequential
    # scan. Expired batch entries are evicted; over-budget new entries are not cached.
    for key in list(_CACHE):
        if _CACHE[key][0]['batch_id'] not in grouped:
            del _CACHE[key]
            _CACHE_SIZES.pop(key, None)
    stats: dict[str, Any] = {'source_bytes_read': 0, 'cached_batches': 0, 'replayed_batches': 0,
             'selected_chunks': len(chunks), 'selected_batches': len(grouped),
             'cached_rows': sum(len(value[1]) for value in _CACHE.values()),
             'cache_estimated_bytes': sum(_CACHE_SIZES.get(key, 0) for key in _CACHE)}
    if _receipts is not None:
        stats.update(receipt_hits=0, receipt_writes=0, reused_pairs=0, rebuilt_pairs=0,
                     reused_shards=0)
    identities = {row['symbol']: row for row in targets}
    kinds = {symbol: row['security_type'] for symbol, row in identities.items()}
    context = (signed(calendar.document)['evidence_sha256'], signed(kinds)['evidence_sha256'])
    checkpoint = None
    pair_fingerprints: dict[str, str] = {}
    replay_sources: dict[str, str] = {}
    if _receipts is not None:
        import importlib.metadata
        import platform

        from scripts import unified_market_checkpoint as checkpoints
        replay_paths = (*_WORKER_SOURCES, 'scripts/unified_market_checkpoint.py',
                        'src/qtrade/data/__init__.py',
                        'src/qtrade/market_data/akshare.py',
                        'src/qtrade/market_data/sina_minute.py',
                        'src/qtrade/data/storage.py', 'scripts/atomic_json.py',
                        'src/qtrade/research/market_validation_receipts.py')
        replay_sources = {name: file_hash(REPO/name) for name in replay_paths}
        context = (checkpoints.identity({'calendar': calendar.document,
                    'sources': replay_sources, 'python': platform.python_version(),
                    'arrow': importlib.metadata.version('pyarrow'),
                    'decimal': str(Context(prec=28)),
                    'price_basis': PRICE_BASIS}), context[1])
        assert _checkpoint_root is not None
        durable_head = base/'unified-data/market-public/unified-market.json'
        if durable_head.exists():
            # A previous atomic replace may have succeeded before its directory
            # fsync failed. Establish namespace durability before cache recovery.
            try:
                _fsync_directory(durable_head.parent)
            except OSError as error:
                raise MarketPublicationUncertain('UNIFIED_MARKET_COMMIT_UNCERTAIN') from error
        checkpoint = checkpoints.load(_checkpoint_root,
            base/'unified-data/market-public/unified-market.json', checkpoints.identity(context))
        pair_fingerprints = checkpoints.fingerprints(chunks, targets,
                                                     checkpoints.identity(context), windows)
    public_head = base/'unified-data/market-public/unified-market.json'
    initial_head_hash = (file_hash(public_head)
                         if _receipts is not None and public_head.exists() else None)
    reused = {} if checkpoint is None else {
        (entry['symbol'], entry['frequency']): entry for entry in checkpoint['index']['entries']
        if entry['state'] == 'AVAILABLE' and checkpoint['fingerprints'].get(
            entry['symbol']+':'+entry['frequency']) == pair_fingerprints.get(
            entry['symbol']+':'+entry['frequency'])}
    reader = _CapturedKlineReader(calendar)
    ordered_bar_fields = tuple(key for key in sorted(BAR_FIELDS) if key != 'amount_basis')
    collected: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    sources: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    problems: dict[tuple[str, str], set[str]] = defaultdict(set)
    store = UnifiedBatchStore(base/'unified-data/lake')
    source = _SourceReads(stats, deadline)
    def collect_batch(batch_id: str, selected: list[dict[str, Any]],
                      prepared: Future[_BatchMaterial]) -> None:
        if time.monotonic() > deadline:
            raise ValueError('UNIFIED_MARKET_TIME_BUDGET')
        try:
            receipt_arguments = {} if _receipts is None else {'_receipts': _receipts}
            manifest, rows = _load(store, batch_id, stats, calendar, kinds, context, reader,
                                   _source=source, _prepared=prepared, **receipt_arguments)
            by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                by_symbol[row['symbol']].append(row)
            spec = manifest['spec']
            for chunk in selected:
                symbol, freq = chunk['symbol'], chunk['frequency']
                if symbol not in identities:
                    raise ValueError('UNIFIED_MARKET_NON_TARGET_CHUNK')
                outcome = manifest['outcomes'][symbol]
                selected_rows = by_symbol[symbol]
                if (manifest['evidence_sha256'] != chunk['manifest_sha256']
                        or spec['universe_version'] != master['universe_version']
                        or spec['frequency'] != freq or outcome['state'] != 'SUCCEEDED'
                        or outcome['bar_count'] != chunk['bar_count']
                        or len(selected_rows) != chunk['bar_count']
                        or min(datetime.fromisoformat(row['timestamp']) for row in selected_rows)
                        != datetime.fromisoformat(chunk['start_time'])
                        or max(datetime.fromisoformat(row['timestamp']) for row in selected_rows)
                        != datetime.fromisoformat(chunk['end_time'])
                        or datetime.fromisoformat(outcome['start']) != datetime.fromisoformat(
                            chunk['start_time'])
                        or datetime.fromisoformat(outcome['end']) != datetime.fromisoformat(
                            chunk['end_time'])
                        or manifest['price_basis'] != PRICE_BASIS
                        or datetime.fromisoformat(manifest['completed_at']) > now):
                    raise ValueError('UNIFIED_MARKET_CHUNK_SOURCE_MISMATCH')
                if (symbol, freq) in reused:
                    continue  # Immutable receipt and current source bytes were checked above.
                for original in selected_rows:
                    if (original['completion_status'] != 'CLOSED'
                            or datetime.fromisoformat(original['timestamp'])
                            > datetime.fromisoformat(spec['target_end'])):
                        raise ValueError('UNIFIED_MARKET_UNCLOSED_SOURCE')
                    # Byte identities must survive a new process/hash seed.
                    row = {key: original[key] for key in ordered_bar_fields}
                    row['amount_basis'] = original['amount_basis']
                    day = str(datetime.fromisoformat(row['timestamp']).astimezone(SHANGHAI).date())
                    if day not in windows[freq]:
                        # Retained rows receive this full check in validate_shard below.
                        # Discarded rows must still be checked before leaving this batch.
                        validate_bar(row, symbol, identities[symbol]['security_type'], freq,
                                     calendar, now)
                        continue
                    key = (symbol, freq)
                    old = collected[key].get(row['timestamp'])
                    if old is not None and old != row:
                        problems[key].add('CONFLICTING_ACCEPTED_BARS')
                    collected[key][row['timestamp']] = row
                    sources[(symbol, freq, day if freq == '5min' else 'window')][batch_id] = {
                        'batch_id': batch_id,
                        'manifest_sha256': manifest['evidence_sha256'],
                        'raw_sha256': manifest['raw']['sha256'],
                        'normalized_sha256': manifest['normalized']['sha256']}
        except OSError:
            raise  # Transient storage faults cannot become sealed permanent exclusions.
        except (ValueError, KeyError, TypeError) as error:
            if isinstance(error, ValueError) and 'BUDGET' in str(error):
                raise
            for chunk in selected:
                problems[(chunk['symbol'], chunk['frequency'])].add('SOURCE_REPLAY_FAILED')
    objects: dict[str, bytes] = {}
    entries: list[dict[str, Any]] = []
    destination = base/'unified-data/market-public'

    sealed: dict[tuple[str, str], dict[str, Any]] = {}

    def seal_pair(symbol: str, freq: str) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
        target = identities[symbol]
        key = (target['symbol'], freq)
        entry = {field: target[field] for field in ('symbol', 'instrument_id',
                                                   'security_type', 'name')}
        entry.update(frequency=freq, state='NO_DATA', issues=sorted(problems[key]),
                     shards=[])
        sealed[key] = entry
        if problems[key]:
            entry['state'] = 'QUARANTINED'
        elif key in reused:
            previous = reused[key]
            stats['reused_pairs'] += 1
            for ref in previous['shards']:
                path = destination/'unified-market/objects'/(ref['sha256']+'.json')
                if path.is_symlink() or path.stat().st_size != ref['bytes']:
                    raise ValueError('UNIFIED_MARKET_IMMUTABLE_OBJECT_CHANGED')
                raw = path.read_bytes()
                if sha256(raw).hexdigest() != ref['sha256']:
                    raise ValueError('UNIFIED_MARKET_IMMUTABLE_OBJECT_CHANGED')
                if ref['sha256'] not in objects:
                    objects[ref['sha256']] = raw
                stats['reused_shards'] += 1
            entry.update(state='AVAILABLE', shards=previous['shards'])
        else:
            if _receipts is not None:
                stats['rebuilt_pairs'] += 1
            groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in collected[key].values():
                groups[row['trading_day'] if freq == '5min' else 'window'].append(row)
            for group, rows in sorted(groups.items()):
                rows.sort(key=lambda row: datetime.fromisoformat(row['timestamp']))
                refs = list(sources[(target['symbol'], freq, group)].values())
                shard = signed({**AUTHORITY,
                    'schema_version': 'qtrade.unified-market-shard.v1',
                    'universe_version': master['universe_version'], **{field: target[field]
                    for field in ('symbol', 'instrument_id', 'security_type')},
                    'frequency': freq, 'price_basis': PRICE_BASIS, 'bars': rows,
                    'source_batches': sorted(refs, key=lambda row: row['batch_id'])})
                yield entry, shard



    # Count every selected chunk, including repeated chunks for one pair. A pair
    # can seal only after its complete batch outcomes and all conflicts are known.
    remaining = Counter((chunk['symbol'], chunk['frequency']) for chunk in chunks)

    def prepare_batch(batch_id: str) -> _BatchMaterial:
        material = source.material(store, batch_id)
        # Exactly one bounded material is prepared ahead. Arrow/gzip decoding
        # can overlap source replay and shard I/O; admission and cache ownership
        # stay on the main thread. Warm entries still rehash every physical blob.
        if (_receipts is None and material.manifest['normalized'] is not None
                and (*material.identities(), *context) not in _CACHE):
            material.check_export_bounds()
            material.validate()
        return material

    def build_shards() -> Generator[tuple[dict[str, Any], dict[str, Any]], None, None]:
        # Cohorts finish early enough for immutable I/O to overlap later source
        # replay. Source membership is unchanged; bars, refs and entries are sorted.
        batches = sorted(grouped.items(), key=lambda item: (
            tuple(sorted(str(chunk['symbol']) for chunk in item[1])), str(item[0])))
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='market-source') as pool:
            prepared = pool.submit(prepare_batch, batches[0][0]) if batches else None
            try:
                for position, (batch_id, selected) in enumerate(batches):
                    assert prepared is not None
                    finished, _ = wait({prepared}, timeout=max(0, deadline-time.monotonic()))
                    if not finished or time.monotonic() > deadline:
                        raise ValueError('UNIFIED_MARKET_TIME_BUDGET')
                    current = prepared
                    # Its read/decode exception is consumed by the same batch;
                    # it cannot admit rows or update the main-thread cache.
                    prepared = (pool.submit(prepare_batch, batches[position+1][0])
                                if position+1 < len(batches) else None)
                    collect_batch(batch_id, selected, current)
                    # Mark all selected pairs on failure before these counts decrease.
                    for chunk in selected:
                        key = (chunk['symbol'], chunk['frequency'])
                        remaining[key] -= 1
                        if remaining[key] == 0 and key not in sealed and key[0] in identities:
                            yield from seal_pair(*key)
            finally:
                if prepared is not None:
                    prepared.cancel()

    worker_sources = _worker_inventory() if _parallel_shards(len(targets)) else None

    def build_objects() -> Iterator[tuple[str, bytes]]:
        entries_pending: deque[dict[str, Any]] = deque()
        def shards() -> Iterator[dict[str, Any]]:
            with closing(build_shards()) as pending:
                for entry, shard in pending:
                    entries_pending.append(entry)
                    yield shard
        built_bytes = 0
        with closing(_encode_shards(shards(), calendar, now, deadline,
                                   worker_sources is not None)) as validated:
            for shard, raw in validated:
                entry = entries_pending.popleft()
                rows = shard['bars']
                digest = sha256(raw).hexdigest()
                entry['shards'].append({'sha256': digest,
                    'evidence_sha256': shard['evidence_sha256'], 'bytes': len(raw),
                    'start': rows[0]['timestamp'], 'end': rows[-1]['timestamp'],
                    'count': len(rows)})
                entry['state'] = 'AVAILABLE'
                if digest not in objects:
                    built_bytes += len(raw)
                    if built_bytes > TOTAL_LIMIT:
                        raise ValueError('UNIFIED_MARKET_EXPORT_BUDGET')
                    objects[digest] = raw
                    yield digest, raw
        if entries_pending:
            raise ValueError('UNIFIED_MARKET_INCOMPLETE_SHARD_ACCOUNTING')

    # All worker processes have exited normally before object directory sync,
    # final source/ledger checks and the index commit. Failures retain the pointer.
    _persist_objects(destination, build_objects(), deadline)
    if any(remaining.values()):
        raise ValueError('UNIFIED_MARKET_INCOMPLETE_CHUNK_ACCOUNTING')
    for target in sorted(targets, key=lambda row: row['symbol']):
        for freq in ('day', '5min'):
            key = (target['symbol'], freq)
            if key not in sealed:
                entry = {field: target[field] for field in ('symbol', 'instrument_id',
                                                           'security_type', 'name')}
                entry.update(frequency=freq, state='NO_DATA', issues=sorted(problems[key]),
                             shards=[])
                sealed[key] = entry
            entries.append(sealed[key])
    total = sum(len(raw) for raw in objects.values())
    if total > TOTAL_LIMIT or time.monotonic() > deadline:
        raise ValueError('UNIFIED_MARKET_EXPORT_BUDGET')
    # New chunks may arrive while replay runs; excluded/changed selected chunks may not.
    final = {tuple(sorted(row.items())) for row in _chunks(database, master['universe_version'],
                                                          windows)}
    if (any(tuple(sorted(row.items())) not in final for row in chunks)
            or json.loads(publication.read_bytes())['master']['universe_version'] != master[
                'universe_version']):
        raise ValueError('UNIFIED_MARKET_LEDGER_OR_MASTER_CHANGED')
    index = signed({**AUTHORITY, 'schema_version': 'qtrade.unified-market-index.v1',
        'universe_version': master['universe_version'], 'master_sha256': master['evidence_sha256'],
        'published_at': now.isoformat(), 'calendar': calendar.document, 'windows': windows,
        'entries': entries, 'total_bytes': total, 'total_files': len(objects),
        'limitations': LIMITATIONS, 'implementation_sources': {name: file_hash(REPO/name)
            for name in ('scripts/publish_unified_market.py',
                         'src/qtrade/research/unified_market.py')}})
    validate_index(index, now)
    if time.monotonic() > deadline:
        raise ValueError('UNIFIED_MARKET_TIME_BUDGET')
    if worker_sources is not None and _worker_inventory() != worker_sources:
        raise ValueError('UNIFIED_MARKET_WORKER_PARENT_SOURCE_CHANGED')
    if _receipts is not None:
        if any(file_hash(REPO/name) != digest for name, digest in replay_sources.items()):
            raise ValueError('UNIFIED_MARKET_WORKER_PARENT_SOURCE_CHANGED')
        assert _checkpoint_root is not None
        if (file_hash(public_head) if public_head.exists() else None) != initial_head_hash:
            raise ValueError('UNIFIED_MARKET_CHECKPOINT_HEAD_CHANGED')
        stats['checkpoint_id'] = checkpoints.prepare(_checkpoint_root, index,
            checkpoints.identity(context), pair_fingerprints,
            None if checkpoint is None else checkpoint['index']['evidence_sha256'], now)
        # Preparing a durable NFS generation may block. Recheck after it, not
        # just before it, so a changed head or exclusion cannot be overwritten.
        _verify_selected_snapshot(chunks, _chunks(database, master['universe_version'], windows))
        current_master = validate_publication(json.loads(publication.read_bytes()))['master']
        if (current_master['universe_version'] != master['universe_version']
                or current_master['collection']['calendar'] != master['collection']['calendar']
                or {row['symbol']: {key: row[key] for key in ('instrument_id',
                        'security_type', 'name')} for row in current_master['securities']
                    if row['in_target']} != {symbol: {key: row[key] for key in (
                        'instrument_id', 'security_type', 'name')}
                        for symbol, row in identities.items()}):
            raise ValueError('UNIFIED_MARKET_LEDGER_OR_MASTER_CHANGED')
        if any(file_hash(REPO/name) != digest for name, digest in replay_sources.items()):
            raise ValueError('UNIFIED_MARKET_WORKER_PARENT_SOURCE_CHANGED')
        if (file_hash(public_head) if public_head.exists() else None) != initial_head_hash:
            raise ValueError('UNIFIED_MARKET_CHECKPOINT_HEAD_CHANGED')
        if time.monotonic() > deadline:
            raise ValueError('UNIFIED_MARKET_TIME_BUDGET')
    try:
        write_json_atomic(destination/'unified-market.json', index)
    except OSError as error:
        # Do not copy a possibly non-durable new pointer to Git as a retained
        # last success. A later pass must reestablish durability and revalidate.
        raise MarketPublicationUncertain('UNIFIED_MARKET_COMMIT_UNCERTAIN') from error
    stats.update(export_seconds=round(time.monotonic()-started, 6), total_bytes=total,
                 total_files=len(objects), index_id=index['evidence_sha256'],
                 cached_rows=sum(len(value[1]) for value in _CACHE.values()),
                 cache_estimated_bytes=sum(_CACHE_SIZES.get(key, 0) for key in _CACHE),
                 quarantined_entries=sum(row['state'] == 'QUARANTINED' for row in entries))
    if _receipts is not None:
        stats['receipt_sidecar_bytes_read'] = _receipts.sidecar_bytes_read
    write_json_atomic(base/'unified-data/market-export-state.json', signed({**AUTHORITY, **stats}))
    return {'index': index, 'statistics': stats}


def main() -> None:
    if sys.argv[1:] == ['--private-shard-worker']:
        raise SystemExit(_shard_worker())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export_market(args.base, args.database)['statistics']))


def publish_market_files(base: Path, publication: Path,
                         database: Path | None = None, *,
                         seed_existing: bool = False, state_root: Path | None = None) -> list[str]:
    """Use the existing public Git queue; a failed export retains its dated last success."""
    if seed_existing and database is not None:
        raise ValueError('UNIFIED_MARKET_DELTA_SEED_DATABASE_FORBIDDEN')
    if state_root is not None and (database is None or seed_existing):
        raise ValueError('UNIFIED_MARKET_CHECKPOINT_DATABASE_REQUIRED')

    def checked(root: Path, name: str) -> Path:
        path = root/name
        if root.is_symlink():
            raise ValueError('UNIFIED_MARKET_PUBLIC_PATH_INVALID')
        current = root
        for part in Path(name).parts:
            current = current/part
            if current.is_symlink():
                raise ValueError('UNIFIED_MARKET_PUBLIC_PATH_INVALID')
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('UNIFIED_MARKET_PUBLIC_PATH_INVALID')
        return path

    if database is not None:
        started_at = datetime.now(UTC)
        started = time.monotonic()
        attempt: dict[str, Any] = {**AUTHORITY,
            'schema_version': 'qtrade.unified-market-export-attempt.v1',
            'started_at': started_at.isoformat(), 'state': 'FAILED',
            'index_id': None, 'error_class': None, 'error_code': None}
        try:
            checkpoint_arguments: dict[str, Any] = {} if state_root is None else {
                'receipt_database': state_root/'validation.sqlite',
                'checkpoint_root': base/'unified-data/market-checkpoints'}
            result = export_market(base, database, **checkpoint_arguments)
            attempt.update(state='SUCCEEDED', index_id=result['index']['evidence_sha256'])
        except MarketPublicationUncertain:
            attempt.update(state='UNCERTAIN', error_class='MarketPublicationUncertain',
                           error_code='UNIFIED_MARKET_COMMIT_UNCERTAIN')
            print('UNIFIED_MARKET_COMMIT_UNCERTAIN_STOPPED', flush=True)
            raise
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            attempt['error_class'] = type(error).__name__
            if str(error) in {'UNIFIED_MARKET_TIME_BUDGET', 'UNIFIED_MARKET_EXPORT_BUDGET',
                    'UNIFIED_MARKET_SOURCE_BYTE_BUDGET', 'UNIFIED_MARKET_CHUNK_BUDGET',
                    'UNIFIED_MARKET_BATCH_BUDGET', 'UNIFIED_MARKET_LEDGER_OR_MASTER_CHANGED',
                    'UNIFIED_MARKET_IMMUTABLE_OBJECT_CHANGED'}:
                attempt['error_code'] = str(error)
            print('UNIFIED_MARKET_EXPORT_FAILED_RETAINING_LAST_SUCCESS', flush=True)
        finally:
            attempt.update(completed_at=datetime.now(UTC).isoformat(),
                           elapsed_seconds=round(time.monotonic()-started, 6))
            write_json_atomic(base/'unified-data/market-export-attempt.json', signed(attempt))
    root = publication if seed_existing else base/'unified-data/market-public'
    path = checked(root, 'unified-market.json')
    pointer = checked(publication, 'unified-market.json')
    if not path.is_file():
        if seed_existing or path.exists():
            raise ValueError('UNIFIED_MARKET_PUBLIC_INDEX_INVALID')
        write_json_atomic(pointer, signed({**AUTHORITY,
            'state': 'NO_VERIFIED_UNIFIED_MARKET_PUBLICATION'}))
        return ['unified-market.json']
    if path.is_symlink() or path.stat().st_size > 4_000_000:
        raise ValueError('UNIFIED_MARKET_PUBLIC_INDEX_INVALID')
    index = validate_index(json.loads(path.read_bytes()))
    names = []
    for entry in index['entries']:
        for ref in entry['shards']:
            name = 'unified-market/objects/'+ref['sha256']+'.json'
            source, target = checked(root, name), checked(publication, name)
            if (source.is_symlink() or not source.resolve().is_relative_to(root.resolve())
                    or source.stat().st_size != ref['bytes'] or file_hash(source) != ref['sha256']):
                raise ValueError('UNIFIED_MARKET_PUBLIC_OBJECT_INVALID')
            if not target.resolve().is_relative_to(publication.resolve()):
                raise ValueError('UNIFIED_MARKET_PUBLIC_DESTINATION_ESCAPE')
            if target.exists():
                if target.is_symlink() or file_hash(target) != ref['sha256']:
                    raise ValueError('UNIFIED_MARKET_PUBLIC_OBJECT_CONFLICT')
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            names.append(name)
            # Stable identity names let Git select the previous shard as a delta
            # base. They are transport hints only; consumers still use hash paths.
            group = 'day'
            if entry['frequency'] == '5min':
                start = datetime.fromisoformat(ref['start']).astimezone(SHANGHAI).date()
                end = datetime.fromisoformat(ref['end']).astimezone(SHANGHAI).date()
                if start != end:
                    raise ValueError('UNIFIED_MARKET_DELTA_SESSION_INVALID')
                group = start.strftime('%Y%m%d')
            identity = f"{entry['symbol']}|{entry['frequency']}|{group}"
            suffix = sha256(identity.encode()).hexdigest()[:16]
            prefix = 'M' if entry['frequency'] == '5min' else 'D'
            alias_name = f"unified-market/.delta/{group}/{prefix}{entry['symbol']}-{suffix}.json"
            alias = checked(publication, alias_name)
            if alias.exists() and not alias.is_file():
                raise ValueError('UNIFIED_MARKET_DELTA_ALIAS_INVALID')
            if not alias.exists() or not alias.samefile(target):
                alias.parent.mkdir(parents=True, exist_ok=True)
                temporary = alias.with_name('.'+alias.name+'.'+uuid4().hex+'.tmp')
                try:
                    os.link(target, temporary)
                    os.replace(temporary, alias)
                finally:
                    temporary.unlink(missing_ok=True)
            names.append(alias_name)
    if not seed_existing:
        write_json_atomic(pointer, index)
    return ['unified-market.json', *sorted(set(names))]


if __name__ == '__main__':
    main()
