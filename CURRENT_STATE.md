# Current state

Last updated: 2026-08-26

- Goal: evolve the v0.1 stable PAPER/SHADOW core into a real-market-data strategy
  research/evolution platform with a professional Chinese web application and
  isolated read-only broker architecture. Real-money trading remains disabled.
- Completed: environment audit; architecture/safety decisions; durable safety
  kernel; versioned data/PIT contracts; research/portfolio baselines; shared-path
  next-bar backtest; control plane/dashboard; reconciliation/recovery contracts;
  authoritative China rule-source registry; vn.py gap analysis.
- Stable baseline: `b502852`, QTrade Platform v0.1 - Stable PAPER/SHADOW Core.
- Current phase: QTrade v0.2 strategy research and real-market PAPER platform.
- Current milestone: V2-M7 AKShare real-market research/PAPER bootstrap,
  adjustable historical backtest preview and bounded factor search.
- Deployment surface: a Vercel-compatible, stateless read-only application now
  serves the Chinese React dashboard with a same-origin `/api` FastAPI function.
  The packaged evidence subset retains its report and Parquet checksums. Control
  authentication is deliberately absent, so control/trade-sensitive routes fail
  closed; no SQLite PAPER runtime, provider credential, broker adapter or live
  order authority is included in the cloud bundle.
  The production project is linked to the user's private GitHub repository and
  is available at `https://qtrade.erapi0neer.xyz` through a dedicated Aliyun
  CNAME to Vercel's project-specific DNS target. Vercel reports the domain as
  `configured-correctly`; no root-domain or wildcard DNS record was changed.
  Anonymous successful GET responses are cached at the Vercel edge for 60
  seconds and the production dashboard polls once per minute. Authorized/cookie
  requests, POSTs and error responses remain uncached.
- Acceptance evidence: 308 tests passed, Ruff clean, strict mypy clean across
  97 source files on Python 3.12.13 (macOS, 2026-08-26); 37 frontend Vitest
  tests, ESLint and both production builds pass. Route-level lazy loading plus
  modular ECharts registration and stable React/zrender chunks keep every
  minified JavaScript asset at or below 426.27 KB with no large-chunk warning.
  The causal finalized-bar -> later tick -> Risk -> OMS -> PaperGateway ->
  accounting path is integration tested without a real-provider credential.
- Frontend completeness audit: `docs/FRONTEND_GAP_AUDIT.md` tracks every
  repository-feasible page gap separately from authorized-data/broker blockers
  and intentional safety boundaries. The local control plane now serves both
  root and `/web` asset paths and accepts the Vercel build's `/api` prefix, so a
  deployment build left in `web/dist` no longer produces a blank or data-less
  loopback page.
- Repository-feasible frontend gaps FE-01 through FE-25 are closed. Optional
  evidence endpoints degrade independently with visible partition failures;
  the shell adds manual refresh, update time, unknown-route recovery and a
  phone breakpoint. Page rendering failures are isolated inside the content
  region so navigation and the visible safety boundary survive. Strategy,
  experiment, research, backtest, positions and orders views now support the
  relevant search/filter/detail/JSON-export paths.
  The monitor retains health/minute status when audit history is unavailable
  and exposes every authorized symbol's causal correction state.
- Runtime observability: `/metrics/prometheus` exports finite low-cardinality
  counters/gauges in Prometheus 0.0.4 text without strategy heartbeat labels,
  credentials or account identifiers. The monitor now binds the actual
  `market_data_lag_seconds` and `orders_per_minute` fields, links the read-only
  export, and supports audit text/severity/event filtering plus current-view
  JSON export. Outbound alert delivery remains separately unconfigured.
- Automatic PAPER reporting: the loopback control-plane runner can bind exact
  data/strategy identities and a bounded operator-verified SSE open-session
  horizon at process startup. A local worker generates one immutable report
  after the Asia/Shanghai cutoff, revalidates hash/identity for idempotency and
  fails closed on missing calendar coverage or identity conflicts. Trade-day
  attribution is Asia/Shanghai-based. `/reporting-status` and the Chinese data
  center expose configured/unconfigured/generated/failed state without allowing
  callers to submit version identities. Public Vercel remains unconfigured and
  read-only.
- Loopback-only PAPER controls now cover recover/start/pause/resume/stop/kill
  and idempotent cancel using memory-only tokens and confirmation for dangerous
  actions. Public hostnames hide those controls and remain stateless read-only;
  this adds no live order path.
- Public research evidence parity: the Vercel bundle now includes the verified
  11-experiment v4 catalog and artifacts, three disjoint walk-forward folds and
  14-scenario post-rejection robustness report in addition to factor, sector and
  market evidence. Strategy, experiment, overview and backtest analytics no
  longer fall back to empty `NOT_EVALUATED` placeholders online.
- Environment: active verification uses Apple Silicon macOS and Python 3.12.13;
  Windows/QMT remains a future deployment target. No Docker, database service or
  vn.py is configured. The pinned optional `akshare==1.18.84` environment with
  NumPy 2.4.6 and the optional `rqdatac[subscribe]==3.6.3` SDK are installed;
  `pip check` and Python 3.11-targeted strict mypy pass.
- Known limitations: the local SQLite runtime is single-process and does not
  claim distributed exactly-once semantics; its durable audit rows are strictly
  schema-validated but are not hash-chained or externally/WORM anchored; matching
  is deliberately simple; licensed realtime data, native
  vn.py and broker adapters require external provider/broker selection.
- vn.py: core 4.4.0 selected behind adapters; native broker wheels require a
  separate broker-specific Python ABI after the user selects a broker.
- Real-data research: causal close-decision/next-open multi-asset experiments now
  emit immutable equity/drawdown/trade/position/risk/log artifacts. Results are
  explicitly NOT_ELIGIBLE for promotion because the universe is current/non-PIT
  and PAPER/SHADOW evidence is absent. A real-data three-fold walk-forward run
  now selects candidates using validation data only, keeps every full fold
  disjoint, and exposes checksum-verified evidence to the Chinese dashboard.
- Historical provider coverage: ETF, A-share and major-index raw daily paths are
  canonicalized and live-probed; AKShare stock/index snapshots and minute
  endpoints remain open, while the licensed RQData equivalents are implemented
  but not authenticated.
- Authoritative realtime bridge: a pinned optional `rqdatac 3.6.3` native bridge
  now performs lazy SDK import, environment-only credential loading, SDK login,
  realtime WebSocket entitlement exchange, data-only capability handshake,
  A-share/ETF/explicit-index symbol normalization, L1 Tick and 1-minute callback
  normalization, raw 1d/1m history, snapshots and an upstream heartbeat. Tick
  callbacks carry a session-global ingest ordinal that the crash-safe per-symbol
  PAPER recorder preserves. Invalid payloads or downstream callback failures
  close the feed and require reconnect/reconciliation. The local SDK is installed,
  but no RQData username/token was supplied, so the authenticated probe was not run
  and current readiness remains `UNCONFIGURED` / `NOT_ELIGIBLE`.
- Causal minute overlay: immutable per-session daily targets can now drive a
  persistent one-minute risk overlay that fails closed during warmup and can
  only preserve or reduce whole-lot target quantities. Weak short-term trend,
  excessive minute volatility and intraday drawdown reduce exposure; recovery
  requires a bounded cooldown. The minute history, session peak, multiplier and
  cooldown survive restart, and multi-symbol tick routing is serialized through
  the existing record -> quality -> finalized bar -> later tick PAPER chain.
- Minute monitoring boundary: `/minute-overlay` validates live status arithmetic
  before the Chinese monitor page displays provider connection, warm symbols and
  the fixed no-external-order boundary. With no authorized RQData/XtData client,
  it reports `UNCONFIGURED`, `NOT_ELIGIBLE` and zero ready symbols rather than
  treating historical AKShare data as a live feed.
- Long native-5-minute falsification: the optional BaoStock 0.9.3 ingest produced
  a signed 1,324,800-bar dataset for the frozen 75-stock/15-sector universe over
  368 common complete sessions. The predeclared expanding study used 120 initial
  training sessions plus six disjoint 40-session test folds and left eight tail
  sessions unused. At 5 bps, the stitched 240-session baseline returned
  `40.8285%`; the reduction-only overlay returned `1.0438%`, an excess of
  `-39.7847%`. Maximum drawdown improved from `-15.1086%` to `-12.1806%`, but
  the 0-bps overlay still lagged by `-10.0946%`. The result is retained as a
  stronger veto at `/long-five-minute-research`, not tuned away.
- BaoStock boundary: the stock-only dataset uses provider `qfq` (`adjustflag=2`)
  because the bounded ETF probe had materially shorter coverage. Current
  constituents are projected backward, adjustment factors are not versioned,
  and the manifest explicitly marks possible future adjustment knowledge,
  non-PIT status and no realtime/PAPER/external-order readiness. All-zero
  suspension placeholders are omitted without price synthesis or forward-fill.
- Adjustable backtest preview: `POST /backtest-preview` and the Chinese backtest
  page now recompute the existing causal close-decision/next-open momentum model
  from a checksum/schema-verified historical Parquet dataset. Date, horizon,
  Top-K, rebalance, reserve, trend, volatility, capital, fee and slippage inputs
  are bounded. Results include a deterministic hash, curve, drawdown, metrics and
  positions, remain ephemeral, and are always labelled current-universe/non-PIT,
  ad hoc and `NOT_ELIGIBLE`; they cannot mutate registered strategy or PAPER state.
- Bounded price-factor search: medium-horizon momentum with an optional recent-
  month skip, short reversal and low-volatility scores now use deterministic
  cross-sectional percentile ranks. Discovery labels stay inside the discovery
  window, factor values are known at the source close timestamp, validation alone
  selects the portfolio candidate, and only that locked active candidate reaches
  the confirmatory window. The research/API artifacts bind the source-tree hash,
  dataset checksum, factor diagnostics, all validation scores and test metrics.
- Factor-search evidence: a new quality-passed AKShare dataset contains 28,770
  raw daily bars for the frozen current 30-ETF universe from 2022-09-06 through
  2026-08-21 (`39a25d71...50d59fd`). An attempted 2015 start was rejected because
  eight raw Sina series contained greater-than-50% price-scale breaks and no
  authoritative adjustment factors were available; the common post-break start
  was used instead of bypassing the quality gate.
- Factor result: 5 predeclared factors and 13 bounded active validation candidates
  were evaluated. Discovery passed 120-day and 252-day skip-20 momentum, 5-day
  reversal and 60-day low volatility; 20-day reversal/Top3/20-session rebalance
  won the 2025 validation window with Sharpe `0.729972`. Its locked 2026-01-01 to
  2026-08-21 confirmation lost `12.3895%`, Sharpe was `-0.551348`, maximum drawdown
  was `-24.1822%`, and it lagged Buy-and-Hold by `7.7719` percentage points after
  modeled costs/slippage. It remains `NOT_ELIGIBLE`; the test period had also been
  viewed by earlier project experiments and is not represented as a virgin blind test.
- Factor UI/API: `/factor-search` verifies the report envelope plus all three
  bound artifact hashes, candidate count/identity, validation winner arithmetic
  and test excess-return arithmetic before the Chinese research page displays
  the negative result. Malformed, promoted or tampered evidence fails closed.
- Repeated factor robustness: a frozen 36-candidate grid was evaluated over eight
  quarterly windows in 2024-2025. Only `reversal-10-top3-20` passed the
  predeclared window gate: `6/8` positive quarters, median quarterly Sharpe
  `0.636225`, worst quarterly Sharpe `-0.900655`, and full-period net return
  `38.8414%` under base modeled costs.
- Robustness stresses: the same locked candidate retained `30.9870%` return at
  doubled transaction cost/slippage, `8/9` one-parameter neighbours and `6/6`
  deterministic 80% universe subsets were positive, and a 1,000-replication
  stationary bootstrap estimated `85.3%` probability of positive total return
  (`5%` quantile `-13.7648%`). The negative lower tail is retained rather than
  hidden by the point estimate.
- Polluted audit and UI/API: 2026 was excluded from every selection calculation
  and opened only after the robustness candidate was locked. Its audit return
  was `-0.4437%`, maximum drawdown `-17.8407%`, and active excess versus
  Buy-and-Hold `4.1739` percentage points. `/factor-robustness` independently
  recomputes the 36-candidate ranking, quarterly gates, stress ratios, bootstrap
  bounds, final robust identity and audit arithmetic; tampering fails closed.
  The Chinese research page labels this post-selection/current-universe evidence
  `NOT_ELIGIBLE` and does not connect it to PAPER.
- Multi-sector stock dataset: a signed current CSI 300/SW level-1 universe was
  frozen before stock-price access with 15 sectors and five current top-weight
  members per sector. Resumable per-symbol caches recovered from upstream
  failures; uniform HFQ ingest retained 149,903 PASSED bars from 2018-01-02 to
  2026-08-21. Every one of the 75 stocks has at least 961 bars. Fifteen stocks
  lost their first five listing-price-discovery sessions; history before the
  last gap over 90 calendar days was removed for `000792` and `002602` before
  any strategy outcome was produced.
- Locked stock/sector transfer: ETF-selected `reversal-10-top3-20` was applied
  without stock-side parameter tuning. The 2019-2025 formal run produced `7/7`
  positive years, `15/15` positive sector-only runs, `15/15` positive
  leave-one-sector-out runs, `315.7504%` full return, `23.5341%` annualized
  return, Sharpe `0.809606`, doubled-cost return `256.1430%`, and bootstrap
  positive-return probability `96.9%`. Maximum drawdown was `-34.2044%`, only
  narrowly inside the predeclared `-35%` research threshold.
- Stock-transfer boundary and UI/API: current 2026 constituents and current SW
  membership are projected backward, HFQ can be revised, sector results are
  visibly inflated by current winners, limit-up/down execution is not modeled,
  and historical suspensions may use last-close valuation for up to 40 missing
  sessions. The conclusion is therefore `NOT_ELIGIBLE` despite passing the
  research gate. `/stock-sector-transfer` independently checks the envelope,
  six artifacts, universe shape, yearly/sector/leave-one counts, cost/bootstrap
  gates and polluted 2026 audit before the Chinese research tab renders it.
- Dataset selection: `run_control_plane.py --historical-manifest` binds an
  ingested data version to the UI without editing source. A 2026-08-24 macOS
  verification ingested 1,140 PASSED bars for the frozen 30-ETF universe from
  2026-07-01 through 2026-08-21, then reproduced an identical preview hash on
  repeat and a distinct hash after changing Top-K/reserve parameters.
- Dashboard historical data: `/market`, `/instruments`, the market page and the
  data-center page now expose the real 30-ETF AKShare dataset only after both its
  evidence envelope and Parquet checksum verify. Provider snapshot connectivity
  remains explicitly `DISCONNECTED` and is no longer conflated with the simulated
  execution-gateway metric.
- Historical market rows: `/historical-bars/latest` now reads the checksum-
  verified Parquet dataset and exposes exactly one latest complete daily bar for
  each of the 30 frozen ETF symbols. The Chinese market page adds search,
  pagination and real OHLCV values while labelling every row
  `HISTORICAL_DAILY_CLOSE`; it explicitly has no Bid/Ask, realtime price or
  REAL_DATA_PAPER authority.
- Historical chart: `/historical-bars/{symbol}` streams and validates the same
  checksum-bound Parquet, rejects physical per-symbol disorder, and returns at
  most 2,000 complete daily bars with the dataset checksum. The market page
  renders real candlesticks and volume for the selected ETF, and removes the
  chart immediately if the verified dataset identity disappears or changes.
- Backtest analytics: `/backtest-analytics` and the Chinese backtest page now
  select the exploratory leader only from the verified comparison report, then
  verify its immutable record, deterministic result hash, equity/drawdown file
  hashes and pointwise drawdown arithmetic before rendering 632 full-period
  observations. Test-window metrics remain explicitly separate; the result is
  not PIT, not Champion and not eligible for promotion.
- Benchmark comparison: the same endpoint independently verifies the v4
  Buy-and-Hold record/result/equity/drawdown hashes and requires all 632 sessions
  to align with the exploratory leader before calculating excess return. The
  dashboard now plots strategy, benchmark, excess and drawdown together; full-
  period terminal excess is `-12.5456%`, reinforcing rather than softening the
  negative held-out conclusion.
- Interactive factor comparison: `/backtest-preview` now accepts only three
  explicit causal price-factor families: momentum, reversal and low volatility.
  Momentum preserves the existing multi-horizon allocation; reversal and low
  volatility use bounded cross-sectional rank combinations, while all three
  retain the same trend, target-volatility, transaction-cost, slippage and
  close-decision/next-open contracts. A fixed 2024-2025 packaged-data smoke run
  completed for all three families across 485 sessions and 30 ETFs; those ad hoc
  outputs are usability evidence only, not a new selection or held-out result.
- Browser-local experiment notebook: successful previews may be named, compared,
  reloaded and deleted in the current browser. At most 30 curve-free summaries
  are retained, duplicate result hashes replace their prior record, corrupt
  storage fails closed, and records appear only for the exact data version and
  checksum. They never become server records, `StrategyVersion` entities,
  promotion evidence or PAPER inputs.
- Backtest detail artifacts: the verified exploratory leader additionally exposes
  32 month-end returns, 56 historical simulated trades and 3 terminal positions.
  The API verifies each artifact checksum, validates every security against the
  experiment's immutable 30-ETF universe, and rejects malformed chronology,
  prices, quantities or costs. The UI explicitly labels these as research
  simulation—not market executions or current PAPER positions.
- Backtest risk contract: the same artifact chain now verifies full-period
  drawdown, turnover and transaction costs,
  cross-checking drawdown against the curve, each trade cost against the
  immutable experiment fee rate, aggregate costs against all simulated trades,
  and turnover against total notional/initial equity. It also binds the engine,
  632-session count and close-decision/
  next-open timing metadata; the dashboard labels this historical research risk,
  not current PAPER account risk. Engine-reported exposure/staleness fields are
  intentionally not shown as verified without independently recomputable series.
- Verified experiment catalog: `/strategies` and `/experiments` now expose all
  11 v4 candidates only after the comparison envelope, immutable record,
  deterministic result hash and every declared artifact validate. Duplicate
  experiment IDs, unknown scenarios and split/curve mismatches fail closed;
  current spec hashes are format-checked but cannot be independently rebuilt
  from the legacy report fields. The Chinese
  strategy/research/experiment pages show the real model, parameters, held-out
  split, record hash and `NOT_ELIGIBLE` result. They explicitly state that these
  are not registered `StrategyVersion` entities, have no lineage, and are not
  Champion/Challenger or running PAPER strategies.
- PAPER account observability: `/orders`, `/trades`, `/positions`, `/account`
  and `/risk` now expose the canonical persistent PAPER runtime rather than
  dashboard placeholders. Applied fills retain strategy/portfolio traceability
  across restart, positions include date-effective sellable quantity, and the
  risk summary recomputes position cost basis plus open BUY limit notional.
  Because no authorized realtime price exists, equity, market value, unrealized
  PnL and drawdown remain explicitly unavailable; historical closes are not
  substituted as current valuation. The web client consumes these six fields
  from one dispatch-gated `/paper-snapshot`, preventing a fill from being shown
  halfway through its in-memory account/OMS projection.
- Dashboard overview honesty: the top cards now say PAPER cash and cumulative
  realized PnL rather than portfolio equity/today PnL. Its chart consumes only
  verified historical backtest analytics and is explicitly labelled as
  non-PIT, NOT_ELIGIBLE research evidence—not current PAPER equity.
- PAPER report history: `/reports` now fails closed on invalid UTF-8/JSON,
  envelope hash, filename/report identity, date/timestamp, data-version and
  strategy-version schema. The Chinese data-center page lists only reports that
  pass those checks and separately renders the trusted automatic scheduler
  state. Scans are bounded to 366 files, 4 MiB per artifact and bounded
  identity/version fields to prevent the five-second dashboard poll from
  amplifying oversized or unbounded report directories.
- Durable audit observability: `/audit` now reads the most recent 500 events
  from the SQLite audit journal in chronological order, so restart no longer
  erases visible control/risk history. Daily incident summaries use the same
  durable source, and report assembly is serialized with PAPER mutations.
  The monitor page renders these events and counts ERROR/CRITICAL alerts;
  audit unavailability is isolated from other dashboard reads and shown as
  unavailable rather than zero alerts.
- Selection-bias guard: the backtest page now places that exploratory positive
  leader beside all three disjoint held-out walk-forward folds. All three folds
  lost money (aggregate return `-20.4412%`, Sharpe `-1.1005`, fold pass ratio
  `0/3`); the UI states that this predeclared out-of-sample evidence takes
  precedence over the ex-post sensitivity winner.
- Robustness evidence: 14 bounded cost, parameter, subperiod and deterministic
  current-universe perturbations are hash-verified and exposed fail-closed in
  the Chinese research page. Worst Sharpe is `-1.1429123433`, Sharpe dispersion
  is `1.6703725093`, and at least one scenario has negative Sharpe. This reuses
  observed periods as a
  post-rejection diagnostic; it is not new held-out or promotion evidence.
- Next: stop tuning against the already viewed 2026 window. Seek a PIT/licensed
  universe and a genuinely new forward PAPER/SHADOW period for the next factor
  hypothesis; separately seek a working snapshot endpoint for recorded PAPER.
  Select a broker before building a native read-only reconciliation bridge.
- External blockers: `WAITING_FOR_AKSHARE_SNAPSHOT_ENDPOINT_ACCESS`,
  `WAITING_FOR_DATA_PROVIDER_AUTH` and
  `WAITING_FOR_BROKER_SELECTION`; they do not block mock adapters, replay,
  strategy research, UI or read-only protocol work.
- Snapshot blocker evidence: Eastmoney and THS ETF snapshots fail TLS on this
  host; Tencent bulk A-share polling is slow/intermittent and lacks proven ETF
  coverage; Xueqiu per-symbol quotes require a session token. Historical closes
  are never relabeled as realtime observations.
- Snapshot recheck: after installing the exact pinned AKShare extra on
  2026-08-24, one bounded 20-ETF probe completed upstream work but still returned
  `ready=false` with `AKShare ETF probe has no eligible fresh canonical row`.
  Historical Sina daily data succeeded; snapshot/PAPER readiness remains false
  and no additional retries were made.
- Provider selection: AKShare 1.18.84 is the credential-free noncommercial
  research/PAPER bootstrap and has a fail-closed ETF adapter plus one-shot probe.
  This host's Eastmoney snapshot/history probes failed before data receipt. The
  Sina daily-history endpoint succeeded and produced a quality-passed 18,960-bar,
  30-ETF canonical dataset; no snapshot/PAPER connection is claimed. RQData remains
  the conditional authoritative choice; XtData is the QMT-entitled fallback.
- Recorded-session recovery: the raw archive owns durable ingest ordinals and a
  checksum-protected per-event journal recovers pre-runner crashes. Any crash after
  entering the strategy/PAPER boundary is treated as ambiguous, latches HALTED and
  requires explicit runtime reconciliation rather than silently skipping an event.
- Analytical persistence: checksum-verified Parquet/DuckDB bar storage and
  immutable hashed experiment records implemented; 51 tests pass at 93% coverage.
- Operational persistence: SQLite WAL/FULL journal records PENDING/APPLIED/FAILED
  fill markers across restart and durable audit events; backtests require a
  PASSED data manifest and record `data_version`; 54 tests pass.
- Runtime integration: PAPER/SHADOW composition gates real submissions on
  verified start/reconciliation; stop blocks writes; durable fill markers,
  gateway lookup/reconnect, idempotent cancel, audit redaction/degradation, and
  expanded portfolio/factor metrics are tested. Current suite: 63 passed.
- Instrument/research integration: point-in-time instrument master,
  fail-closed calendar, T+0/T+1 sellability lots, deterministic paper failure
  injection, bearer-token control authentication, and a reproducible import ->
  backtest -> metrics -> immutable experiment workflow are implemented.
- Recovery hardening: restart restores cash, positions, accepted/open orders and
  reservations, then always boots HALTED with reconciliation unknown. Gateway
  incidents latch HALTED; open BUYs aggregate into exposure/position risk;
  sellability is enforced by date-effective lots. Full external gateway/OMS
  reconciliation is still required before any post-restart resume.
- Atomic recovery: fill APPLIED marker, projected account/OMS state, and
  post-fill reservations commit in one SQLite transaction. Failure immediately
  after that commit restores exactly one effect. Reconciliation issues an
  expiring readiness attestation; mutable health metrics cannot authorize start.
- Mode boundary: runnable PAPER/SHADOW services require a runtime submission
  guard; strategy/portfolio imports cannot reach execution adapters. Backtest
  T+N sellability advances over observed open sessions, and the authoritative
  calendar exposes an explicit open-session advance operation.
- Cancellation: typed cancel commands use an independent idempotency key and
  traverse the application service under the dispatch gate. Cancels remain
  available after HALT and the API requires the separate trade bearer token.
- Observability: thread-safe runtime counters record unique order attempts,
  rejections, applied fills, rolling orders/minute and dispatch latency. Replay
  and duplicate fill retries do not inflate rates.
- Historical ingestion: strict canonical CSV import validates schema, UTF-8,
  offset-aware timestamps, finite values and grouped per-instrument chronology;
  source checksums flow into the downstream versioned Parquet manifest.
- Transaction costs: versioned schedules distinguish broker all-in commission
  from decomposed service/handling/regulatory fees, reject double counting,
  and model independent transfer fees plus seller-only stamp tax.
- Research validity: deterministic rolling/expanding walk-forward splits enforce
  sorted unique sessions and held-out chronological windows. Momentum, reversal,
  volatility and liquidity baselines plus multi-period ICIR are tested without
  any execution-layer dependency.
- Real ETF walk-forward evidence: commit `0438232`, dataset checksum
  `70e3abf151b88382430ce82b333702243618a5eccc0b9deb71f30b19e694ac98`,
  three disjoint held-out folds, aggregate return `-0.2044119541`, Sharpe
  `-1.1004932854`, 14 test-window trades, and fold pass ratio `0`. The result is
  `NOT_ELIGIBLE`; no strategy role or Champion was assigned.
- Persistence failure safety: account and OMS/reservation snapshots commit in
  one SQLite transaction. A snapshot write failure latches reconciliation FAILED
  and HALTED before propagating. The source runtime package is now explicitly
  tracked; `/runtime/` alone is ignored for generated artifacts.
- Fill validation: durable projections now reuse Ledger, OMS and reservation
  invariants before an APPLIED commit. An adverse-price fill cannot persist
  negative cash across restart. Post-effect monitoring exceptions are isolated
  and set `monitoring_degraded` without turning success into retryable failure.
- Review fixes: decomposed fees expose named components and apply the broker
  minimum before independent handling/regulatory charges; CSV imports enforce
  canonical six-digit IDs, SSE/SZSE venues and China-local trading-day match;
  multi-period factor ICIR/turnover uses keyed PIT observations.
- Walk-forward validity: label-horizon embargo uses verified session counts and
  configurations with overlapping held-out test windows are rejected.
- Cancel durability: the application service owns terminal cancel persistence
  and exactly-once observation. A database failure latches HALT; retrying the
  same cancel key persists the terminal state and restart restores no reservation.
- Shadow boundary: a process adapter capability check runs before connector
  invocation, accepts only hash-verified strictly sequenced observation events,
  deduplicates IDs, rejects command event types, and exposes no order-send API.
- Corporate actions: backward-adjusted bars consume only sourced normalized
  factors known at the requested point in time, apply only before effective date,
  adjust price/volume consistently, and record the exact adjustment IDs used.
- Post-commit convergence: validated durable fill projections are installed in
  ledger, OMS and reservations before audit I/O. An audit-sink failure after
  APPLIED leaves one converged fill, latches HALT/reconciliation FAILED, and
  restart restores the identical state.
- Metadata lineage: an immutable SQLite catalog registers PASSED manifests and
  experiment spec hashes with foreign-keyed data versions; the baseline workflow
  now populates and verifies this catalog.
- Live trading: disabled and structurally unavailable.
- Historical minute ingestion: a bounded Sina reader now fetches raw 1-minute
  bars through a retrying `httpx` boundary, validates positive OHLCV and the
  provider-native A-share session grid, and never synthesizes omitted minutes.
  The current synchronized dataset contains 115,430 bars across 22 ETFs and 75
  stocks spanning 15 sectors with five stocks per sector for the five latest common completed sessions
  (2026-08-19 through 2026-08-25). Its Parquet checksum is
  `ef25bdff090b8756e1c862c77f75263919ad1272a7fe19e8cf5687f9b94e1f9c`;
  classification remains recent historical, non-PIT and `NOT_ELIGIBLE` for
  realtime/PAPER readiness.
- Causal minute backtest: seven frozen reduction-only policies were ranked on
  2026-08-19 through 2026-08-21 and `drawdown-tight-v1` was read once on the
  2026-08-24/25 holdout. At 5 bps the base returned `-1.0018%` with `-1.7256%`
  maximum drawdown; the overlay returned `-0.3182%` with `-0.3933%` drawdown.
  Excess return stayed positive at 0/5/10 bps but turned negative at 20 bps;
  both ETF and stock subgroups and all 15 whole-sector leave-one-out variants remained
  negative in absolute return. Each sector check removes all five constituent
  stocks. This is useful risk-reduction diagnostics, not a
  profitable or promotion-eligible strategy result.
- Public minute evidence: `/minute-research` verifies the signed report plus all
  six artifact hashes, while `/minute-bars/{symbol}` revalidates the packaged
  Parquet checksum and the complete 97 × 5 × 238 grid before returning a bounded
  historical series. The market page switches between daily K and 1-minute
  lines; the backtest page shows the locked minute curve, daily partitions and
  cost stress. All labels remain historical, public read-only and not realtime.
- Longer cross-scale falsification: the provider-native 5-minute dataset contains
  193,920 bars across 101 symbols (26 ETFs plus 75 stocks in 15 sectors) and 40
  complete sessions from 2026-07-01 through 2026-08-25. Four expanding folds use
  16/22/28/34 training sessions and four mutually disjoint six-session test
  windows. At 5 bps the stitched 24-session baseline returned `-1.9249%`, while
  the overlay returned `-4.6234%` with `-4.9776%` drawdown and `-2.6985%`
  relative return; only one of four folds had positive overlay return. At 0 bps
  the overlay still trailed by `-0.0650%`, and ETF, stock and every whole-sector
  exclusion remained negative in absolute overlay return. This rejects transfer
  of the encouraging five-day 1-minute diagnostic; it is not tuned away.
- Public 5-minute evidence: `/five-minute-research` independently checks the
  report signature, seven artifact hashes, expanding split arithmetic, candidate
  ranks/selections, costs, subgroups, sector exclusions and all 1,152 causal curve
  points. `/five-minute-bars/{symbol}` rechecks the 101 × 40 × 48 Parquet grid and
  returns a bounded historical series. The Chinese market page exposes the
  checksum-bound 1,920-point series per symbol in a separate, lazily loaded
  native-5-minute tab; its failures cannot hide the daily or 1-minute tabs. The
  backtest page displays the failed folds and cross-scale veto. `NOT_ELIGIBLE`,
  no PAPER/SHADOW authority and `external_order_transmission=false` remain
  mandatory.
