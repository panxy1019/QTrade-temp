# Q — A-share and ETF quantitative trading platform

Q is a safety-first personal quantitative trading platform for Shanghai and
Shenzhen A-shares, exchange-traded funds, and index benchmarks.

The repository contains a runnable local PAPER/SHADOW platform checkpoint with
versioned data, research, event-driven backtesting, portfolio construction,
risk/OMS/accounting, recovery, monitoring, and a control plane. Real-money
trading is not enabled.

## Safety invariant

`LIVE_TRADING_ENABLED` is false by default and cannot be enabled by the current
software. Strategies never call a broker directly: every order follows
portfolio → risk → OMS → execution → gateway.

## Development

Requires Python 3.11 or newer. From the repository root:

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python scripts/run_control_plane.py
```

Build and verify the Simplified-Chinese React dashboard separately:

```powershell
npm --prefix web install
npm --prefix web test -- --run
npm --prefix web run lint
npm --prefix web run build
```

The local dashboard is then available at `http://127.0.0.1:8000`. It boots
fail-closed (gateway disconnected and reconciliation unknown); the page is an
operational status surface, not authorization for external order transmission.

Control endpoints are disabled unless `QTRADE_CONTROL_TOKEN` is supplied in the
process environment. Send it as `Authorization: Bearer <token>`. Read endpoints
do not accept a caller-provided role escalation. Bind the development server to
loopback unless a separately reviewed TLS/authentication proxy is deployed.

After a restart the PAPER runtime is HALTED. `POST /system/recover` (authenticated)
must complete local reconciliation and issue a fresh readiness attestation before
`POST /system/start` can succeed. Pending/failed fills or uncertain submissions
make recovery fail closed.

Run the complete synthetic provenance workflow with:

```powershell
python scripts/run_baseline_workflow.py
```

It writes ignored runtime artifacts under `runtime/sample`: checksum-verified
Parquet bars and an immutable experiment record containing `git_commit` and
`data_version`.

`qtrade.runtime.PaperBarRunner` consumes strictly ordered historical/replay bars, executes a
strategy's previous target through the durable PAPER pipeline at the next bar
open, and checkpoints the pending target and last consumed event. Dated fills
persist acquisition lots and enforce date-effective T+N sellability across
restart. The runner is intentionally single-process; licensed realtime feeds
belong behind the read-only SHADOW adapter after a provider is selected.

Completed realtime bars use `qtrade.runtime.RealDataPaperRunner`: a finalized bar
may decide a target, but execution can occur only on a later received tick under
a fresh provider/session/data-version readiness attestation. This prevents using
an already historical bar open as a PAPER fill.

Historical vendor exports can enter through `qtrade.data.CsvBarImporter`. The
canonical UTF-8 CSV schema is exactly `symbol,exchange,timestamp,trading_day,open,high,low,close,volume`;
timestamps must include an offset. Imports are checksum-versioned and must pass
the grouped quality gate before Parquet storage. Vendor-specific column mapping
belongs in a separate licensed-data adapter.

Project status and exact continuation steps are maintained in
[`CURRENT_STATE.md`](CURRENT_STATE.md), [`plans/MASTER_PLAN.md`](plans/MASTER_PLAN.md),
and [`plans/V2_MASTER_PLAN.md`](plans/V2_MASTER_PLAN.md).

## Causal minute risk overlay

`qtrade.runtime.MinuteRiskOverlayStrategy` combines an immutable daily target
with finalized one-minute bars. It fails closed while minute history is warming,
then may preserve or reduce the daily target in whole lots when short-term trend,
minute volatility or intraday drawdown limits are breached. It cannot introduce
a symbol, exceed the daily quantity or change the daily strategy version.
Recovery cooldown prevents immediate re-risking.

Daily targets and overlay histories can use the SQLite operational journal as a
state store, so restarts retain the publication timestamp, rolling closes,
session peak, active multiplier and cooldown. `MultiAssetRecordedPaperSession`
serializes a shared tick stream and routes each configured symbol through the
existing raw-recording, quality, one-minute aggregation and causal PAPER chain.
The resulting target still executes only on a tick received after the finalized
bar. `/minute-overlay` exposes fail-closed readiness to the Chinese monitor page.

No authorized realtime provider is configured by default. The endpoint therefore
reports `UNCONFIGURED`, `NOT_ELIGIBLE` and `external_order_transmission=false`;
AKShare daily history is not relabelled as realtime. A licensed RQData or XtData
read-only bridge must be selected and validated before forward SHADOW/PAPER use.

The optional native RQData bridge is implemented with pinned `rqdatac 3.6.3`.
It imports the SDK lazily, reads `QTRADE_RQDATA_USERNAME` and
`QTRADE_RQDATA_TOKEN` only inside the bridge process, and claims a session only
after both SDK login and realtime WebSocket authorization succeed. Install and
run its data-only probe with:

```bash
python -m pip install -e '.[rqdata]'
python scripts/probe_rqdata_readonly.py
python scripts/probe_rqdata_readonly.py --connect --symbol 510300
```

The first probe prints only SDK/configuration booleans and environment-variable
names. `--connect` performs an authorized heartbeat and snapshot round trip but
never accesses an account or sends an order. Plain six-digit A-share/ETF codes
are mapped by exchange prefix; ambiguous index codes must include an explicit
RQData suffix such as `000300.XSHG`.

For forward PAPER, register the `RqDataProvider.on_tick` callback with the
configured `MultiAssetRecordedPaperSession` before subscribing. The bridge gives
every callback a session-global ingestion ordinal, and the recorder preserves it
across per-symbol canonical files so multi-stock replay retains arrival order.
The local `TickBarAggregator` remains the strategy clock. RQData's pushed minute
bar is a secondary comparison input because the vendor documents that late ticks
can make a live minute bar differ from its cleaned end-of-day history. A fresh,
short-lived market-data readiness attestation is still required before the PAPER
runner accepts any tick.

## Real historical data and adjustable preview

AKShare is an optional, noncommercial research source. After installing
`.[akshare]`, ingest a bounded daily dataset with an explicit acknowledgement:

```bash
QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true python scripts/akshare_history_ingest.py \
  --start 2024-01-01 --end 2026-08-21 \
  --data-version akshare-etf30-local-v1
```

Build the web application and start the loopback-only control plane against the
generated, checksum-signed manifest:

```bash
npm --prefix web run build
python scripts/run_control_plane.py \
  --historical-manifest runtime/data/akshare/akshare-etf30-local-v1/manifest.json
```

Open `http://127.0.0.1:8000/web/#/backtest`. The parameterized preview supports
bounded date, momentum horizon, Top-K, rebalance, reserve, trend, volatility,
capital, transaction-cost and slippage adjustments. It recomputes from verified
Parquet and never mutates the strategy registry or PAPER runtime. Every preview
is explicitly non-PIT, ad hoc and `NOT_ELIGIBLE`; it is not promotion evidence or
investment advice.

## Bounded factor search

The formal factor search uses a longer checksum-verified dataset and separates
factor discovery, portfolio validation and one locked active confirmation. It
does not require a clean Git tree; instead, the report records a deterministic
hash of every tracked/untracked source file so local research remains auditable.

```bash
QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true .venv/bin/python \
  scripts/akshare_history_ingest.py \
  --start 2022-09-06 --end 2026-08-21 \
  --data-version akshare-etf30-factor-research-20220906-20260821-v1

.venv/bin/python scripts/run_akshare_etf_factor_search.py \
  --data-version akshare-etf30-factor-research-20220906-20260821-v1 \
  --output-dir runtime/research/akshare-factor-search-v3
```

The current run evaluates five factor hypotheses and at most fifteen active
validation candidates with modeled transaction cost and slippage. Buy-and-Hold
is comparison-only. The resulting report and its diagnostics/validation/test
artifacts are ignored runtime evidence, served through `/factor-search` only
after envelope and artifact verification. The current result is negative,
current-universe/non-PIT, based on a previously viewed confirmatory period and
therefore permanently `NOT_ELIGIBLE`.

## Repeated robustness search

The follow-up search freezes 36 factor/parameter candidates before execution and
uses only eight quarterly windows from 2024 through 2025 for selection. A
candidate must first pass the window-level return/median-Sharpe/worst-Sharpe
gate, then survive doubled costs, one-parameter neighbours, six deterministic
80% universe subsets and a 1,000-replication stationary bootstrap. The 2026
period is accessed only after selection and is permanently labelled a polluted
audit rather than new held-out evidence.

```bash
.venv/bin/python scripts/run_akshare_etf_robust_factor_search.py \
  --data-version akshare-etf30-factor-research-20220906-20260821-v1 \
  --parent-report runtime/research/akshare-factor-search-v3/report.json \
  --output-dir runtime/research/akshare-robust-factor-search-v4
```

The current run found one research-robust candidate,
`reversal-10-top3-20`: six of eight positive quarters, median quarterly Sharpe
`0.636225`, worst quarterly Sharpe `-0.900655`, doubled-cost return `30.9870%`,
eight of nine positive one-parameter neighbours, six of six positive universe
subsets, and bootstrap positive-return probability `85.3%`. Its 2026 polluted
audit returned `-0.4437%` with maximum drawdown `-17.8407%`. This is repeated,
post-selection evidence on a current/non-PIT universe—not a new blind test—and
remains `NOT_ELIGIBLE`. `/factor-robustness` exposes it only after independently
checking the report, all five artifact hashes, ranking/window arithmetic, every
gate criterion and the 2026 audit boundary.

## Multi-sector stock transfer

The ETF-selected `reversal-10-top3-20` candidate is also frozen before any stock
outcome is read, then transferred without stock-side parameter tuning to 75 CSI
300 members: 15 current SW level-1 sectors and the five highest current CSI 300
weights in each sector. The universe is signed before price download.

```bash
.venv/bin/python scripts/build_akshare_sector_stock_universe.py

QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true .venv/bin/python \
  scripts/akshare_stock_history_ingest.py \
  --data-version akshare-csi300-sw15x5-hfq-20180101-20260821-v1

.venv/bin/python scripts/run_akshare_stock_sector_transfer.py \
  --data-version akshare-csi300-sw15x5-hfq-20180101-20260821-v1 \
  --output-dir runtime/research/akshare-stock-sector-transfer-v3
```

The ingest is resumable through per-stock signed caches, uses one uniform HFQ
series, removes five initial price-discovery sessions for stocks listed inside
the window, and resets history after the last gap exceeding 90 calendar days.
The resulting PASSED dataset has 149,903 bars from 2018-01-02 through 2026-08-21;
all 75 stocks have at least 961 retained bars.

The formal 2019-2025 transfer passed the predeclared research gate: `7/7`
positive years, `15/15` positive sector-only runs, `15/15` positive
leave-one-sector-out runs, `315.7504%` full return, `256.1430%` doubled-cost
return, `96.9%` bootstrap probability of a positive total return, and
`-34.2044%` maximum drawdown. These unusually strong current-winner results do
not cure survivorship bias: the 2026 constituents are projected backward, HFQ
is not point-in-time, limit-up/down execution is absent, and suspended holdings
may use the last close for up to 40 missing market sessions. The report therefore
remains permanently `NOT_ELIGIBLE`. `/stock-sector-transfer` verifies the report,
six artifact hashes and all gate arithmetic before the Chinese research page
shows it.

## Public read-only web deployment

`vercel.json` builds the React application for `/` and exposes the stateless
FastAPI entrypoint at `/api`. The committed deployment evidence is limited to
the 30-ETF signed Parquet dataset and the accepted factor, robustness and stock
transfer reports. The cloud context has zero trading capacity and no control or
trade token, so mutation endpoints fail closed with HTTP 503.

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/test_vercel_entry.py
npm --prefix web run build:vercel
npx --yes vercel@latest deploy --prod
```

The hosted application is a public research dashboard, not the persistent local
PAPER runtime. It has no provider credential, realtime minute feed, SQLite
account state, broker adapter or external order-transmission capability. See
`deployment/README.md` for the packaged-evidence boundary.
