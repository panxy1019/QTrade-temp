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

Automatic PAPER end-of-day reports are available only when the local process is
started with trusted runtime identities and an explicit, operator-verified SSE
open-session horizon. The HTTP API never accepts data or strategy versions for
report generation. For example, after those identities have been verified by
the PAPER operator:

```bash
python scripts/run_control_plane.py \
  --report-data-version authorized-paper-session-v1 \
  --report-strategy-version minute-risk@1.0.0 \
  --report-open-day 2026-08-26 \
  --report-cutoff 15:30
```

The worker polls locally and creates at most one immutable report per configured
open day after the Asia/Shanghai cutoff. Missing calendar coverage, an existing
report with a different identity, or write/hash verification failure stops that
day's generation. `/reporting-status` exposes this state read-only; the public
Vercel deployment remains `UNCONFIGURED` and cannot write reports.

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

Recent historical minute research uses a separate bounded Sina HTTP reader. It
keeps only complete provider-native sessions and never invents the source's
omitted 14:58/14:59 timestamps. The frozen universe combines 30 ETF candidates
with five current CSI 300 stocks from each of 15 sectors; incomplete ETFs are
excluded with their missing days retained in the signed manifest.

```bash
.venv/bin/python scripts/build_akshare_sector_stock_universe.py

QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true .venv/bin/python \
  scripts/sina_minute_history_ingest.py \
  --stock-universe runtime/universes/akshare-csi300-sw15x5-current-20260731-v1.json \
  --data-version sina-minute-multiasset-sw15x5-20260825-v3

.venv/bin/python scripts/run_sina_minute_overlay_backtest.py \
  --data-version sina-minute-multiasset-sw15x5-20260825-v3 \
  --output runtime/research/sina-minute-overlay-sw15x5-v2
```

The minute diagnostic observes each signal at bar close and changes exposure
only at the next bar open. Seven policies are ranked on the first three sessions;
the chosen policy is then read once on the final two sessions, with 0/5/10/20 bps
cost stress, ETF/stock subgroups and 15 whole-sector leave-one-out checks. Five recent
sessions are far too short for promotion, so the report is always
`NOT_ELIGIBLE` and cannot authorize PAPER or live market access.

The same frozen cross-section also has a separate provider-native 5-minute
falsification study. It is not a resample of the five-day 1-minute file and is
not evidence of 1-minute realtime readiness:

```bash
QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true .venv/bin/python \
  scripts/sina_minute_history_ingest.py \
  --interval-minutes 5 \
  --sessions 40 \
  --stock-universe runtime/universes/akshare-csi300-sw15x5-current-20260731-v1.json \
  --data-version sina-five-minute-multiasset-sw15x5-20260825-v1

.venv/bin/python scripts/run_sina_five_minute_walk_forward.py \
  --data-version sina-five-minute-multiasset-sw15x5-20260825-v1 \
  --output runtime/research/sina-five-minute-walk-forward-v1
```

The signed dataset contains 193,920 bars for 26 ETFs and 75 stocks across 40
sessions. Four expanding folds start with 16 training sessions and each consume
six disjoint test sessions; every candidate is causally shadow-warmed and the
selected exposure changes only at the next 5-minute open. At 5 bps the stitched
24-session baseline returned `-1.9249%`, while the overlay returned `-4.6234%`
(`-2.6985%` relative), with only one of four folds positive in absolute terms.
Even the 0 bps overlay trailed the baseline. This failure is retained as a
cross-time-scale robustness veto rather than tuned away. `/five-minute-research`
and `/five-minute-bars/{symbol}` expose only hash-verified, historical, read-only
evidence, and the market page provides a separate native-5-minute symbol browser
instead of relabeling the 1-minute tab. Promotion, realtime/PAPER readiness and
external orders remain disabled.

For a longer provider cross-check, the optional BaoStock 0.9.3 path downloads
provider-native 5-minute A-share history. It deliberately excludes ETFs because
the bounded provider probe returned materially shorter ETF coverage, uses only
the previously frozen 75-stock/15-sector universe, omits all-zero suspension
placeholders without forward-filling, and records `qfq`/`adjustflag=2` as a
non-PIT future-adjustment-knowledge risk:

```bash
QTRADE_BAOSTOCK_FREE_DATA_ACK=true uv run --extra baostock python \
  scripts/baostock_five_minute_history_ingest.py \
  --data-version baostock-five-minute-stock-sw15x5-qfq-20250102-20260825-v1

uv run python scripts/run_sina_five_minute_walk_forward.py \
  --profile baostock-long \
  --data-version baostock-five-minute-stock-sw15x5-qfq-20250102-20260825-v1
```

The signed dataset contains 1,324,800 bars for 75 stocks across 368 common
complete sessions. The predeclared study reads the first 360 sessions only:
120 initial training sessions followed by six disjoint 40-session test folds;
the final eight sessions are excluded before records enter the strategy engine.
At 5 bps, the stitched 240-session
baseline returned `+40.8285%`, while the overlay returned `+1.0438%`
(`-39.7847%` relative). The overlay reduced maximum drawdown from `-15.1086%`
to `-12.1806%`, but even at 0 bps it lagged the baseline by `-10.0946%`.
The causally aligned execution diagnostics cover all 864,000 symbol-bars in the
six test folds: mean exposure was only `63.0175%`, `80.0331%` of observations
were below full exposure, mean cumulative turnover was `511.89` per symbol, and
the 5 bps equity deduction averaged `26.8744%` of one initial-equity unit per
symbol. Weak minute trend was the most frequent overlapping trigger. This
explains the veto as persistent under-exposure plus turnover drag; it is not a
new parameter search.
`/long-five-minute-research` exposes the checksum- and artifact-verified result
as a second, stronger robustness veto. The read-only
`/long-five-minute-bars/{symbol}?trading_day=YYYY-MM-DD` endpoint verifies the
same Parquet checksum and returns exactly the selected session's 48 native bars;
the market page provides stock and date selectors. The final eight sessions did
not enter that six-fold study. They were later read exactly once by a separately
committed preregistered diagnostic: the candidate policy was fixed before the
read, all prior 360 sessions were used only for causal state warmup, and only the
eight reserved sessions were scored.

At 5 bps the reserved-tail baseline, high-exposure candidate and prior
low-exposure comparator returned `-3.0960%`, `-3.0655%` and `-3.0157%`.
The candidate improved drawdown versus the baseline and cut turnover below half
the comparator, but missed the 85% exposure floor, lost to the comparator, stayed
negative at 0/5 bps, and won only 6 of 15 leave-one-sector-out comparisons. It
therefore passed only 2 of 6 locked gates and remains `NOT_ELIGIBLE`; the same
tail must not be used for a second tuning iteration. The tail was partially
viewed during frontend chart QA, so this is a diagnostic rather than a blind
independent confirmation. `/reserved-tail-diagnostic` recomputes the gates and
verifies the preregistration, dataset, prior report, curve and artifact hashes
before returning the evidence. None of these endpoints authorizes realtime data,
PAPER, strategy promotion or external orders.

The public deployment can be audited independently across one stock from every
sector and three widely separated sessions. The script runs the same 45 requests
twice, verifies the dataset checksum, 48 unique bars, trading day, observation
type and China-market `09:35`–`15:00` clock, then signs the result:

```bash
PYTHONPATH=. .venv/bin/python scripts/audit_public_minute_bars.py \
  --cache-key <unique-audit-id> \
  --output deployment/evidence/public-minute-audit/report.json
```

The 2026-08-26 audit passed all 90 historical-bar requests. The
forced-cache-miss round had `4.669s` median and `6.248s` P95 latency; the
repeated CDN-hit round had `0.179s` median and `0.274s` P95 latency, with all 45
responses reported as `HIT`. The same run also confirmed that all seven public
control/cancel writes returned `503`.
`/public-minute-audit` exposes this point-in-time, checksum-bound availability
evidence. It demonstrates public historical-bar access and caching, not an
authorized realtime feed or a performance SLA.

The retrospective development-only policy search is reproducible with:

```bash
PYTHONPATH=. .venv/bin/python \
  scripts/run_baostock_development_policy_search.py \
  --output deployment/evidence/development-policy-search/report.json
```

It evaluates four fixed high-exposure, slower 5-minute reduction overlays on
the first 360 sessions only; the final eight sessions do not enter the strategy
engine or candidate metrics. The observed development leader retains `91.32%`
mean exposure and reduces mean turnover to `50.52` per symbol, but still trails
the base return by `2.21` percentage points. Prior reserved-tail evidence was
already visible before this post-hoc search, so the signed report remains
`NOT_ELIGIBLE`. It locks that candidate before the 2026-08-26 open for a future
minimum of 20 complete sessions; partial same-day observations never enter its
metrics.

That lock now has a code-bound forward-observation preregistration. It was
created after the first eligible session opened but before this project fetched
that target session, so the artifact explicitly does not claim a fully blind
preregistration. It binds the development report, the 368-session warmup
dataset, the exact policy, the collector/evaluator/engine hashes and five fixed
20-session gates. The research page and `/forward-observation` show the verified
`0/20` starting state.

After 15:10 China time, one complete future session can be collected with:

```bash
QTRADE_BAOSTOCK_FREE_DATA_ACK=true PYTHONPATH=. .venv/bin/python \
  scripts/probe_baostock_forward_session.py \
  --anchor-day 2026-08-25 --target-day 2026-08-26 \
  --output-root deployment/evidence/forward-session-data
```

The collector requires the exact frozen 75-stock identity, 48 provider-native
5-minute bars per stock and a byte-equivalent anchor-day OHLCV grid. A partial
session, suspended zero placeholder, missing symbol or QFQ anchor revision
rejects the entire target day. Accepted snapshots are immutable. The locked
observer is then reproducible with:

```bash
PYTHONPATH=. .venv/bin/python \
  scripts/run_baostock_locked_forward_observation.py \
  --forward-root deployment/evidence/forward-session-data \
  --output runtime/research/baostock-locked-forward-observation-v1/report.next.json
PYTHONPATH=. .venv/bin/python \
  scripts/promote_baostock_forward_observation.py \
  --candidate runtime/research/baostock-locked-forward-observation-v1/report.next.json
```

All 368 warmup sessions enter strategy state but not future metrics. Only future
complete sessions are scored at 0/5 bps and under whole-sector exclusions. No
conclusion is complete before 20 sessions, and even a pass cannot authorize
PAPER, LIVE or external order transmission.

The candidate is never copied directly over the public baseline. The promotion
step reruns the same dataset, development-lock, preregistration and report
validators used by the public API; requires at least one newly appended complete
session; rejects any rewritten historical session, lock change or non-monotonic
generation time; then uses an atomic file replacement and verifies the published
digest again.

For a bounded same-day observation, the following probe selects one stock from
each of the 15 frozen sectors and keeps only bars older than a conservative
60-second finalization guard:

```bash
QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true PYTHONPATH=. .venv/bin/python \
  scripts/probe_sina_intraday_observation.py \
  --output deployment/evidence/intraday-observation/report.json
```

`/intraday-observation` and the market-page “当日分钟观测” tab expose the signed
point-in-time result. A pre-open run correctly contained zero same-day bars; the
09:33:16 China-time refresh then verified two finalized bars (09:31 and 09:32)
for all 15 sector representatives. The 12:07:48 midday refresh verified all 120
morning-session common bars through 11:30, with a `1.057s` request window. Its
larger latest-bar age is retained because the market was in the lunch break;
provider rows timestamped after the causal cutoff were excluded. The
probe is intentionally a free historical-source observation, not a streaming
subscription: it never sets realtime/PAPER readiness, mutates strategy state or
sends an external order. The report can be refreshed after the market opens to
show only finalized same-day bars while preserving that boundary. Its signed
evidence also records the 15-symbol request window and the maximum age of each
symbol's latest same-day finalized bar so source delay stays visible.

A separate breadth probe checks all five frozen stocks in each of the 15 sectors
without duplicating their full OHLCV payload in the dashboard snapshot:

```bash
QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true PYTHONPATH=. .venv/bin/python \
  scripts/probe_sina_intraday_breadth.py \
  --output deployment/evidence/intraday-breadth/report.json
```

The signed artifact retains every stock's finalized same-day timestamp set plus
its first open, previous close and latest close. The API independently recomputes
the 75-stock intersection, per-sector intersections, latest-bar ages, request
window, relative-open/latest-minute returns and equal-weight directional breadth,
then omits the raw timestamp sets from its response. The market page shows the
resulting 15×5 coverage and sector-direction tables beside the 15 representative
stock charts. At the fourth 12:35 China-time checkpoint, all 75 stocks shared 120
finalized morning minutes through 11:30; 59/75 were above their first open and
12/75 rose in the latest finalized minute, while the equal-weight session and
latest-minute returns were `+1.0826%` and `-0.0709%`. This is observable source
breadth only; it does not create realtime entitlement, strategy input, PAPER
readiness or order authority.

Multiple detail/breadth refreshes can be chained into a bounded continuity
artifact:

```bash
PYTHONPATH=. .venv/bin/python scripts/update_intraday_continuity.py \
  --output deployment/evidence/intraday-continuity/report.json
```

Each checkpoint binds both source-report hashes, completion times, coverage,
common finalized-minute counts and lag measurements to the prior checkpoint.
Starting at an explicit immutable sequence, it also retains the verified
relative-open/latest-minute breadth and equal-weight returns. Earlier checkpoint
hashes are not rewritten during this schema migration. `/intraday-continuity`
recomputes the chain, monotonicity and latest-report
binding before the market page renders the timeline. A source regression remains
visible instead of being repaired or dropped. The chain is deployment evidence,
not a realtime subscription, strategy state or PAPER authorization.
The current sixth checkpoint binds 15/15 detail and 75/75 breadth coverage at
150 common finalized minutes through 13:30 and is monotonic versus the previous
121-minute checkpoint. The signed direction timeline starts at checkpoint five;
checkpoint six has 57/75 stocks above open, 29/75 rising in the latest minute,
`+0.9648%` equal-weight session return and `-0.74 bp` equal-weight latest-minute
return. Versus checkpoint five, above-open participation fell by three stocks and
the equal-weight session return fell `11.99 bp`. All three mutable report
writers publish through a flushed, `fsync`-backed same-directory temporary file
and atomic replacement, so an
interrupted refresh cannot expose a half-written JSON document.

The dashboard reads the long 5-minute research, reserved-tail diagnostic,
development lock, future observer, public audit and both intraday reports through
one `/minute-research-snapshot` request. The server opens and SHA-256 verifies the
24 MB / 1,324,800-row long-history Parquet once, then passes the same verified
identity to all eight independent report validators. Standalone endpoints remain
available for inspection. A missing or invalid report stays `null` in the bundle
and cannot be replaced by an unverified value; the aggregation changes read
amplification only, not evidence rules or runtime authority.
The browser also caps concurrent GETs at six, preventing a fresh serverless
deployment from cold-starting every remaining partition at once. A request-pool
test fixes that upper bound and confirms that the retired heavy endpoints are no
longer part of the dashboard fan-out.

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
bounded date, momentum/reversal/low-volatility factor family, factor horizons,
Top-K, rebalance, reserve, trend, volatility, capital, transaction-cost and
slippage adjustments. It recomputes from verified Parquet and never mutates the
strategy registry or PAPER runtime. Results can be named and compared in the
current browser, where records remain bound to the exact data version, checksum
and result hash; this local notebook is not server persistence. Every preview is
explicitly non-PIT, ad hoc and `NOT_ELIGIBLE`; it is not promotion evidence or
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
FastAPI entrypoint at `/api`. The committed deployment evidence contains the
checksum-bound daily, 1-minute and native 5-minute historical datasets plus the
accepted research, walk-forward, robustness and transfer reports. Packaging
these immutable artifacts does not add realtime identity or data authority. The
cloud context has zero trading capacity and no control or trade token, so
mutation endpoints fail closed with HTTP 503.

Low-cardinality runtime counters and gauges are available in Prometheus 0.0.4
text format at `/metrics/prometheus` locally and `/api/metrics/prometheus` on the
Vercel deployment. The export omits strategy heartbeat identities and contains
no credential, account identifier or order mutation path. The system-monitor
page links to that read-only endpoint and can search, filter and export the
current bounded audit view.

```bash
PYTHONPATH=. .venv/bin/pytest -q tests/test_vercel_entry.py
npm --prefix web run build:vercel
npx --yes vercel@latest deploy --prod
```

The hosted application is a public research dashboard, not the persistent local
PAPER runtime. It has no provider credential, realtime minute feed, SQLite
account state, broker adapter or external order-transmission capability. See
`deployment/README.md` for the packaged-evidence boundary.

Production: <https://qtrade.erapi0neer.xyz>
