# Previous-session incremental gap repair

This is an opt-in maintenance-window operator, not an unattended repair service.
It does not stop or restart maintenance, publish data, or change freshness rules.
Production apply remains deferred pending operator review and an explicitly
quiescent maintenance owner. A `WAITING` service whose owner process is alive is
insufficient: the ordinary queue's process ownership must remain exclusive.

The source/gap audit found that the ordinary collector claims the currently
required trading day only. Its separate monthly BaoStock history queue cannot
repair Sina incremental chunks or substitute its different price basis. No vn.py
gateway supplies this missing observation recovery. This command reuses the
existing `collect_batch`, immutable `UnifiedBatchStore`, collection queue claim and
finish methods, current-work priority checks and nonblocking shared vendor lease.

Read-only plan; no network, queue construction, lease acquisition or data writes:

```powershell
$env:PYTHONPATH='E:/QTrade-git/src;E:/QTrade-git'
python -m scripts.repair_incremental_gaps --base BASE --database DATABASE --source-revision REVIEWED_40_HEX_REVISION
```

During an explicitly authorized maintenance window, the same command with
`--apply --max-batches 2` attempts at most two one-symbol batches. The maximum is
three batches per invocation. Existing noncommercial acknowledgement and
`LIVE_TRADING_ENABLED=false` are required. The command never releases or bypasses
another process's ownership, unresolved RUNNING tasks, a vendor lease, a current
priority block, source backoff or the 100 GiB storage reserve. Stale/missing current
priority evidence blocks apply. It does not refresh that evidence itself.
The existing `failure_streak_v1` migration marker and integer, non-null failure
streak column are mandatory. A legacy database is rejected read-only, including
when there are no repair candidates; this command never runs a global migration.

Only existing tasks in the fixed published universe are eligible, for the three
previous open sessions in the repository calendar. Today and future sessions are
always excluded, including after today's close. Existing task retry not-before
times are retained. For a due incomplete task, the desired boundary can advance to
that prior session's 15:00 close; already accepted rows and immutable batches are
preserved. Rows are admitted strictly after its existing accepted boundary.
Task reads are filtered in SQLite to the same universe, minute frequency and at
most three eligible dates; growing older history is outside that query.

The same fixed Sina K-line endpoint requests 240 trailing five-minute rows, below
the existing 1,970-row parser/provider bound. This covers three prior sessions,
today's possible suffix and one session of margin. The existing validator isolates
the exact prior day, enforces the completed grid and OHLCV gates, and requires
durable raw capture before parsing. Partial source responses create failed batch
receipts and normal backoff; they never advance accepted coverage. No missing bar,
amount, suspension reason or historical availability is invented.

The signed JSON result states `PLAN`, `REPAIRED`, `UNRESOLVED` or `BLOCKED` and
includes exact new batch identities. A crash after claim leaves the ordinary
durable RUNNING batch for the existing collector's recovery mechanism; this tool
refuses to take over such work. A network response can remain incomplete or outside
the bounded trailing window. Partial earlier sessions and sessions older than the
three-session scope remain explicit limitations. A bounded `REPAIRED` result does
not certify history, PIT, current freshness, strategy admission or whole-universe
coverage. Public publication remains a separate existing operation.

Local reproduction:

```powershell
python -m pytest tests/test_repair_incremental_gaps.py -q
python -m ruff check scripts/repair_incremental_gaps.py tests/test_repair_incremental_gaps.py
```

Tests use real temporary SQLite queues and raw/Parquet batch storage with synthetic
source responses, never a vendor network. Windows substitutes unavailable directory
fsync only in the existing shared storage fixture; Linux integration must verify
the production fsync path before deployment.
