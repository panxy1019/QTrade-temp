# Continuous research observation deployment

## Boundary and reuse audit

The observer reuses the repository's AKShare adapter, Sina native five-minute
reader, Bar quality gate, Parquet store, and durable JSON publisher. It adds
scheduling and freshness checks, not another gateway or trading engine. The
existing vn.py audit remains in `docs/VNPY_GAP_ANALYSIS.md`; importing vn.py does
not resolve the missing process supervision and freshness monitoring.

The service has no strategy, risk, OMS, account, broker or order interface.
`LIVE_TRADING_ENABLED=false`, `real_data_paper_readiness=false`, and
`external_order_transmission=false` remain fixed. Source histories, calendar
information and frozen current constituents are research observations, not PIT
or live execution authority. Old signed research evidence is never overwritten.

## Data and schedule

- Daily: the frozen 30-ETF universe, raw Sina daily history through the latest
  complete eligible day, starting 2026-08-22 to recover the historical gap.
- Intraday: the frozen 75-stock universe, provider-native five-minute history,
  up to 96 bars per symbol, excluding bars less than 60 seconds old.
- Daily collection runs on startup and at most hourly. Five-minute collection
  runs on startup, every 15 minutes during the weekday market window, and every
  six hours otherwise. One subprocess runs at a time with a 360-second timeout.
- HTTP request deadlines and retries remain bounded. Failure preserves the last
  successful dataset and separately reports the failed attempt.
- Each successful collection creates a new immutable Parquet dataset and
  checksum-bound report. These are historical collections and must not be
  counted as previously recorded forward PAPER/SHADOW sessions.

`configs/observer_calendar_2026.json` records SSE/SZSE sources and its explicit
2026-08-01 through 2026-12-31 horizon. Daily freshness advances after 16:30 China
time. Intraday freshness accounts for lunch, the finalization guard, weekends
and stated exchange holidays, with a visible 20-minute observation tolerance.
Out-of-horizon calendars, future clocks, incomplete universes, checksum failure,
stale source data and dead workers cannot report healthy. The calendar must be
renewed before 2027; emergency exchange closures require an explicit update.

## Installation and verification

Use a dedicated Python >=3.11 virtual environment and the exact accepted source
revision. Do not modify shared cluster environments. For example:

```bash
python3 -m venv /PERSIST/qtrade/venv
/PERSIST/qtrade/venv/bin/python -m pip install -e '.[akshare,dev]'
/PERSIST/qtrade/venv/bin/python -m pytest -q \
  tests/test_data_observer.py tests/test_sina_minute.py \
  tests/test_akshare_provider.py tests/test_atomic_json.py
/PERSIST/qtrade/venv/bin/python -m ruff check \
  scripts/run_data_observer.py tests/test_data_observer.py
MYPYPATH=src /PERSIST/qtrade/venv/bin/python -m mypy \
  scripts/run_data_observer.py --follow-imports=silent
export LIVE_TRADING_ENABLED=false
export QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true
/PERSIST/qtrade/venv/bin/python -m scripts.run_data_observer \
  --root /PERSIST/qtrade/runtime --port 8010
```

Inspect the filesystem before choosing `/PERSIST`. NFS is used only for immutable
research files and JSON publication; this deployment does not put a SQLite WAL
runtime/catalog on NFS. The single-writer lock is on container-local `/tmp`.
One container owns this service; multi-container concurrent writes are unsupported.

Adapt `observer.supervisor.conf.example`, install it as one new Supervisor
program, then use `supervisorctl reread` and `supervisorctl update
qtrade-data-observer`. Existing programs must not be restarted. Supervisor
restarts this worker after process failure; it cannot guarantee that a rented
container itself will remain powered on or be recreated automatically. Keep the
program template on persistent storage for container-reset recovery.

The inspected container's Supervisor compatibility layer can apply `reread` or
`update` while its Python client raises `ValueError: not enough values to unpack`.
Do not infer success from that error or restart every program. Inspect the
specific process status; after a targeted restart verify its `/proc/PID/cwd`,
data versions and health endpoint against the intended release. This deployment
verified both running programs against source `300497a` after targeted restarts.

The HTTP service binds only `127.0.0.1:8010`:

```bash
curl -fsS http://127.0.0.1:8010/data-freshness
curl -fsS http://127.0.0.1:8010/health
curl -fsS http://127.0.0.1:8010/metrics/prometheus
```

`/health` returns 503 on missing/stale/invalid data or a stale heartbeat;
`/data-freshness` remains readable for diagnosis. The persisted `freshness.json`
contains the last computed status; independent monitoring must check
`heartbeat_at` rather than treating an old JSON document as current health.

Reach this private endpoint using an SSH local port forward. Do not publish the
cluster's control services or credentials. Outbound email/chat notification and
off-host backups are not configured by this milestone.

## Vercel ownership and connection

Vercel hosts the React application and the stateless read-only FastAPI function.
Import `panxy1019/QTrade-temp` into the user's Vercel team, retain root `./`, and
use the repository's `vercel.json`. Git-connected production-branch pushes
trigger deployments. Custom domains are added to that project and configured
in the domain owner's DNS; copying a repository does not transfer an old project
or someone else's domain.

The existing Vercel adapter reads bundled evidence. It cannot reach an RFC1918
SSH cluster address on the public internet. Continuous cluster collection does
not automatically update that old evidence bundle or turn the new website into
a realtime dashboard. Connecting the website requires an explicitly configured
read-only publication path (for example, approved object storage or a reviewed
HTTPS observation API). No SSH password belongs in Vercel or GitHub settings.

The subsequent read-only channel is implemented by
`scripts/publish_research_snapshot.py` and `qtrade.api.public_observations`.
It publishes bounded JSON every five minutes to the dedicated
`panxy1019/QTrade-observations` repository with a repository-scoped deploy key;
Vercel reads a fixed public HTTPS URL without credentials. Use
`publisher.supervisor.conf.example` for the independent supervised publisher.
The original evidence remains frozen. See `docs/RESEARCH_WORKFLOW.md` for
publication, formal experiment persistence and catalog recovery, and
`docs/DATA_FACTOR_STRATEGY_STATUS.md` for the data/factor/strategy inventory.

## Acceptance record, 2026-10-25

- Source: `panxy1019/QTrade-temp` commit
  `f0b5ad7c10db2b3af6591df4744952c2ed360075` plus this observer change.
- Runtime: Ubuntu 22.04 container, isolated Python 3.14.6; effective allocation
  13 vCPUs and 60 GiB RAM. The persistent NFS mount had about 299 GiB available.
- Linux verification: 40 focused tests passed, Ruff passed, and the source-aware
  mypy command above passed. Windows verification: 37 relevant tests passed,
  excluding the existing Linux-only directory-fsync suite.
- Daily version `observer-daily-20261025T081435756587Z`: 1,170 bars, 30/30 ETFs;
  Parquet SHA-256
  `790a88d9217c18893a8f83bdc9bc7f50a23db359daa50c756f3e42ea44cd4c92`.
- Minute version `observer-minute-20261025T081525765102Z`: 7,200 bars, 75/75 stocks;
  Parquet SHA-256
  `84e64232d9b1a41555babe2c3dacd242fdcd438989c1419e2540c7a75561aaac`.
- At 08:23:48 UTC, `/health` returned HTTP 200 with both streams `FRESH`, all
  latest symbol clocks at 2026-10-23 15:00 Asia/Shanghai and a current heartbeat.
  A targeted Supervisor restart changed the PID and retained both data versions
  and task state. Existing container services were not restarted.
- Vercel production `https://q-trade-temp.vercel.app` returned HTTP 200 for
  `/api/deployment`, with the expected main-branch commit and no external-order
  authority. `/api/historical-bars/latest` still reported 2026-08-21 bundled data;
  public-web integration with the new observations remains outstanding.

This is a dated acceptance snapshot, not a promise that a powered-off container
or an expired calendar remains healthy. Use the live endpoints for current status.
