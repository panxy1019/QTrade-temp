# Unified collection latency and fair progress

## Scope and source audit

The observed October 27 collector used four concurrent `urllib` requests but
opened a new HTTP connection for each symbol. Cluster samples supplied during
this change show 25-symbol batches taking 6–16 seconds (median 8); receiving the
last body to sealing the batch took less than 0.2 seconds. This identifies a
network bottleneck rather than evidence to weaken NAS durability. It does not
establish that the entire 1,500-security pool meets a five-minute deadline.

The existing Sina adapter, incremental Bar validators, immutable raw/Parquet
store and SQLite queue remain the source and admission boundary. vn.py gateways
do not supply reusable connections to these public research endpoints or repair
their source latency. The declared `akshare` extra already includes HTTPX;
the inspected cluster environment has HTTPX 0.28.1. No new package is required.

## Change

One pass owns an HTTPX client with at most four connections and four retained
connections, 30-second idle retention, verified TLS and a 12-second timeout.
Four download workers remain the ceiling. There are no automatic retries or
redirects. Each response is streamed with the existing 2,000,000-byte cap;
incomplete responses retain received bytes and remain inadmissible. HTTP error
bodies remain archived. `Accept-Encoding: identity` preserves the parser's
existing uncompressed body contract. Standalone `fetch` remains available for
compatibility and a bounded before/after transport probe.

Before each new batch the collector checks the exchange calendar. A changed
completed grid updates unclaimed tasks without restarting the pass or changing
an already claimed batch's target. Each frequency still receives one bounded
batch per turn, so daily tasks continue alongside minute work after close.
Within each fresh/retry quota, never-collected symbols and then the oldest
completed end take priority. A newer grid cannot keep recycling a just-completed
prefix ahead of the remaining pool. Existing retry quotas/backoff, interrupted
batch recovery, successful checkpoints and the 100 GiB reserve stay in force.

The signed operational progress adds cumulative `phase_seconds` and the latest
batch's `last_batch_timings`: download, raw persistence, parse, commit, queue
finish, queue snapshot, progress write and publication. These are wall durations,
not admission evidence. A snapshot contains timing from writes/publications
already completed; its own write is visible on the subsequent snapshot. Requested,
downloaded, validated and admitted counters retain their separate meanings.
Immutable receipt schemas and source observation clocks are unchanged.

## Local reproduction

PowerShell, from the repository root:

```powershell
$env:PYTHONPATH='E:/QTrade-git/src;E:/QTrade-git'
$env:MYPYPATH=$env:PYTHONPATH
python -m pytest tests/test_collect_unified_data.py tests/test_unified_queue.py tests/test_unified_transport.py tests/test_unified_maintenance.py tests/test_unified_store.py --basetemp=E:/lat-final -q
python -m ruff check scripts/collect_unified_data.py scripts/unified_queue.py scripts/benchmark_unified_transport.py tests/test_collect_unified_data.py tests/test_unified_transport.py tests/test_unified_queue.py
python -m mypy --follow-imports=silent --explicit-package-bases scripts/collect_unified_data.py scripts/unified_queue.py scripts/benchmark_unified_transport.py
```

Tests use a real loopback HTTP/1.1 server to prove connection reuse across
batches with at most four accepted connections, including concurrent requests.
They cover exact-size/oversized bodies, HTTP 429, redirects and mid-body failure.
The dynamic-grid integration test uses actual queue transactions and immutable
Parquet, and verifies disjoint symbol batches, unchanged sealed clocks, real
admitted rows and reloadable raw/normalized hashes. Queue regression tests cover
advancing grids, preserved old success, interrupted recovery and retry fairness.
Windows store fixtures substitute unsupported directory fsync; Linux acceptance
must exercise the actual directory fsync and immutable-file implementation.

## Bounded cluster probe and acceptance

From an isolated reviewed source checkout using the existing cluster Python:

```sh
PYTHONPATH=src:. .venv/bin/python -m scripts.benchmark_unified_transport > /tmp/unified-transport-probe.json
```

The probe makes exactly 16 public GET attempts: the same eight symbols once
through `urllib`, then once through the pooled transport, with four workers in
each arm. It writes no collection ledger, model, publication or service state.
Redirect stdout to retain the actual response bytes (base64), source times and
body hashes. Summarize `results[].mode`, `seconds`, `requested` and `downloaded`;
the probe does not parse or admit market rows. Run once; do not loop against the
vendor. This small sequential A/B is sensitive to network/vendor drift and is
evidence for this host and time only.

After independent review and an explicitly scheduled collector-only rollout,
compare complete pass `phase_seconds`, accepted rows, per-symbol completed ends
and newest expected grid against the previous pass. Verify that the oldest end
advances throughout the pool, that failures retain prior successes, and that
daily work still completes after close. Full-pool freshness requires actual
accepted grid coverage and successful publication; throughput/request counts
alone cannot satisfy it. A service restart or production configuration change
was not performed by this implementation task.

The vendor may close connections or delay updates; pooling cannot guarantee a
five-minute all-pool SLA. Source outages, suspended/invalid symbols and publication
transport remain independent constraints. Public history, original raw evidence,
PIT limitations and `LIVE_TRADING_ENABLED=false` are unaffected. No historical
availability, paper recovery or external trading authorization is created.

## Consecutive-failure backoff repair

The October 27 rollout exposed a separate delay: `attempts` counts every claimed
request, including successful updates, but the old retry formula treated it as
the number of consecutive failures. A single transient failure after several
successful grids therefore waited much longer than its intended 60 seconds.

`attempts` remains the cumulative audit count. `consecutive_failures` is a new
queue column: successful admission resets it to zero, failed admission advances
it, and an interrupted attempt preserves it. It saturates at nine because all
higher values have the same existing retry delay. The retained schedule is
60, 120, 240 seconds and so on up to the existing 15,360-second exponential cap;
the former 21,600-second outer bound remains unchanged. This does not shorten a
genuine continuous source outage to a first-failure retry.

On first queue ownership after upgrade, one transaction adds the column and
records `collection_metadata.failure_streak_v1`. For legacy rows with a last
success, it verifies the successful batch's identity and counts up to nine later
`COMPLETE` batches containing the same symbol, universe, frequency and trading
date. Existing success metadata proves these later completions were failures.
Receipt hashes, aware completion clocks, chronology and the cumulative attempt
count must be consistent. Missing proof retains a conservative count and the
existing retry deadline; rows without any success retain the conservative old
attempt count. Batch receipts, source data and old successful checkpoints are
never rewritten.

A legacy failed deadline is repaired only if it exactly equals the actual last
failed batch's `completed_at` plus the former cumulative-attempt formula. Only
then can the proven streak replace that formula. The repaired deadline is based
on that recorded completion time, even when already past; an unexplained or
custom deadline is preserved. The migration marker records counts of examined
tasks, proven streaks, repaired deadlines and conservative fallbacks, and prevents
reapplying the repair on later queue construction.

The focused queue tests include legacy databases with the new column removed,
scope-mismatched noise batches, capped streaks, one-time migration, custom and
unproven deadlines, source recovery and interrupted-process recovery. Production
migration and the resulting actual source retries require the parent's separate
review and deployment; this implementation task does not operate the service.
