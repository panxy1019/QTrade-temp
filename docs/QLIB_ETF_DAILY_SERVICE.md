# Daily ETF native-close diagnostic service

This standalone service completes automatic daily scheduling for the existing ETF
close diagnostic. It does not change the Qlib worker, publisher, API or gateway.
`LIVE_TRADING_ENABLED=false` remains mandatory. The service is implemented locally;
no new deployment, actual daily transition or source collection is claimed here.

## Source and reuse audit

| Existing component | Reuse and remaining gap |
| --- | --- |
| `Calendar` and observed security master | Select the latest completed exchange day and 1–50 observed target ETFs; freeze the signed master for that day |
| `run_etf_trend.run` | Same bounded Sina daily kline requests and immutable attempt/receipt; an optional source callback adds request-by-request scheduling gates |
| `UnifiedBatchStore` | Preserve each actual response in a content-addressed raw pack before another query; preserve the final replayable aggregate receipt |
| `verify_etf_archive` | Reparse the real raw bodies on their original observation clock and compare the exact signed diagnostic before publication |
| Current collection service/progress | Require a fresh `WAITING` heartbeat and signed `PASS_FINISHED` progress for the required day; current collection takes priority |
| Existing daily/version service | Adapt its local Linux lease, bounded alarm and immutable scheduling-intent pattern; no additional research-worker job or data-version ledger writer |
| vn.py/execution components | No role at this data-only boundary; no portfolio, order, gateway or brokerage access is added |

The historical BaoStock/PIT backoff files refer to another vendor and are not
misrepresented as Sina endpoint health. The ETF service respects current Sina
HTTP/transport retry deadlines and keeps its own six-hour source failure cooldown.
It never changes the collector's queue, backoff or process owner.

## Configuration and reproduction

Linux is required for the supervised CLI's local `flock` leases and hard
`SIGALRM`. Keep `--local-root` on a durable local filesystem, preserve it across
restarts and releases, and run exactly one configured service. The input/output
base may use the existing research storage, subject to its current atomic-file
and content-store requirements. Supply the full reviewed 40-character release
revision; this argument is recorded provenance, not an independent source-code
inventory certification.

Required environment and one bounded cycle:

```bash
export LIVE_TRADING_ENABLED=false
export QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true
export PYTHONPATH="$PWD/src:$PWD"
python -m scripts.run_etf_trend_service \
  --base "$B" \
  --local-root /root/qtrade-local/etf-trend-service \
  --master "$B/unified-data/catalog/latest.json" \
  --source-revision "$REVIEWED_FULL_RELEASE_SHA" \
  --once
```

Omit `--once` for the continuous supervised process. The default master is already
`<base>/unified-data/catalog/latest.json`, so `--master` is optional. The service
checks once every 300 seconds after each cycle, with no catch-up burst. Each
cycle has a 900-second hard deadline. The existing source request has a 12-second
socket timeout and a 2 MB response limit; at most 50 ETF queries are possible.

Before the first query and every following query, the service checks:

- the same completed exchange day;
- free space of at least 100 GiB plus 128 MiB of write headroom;
- its persistent Sina cooldown;
- a current collector heartbeat no older than 300 seconds, state `WAITING`, and
  `live_trading_enabled=false`;
- signed collection progress no older than 300 seconds, state `PASS_FINISHED`,
  and the required complete day;
- no running current collection task or pending HTTP/transport source retry.

A source failure receives a six-hour cooldown immediately after its raw response
is durable. The next query is blocked. A current collector's due source retry
also takes priority until it has actually run. Missing, corrupt, stale or future
gate evidence fails closed and does not consume a daily source attempt.

## Immutable daily state and publication

The local root contains `cycle.json`, `source-backoff.json`, process/cycle leases,
and `days/YYYY-MM-DD/{master,request,attempt,completed,failed}.json` as applicable.
Each day freezes one master, source revision and diagnostic identity before any
source request. Once `attempt.json` exists, that day cannot automatically issue a
second source burst, including after an interrupted process, source failure,
master refresh or code change.

Each source response also has an immutable archive
`source-receipts/NN.json` pointing to its raw pack. A priority change can interrupt
the burst after the current request, while retaining all already received bytes.
At most one in-flight HTTP request can overlap a newly starting higher-priority
collector; the service does not claim atomic exclusion with that existing
collector, which does not participate in an ETF lease.

A complete archived receipt can be reprocessed with `collect=False` after a
same-day crash. A completed diagnostic can be replayed after a failed publication
without another network request. A partial or uncertain attempt requires explicit
operator review; there is no automatic same-day repair or retry policy. Preserve
its markers and raw bytes instead of deleting them to trigger another burst.

Only exact raw replay and `verify_etf_archive` success permit an immutable daily
completion and the signed pointer:

```text
<base>/online-research/etf-trend/<diagnostic_id>/...
<base>/online-research/etf-trend/latest.json
```

The pointer contains `AUTHORITY` and `diagnostic_id`; it never exposes an arbitrary
path. Existing verified same-day publication is adopted without recapturing even
if the running revision or observed master changes. A later complete day creates
a different archive and preserves earlier files. Unknown or newer pointer state
blocks recovery instead of silently rolling it backward.

The existing worker, snapshot publisher and API remain responsible for making this
verified pointer visible publicly and reporting `current_input`. They must be
deployed separately with their accepted configuration. An expired archive remains
historical evidence, not a current input or a successful new-day transition.

## Offline evidence and limits

```bash
PYTHONPATH="$PWD/src:$PWD" python -m pytest \
  tests/test_etf_trend_service.py tests/test_etf_trend.py \
  tests/test_etf_trend_publication.py
python -m ruff check scripts/run_etf_trend_service.py scripts/run_etf_trend.py \
  tests/test_etf_trend_service.py
MYPYPATH="$PWD/src" python -m mypy --explicit-package-bases \
  --follow-imports=silent --platform linux \
  scripts/run_etf_trend_service.py scripts/run_etf_trend.py
```

The 52 focused offline checks pass locally, including real raw-pack creation,
normalization and archive replay for two completed exchange days. HTTP responses
and time are controlled; no real requests are made. On Windows only, fixture
creation substitutes Linux directory fsync and leases; a real Linux service lease,
alarm and supervised restart still need deployment acceptance.

This remains a vendor-native **close-only** diagnostic. Adjustments, distributions,
splits, full bars, PIT, historical availability and execution constraints are not
certified. A complete input set can contain no positive-trend scores, or an
archive can truthfully report unavailable inputs. Neither state grants PAPER
admission, holdings, fills or forward returns. A real next-session run and public
delivery must be observed before claiming continuous production acceptance.
