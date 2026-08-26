# Current state

Last updated: 2026-08-27

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
- Acceptance evidence: 456 tests passed, Ruff clean, strict mypy clean across
  98 checked source files on Python 3.12.13
  (macOS, 2026-08-27); 66 frontend Vitest
  tests, ESLint and both production builds pass. Route-level lazy loading plus
  modular ECharts registration and stable React/zrender chunks keep every
  minified JavaScript asset at or below 429.92 KB with no large-chunk warning.
  GitHub CI grants only read access to repository contents and pins the official
  Node 24 checkout/setup-python actions to their verified v7 release commits.
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
  On the 60-second public polling cadence, a partial or failed first snapshot
  receives one bounded retry after five seconds; a complete first snapshot and
  the local five-second cadence issue no extra retry.
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
  Restart recovery now rejects lossy JSON type coercion in durable daily-target
  and minute-overlay checkpoints: booleans cannot become quantities and strings
  cannot become readiness flags. A malformed checkpoint fails closed before any
  PAPER/SHADOW state is restored. The later-Tick execution checkpoint applies
  the same exact-type boundary to pending quantities and ingest ordinals, and
  requires timezone-aware causal signal/decision timestamps before recovery.
  Durable account recovery also rejects non-finite cash/PnL, boolean quantities
  and malformed position rows; valid recovery remains HALTED until explicit
  reconciliation. Durable orders likewise require a complete collection, finite
  positive request economics, bounded exact filled quantities and ordered
  timezone-aware timestamps instead of skipping or coercing damaged rows.
  Reservation and T+1 sellability-lot collections are also mandatory: their
  quantities remain exact positive integers, reservation cash remains finite
  and non-negative, sell reservations cannot carry cash, and malformed rows or
  dates reject restart instead of disappearing from recovered risk state.
- Intraday breadth evidence: a separate signed Sina point-in-time probe covers
  all 75 frozen stocks across 15 sectors with five stocks per sector. It records
  each stock's finalized same-day timestamp set, request timing, latest-bar age,
  first open, previous close and latest close. `/intraday-breadth-observation`
  recomputes the 75-stock/per-sector intersections and exact relative-open,
  latest-minute and equal-weight direction summaries before returning a bounded
  result to the Chinese market page. The 15:01 China-time refresh retained 75/75
  stock coverage and the source-defined complete 238-minute session through
  15:00: 55 stocks were above their first open, 13 rose in the latest finalized
  minute, and equal-weight returns were `+0.8777%` session-to-date and `-2.88 bp`
  latest-minute. Sina omits 14:58/14:59 and publishes the closing auction at
  15:00; QTrade does not synthesize those two timestamps. Its 75-stock request
  window was `2.802s`, which remains visible as source
  lag evidence. These
  observational fields remain outside strategy/PAPER state and cannot set
  realtime readiness or transmit an external order.
- Intraday continuity: each deployed checkpoint can bind the current 15-stock
  detail report and 75-stock breadth report into a bounded previous-hash chain.
  `/intraday-continuity` verifies every checkpoint digest, sequence, China trading
  day, monotonic common-minute arithmetic and exact latest-report identities.
  The market page displays regressions rather than silently correcting them;
  this remains deployment evidence and never enters strategy or PAPER state.
  The 2026-08-26 chain has nine checkpoints; its latest detail/breadth pair
  covers 15/15 and 75/75 stocks with the complete 238 common finalized source
  minutes through 15:00 and is monotonic versus the prior 210-minute checkpoint.
  Schema v2 preserves
  all four prior checkpoint hashes and starts a direction-breadth timeline at
  checkpoint five; missing direction after that boundary fails closed. Mutable
  intraday reports now use flushed, `fsync`-backed atomic publication, preventing
  a crash from exposing a partially written signed snapshot. Latest-minute
  returns render in signed basis points so small causal moves are not rounded to
  a misleading `0.00%`. Each direction checkpoint also displays its change in
  above-open participation and equal-weight session basis points versus the prior
  direction-bearing checkpoint; this remains observation-only.
- Long native-5-minute falsification: the optional BaoStock 0.9.3 ingest produced
  a signed 1,324,800-bar dataset for the frozen 75-stock/15-sector universe over
  368 common complete sessions. The predeclared expanding study used 120 initial
  training sessions plus six disjoint 40-session test folds and excluded eight
  tail sessions before records entered the strategy engine. At 5 bps, the
  stitched 240-session baseline returned
  `40.8285%`; the reduction-only overlay returned `1.0438%`, an excess of
  `-39.7847%`. Maximum drawdown improved from `-15.1086%` to `-12.1806%`, but
  the 0-bps overlay still lagged by `-10.0946%`. The result is retained as a
  stronger veto at `/long-five-minute-research`, not tuned away.
- Long-study execution diagnosis: all 864,000 symbol-bars in the six independent
  folds are now causally attributed to the bar where exposure actually applies.
  Mean exposure was `63.0175%`; `80.0331%` of observations were reduced; mean
  cumulative turnover was `511.89` per symbol and 5-bps cost deduction averaged
  `26.8744%` of an initial-equity unit per symbol. Fold totals and overlapping
  trigger counts are signed, arithmetically revalidated by the API, and displayed
  without changing candidates, folds, returns, curves or the reserved tail.
- Long-history intraday browser: the public read-only market page can select any
  of the 75 stocks and 368 complete sessions. The
  `/long-five-minute-bars/{symbol}?trading_day=YYYY-MM-DD` endpoint rechecks the
  signed Parquet identity and returns exactly 48 provider-native bars for that
  day. Viewing a reserved-tail session does not enter it into the backtest or
  change the frozen research result; QFQ/non-PIT and non-realtime boundaries stay
  visible.
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
- Individual-stock minute preview: `POST /single-stock-five-minute-backtest`
  accepts any stock in the signed 75-stock BaoStock universe, a verified range of
  two to 120 sessions and bounded 5-minute reduction-only parameters. It replays
  all prior available Bars only as causal warmup, scores the selected interval,
  and returns deterministic base/overlay curves, drawdown, execution and daily
  diagnostics. The backtest page exposes the complete selector and JSON export;
  QFQ/non-PIT, ad-hoc, `NOT_ELIGIBLE`, stateless and external-order-disabled
  boundaries are part of the response and UI.
- Intraday factor workflow: `POST /intraday-factor-backtest` and the research
  page now connect factor definition, effectiveness and related-feature
  correlation checks, strategy timing, causal replay, cost stress and explicit
  PAPER/LIVE gates. The opening three-ten-minute-bullish rule runs on 75 stocks
  across 15 sectors using exact 48-Bar native grids. It is known at the 10:00
  close, uses the next Bar's open (Bar label 10:05, interval start 10:00), and
  exits T+1 at the first Bar's open (label 09:35, interval start 09:30). The
  disclosed 2026-07-28 through 2026-08-24 baseline triggered 192/1,500 stock-days
  and lost 4.0840% after 5 bps per side; Sharpe was -3.3347 and maximum drawdown
  -5.1038%. It failed the research gate and cannot enter PAPER. Five-level
  order-book buying intensity remains `UNCONFIGURED` behind a structured 0/6
  data-readiness contract. It requires all five bid/ask prices and sizes,
  provider event/receive clocks plus sequence, provider-unit normalization to
  shares, continuous-auction validity, a certified freshness limit and recorded
  replay reconciliation. Missing/non-finite/negative depth, crossed/non-monotonic
  books, stale/future snapshots and a zero total ask size all reject the factor.
  `intensity > 1` is displayed only as an untested buy hypothesis; the sell
  condition is not defined and no effectiveness, correlation or backtest claim
  is made. OHLCV is not used to fabricate bid/ask depth. Because the
  endpoint is deterministic and side-effect free, its browser client retries
  one network-level fetch failure; HTTP validation/server responses and every
  control or trading POST are never retried by this path. A fixed
  `0/5/10/20 bps` expanding-window diagnostic now selects a threshold on prior
  sessions only and stitches disjoint test folds. Both 5-day test folds selected
  5 bps and lost 2.5834% / 2.1394%; the stitched ten-session return was -4.6675%
  with -5.2240% maximum drawdown. All four fixed-threshold test returns were
  negative (-4.4507% to -5.7885%). This reused-history diagnostic therefore
  fails its robustness gate and is not represented as independent evidence.
  A second immutable long-window report fixes the latest 120 eligible sessions,
  a 60-session initial training prefix and six disjoint ten-session test folds.
  It binds the 75-stock/15-sector dataset checksum, factor/API source hashes and
  exact response hash. Across 9,000 stock-days the rule triggered 1,001 signals;
  full-window return was -27.1715% with Rank IC -0.0121, while the stitched
  sample-out return was -28.3060% with -28.7245% maximum drawdown and only one
  of six positive folds. All four fixed thresholds were negative. The read-only
  endpoint, research card and data-center inventory preserve this stronger veto;
  it is viewed reused history, remains `NOT_ELIGIBLE`, and cannot authorize
  PAPER, LIVE, state mutation or external orders.
  The same card now exposes the signed 120-day strategy/benchmark and drawdown
  curve, all six fold identities, all 15 sector partitions, a searchable 75-stock
  breakdown, threshold stress and both hard-gate matrices. These are read-only
  views of the fixed response hash; filters cannot change the evaluated universe
  or create a new candidate.
- Bounded intraday factor construction: the same endpoint and workbench now
  accept a typed three-slot opening-direction rule. Each completed ten-minute
  interval is `BULLISH`, `BEARISH` or `IGNORE`; active slots combine with `ALL`
  or `ANY` under one bounded nonnegative body threshold. The default three-
  bullish preset cannot be redefined, and custom requests cannot submit code,
  arbitrary features or alternate timestamps. Every custom result remains
  causal (10:00 decision, 10:05 next-Bar entry, default T+1 09:35 exit), ephemeral,
  current-universe/QFQ non-PIT and `NOT_ELIGIBLE`; it reuses all existing
  effectiveness, correlation, robustness and promotion gates without mutating
  strategy or PAPER state.
- Bounded intraday exit-factor construction: the same strategy form can instead
  require three completed bearish ten-minute bodies on T+1. The exit condition
  is not known until the next session's 10:00 close, so a trigger executes only
  at the open of the 10:05-labelled source Bar. A non-trigger exits at the open
  of the final 15:00-labelled Bar, whose interval starts at 14:55; neither path
  uses a same-Bar close. The threshold is bounded to 0-1000 bps, a threshold on
  the default next-open exit is rejected rather than ignored, and the benchmark
  uses the same per-symbol exit rule. Browser-local records retain the exit
  identity while entry-factor correlation remains standardized to T+1 open.
  On the already viewed 2026-07-28 through 2026-08-24 window, the 5 bps exit
  condition triggered 28 of 192 entries and 164 used the time exit. The full
  window returned +1.3245% with +4.9200% excess, but maximum drawdown was
  -11.3789%, stitched walk-forward return was -10.0783%, and the research gate
  still failed. This is a retrospective diagnostic, not evidence for PAPER.
- Exit-factor effectiveness is evaluated separately from total strategy profit.
  For each actual entry where the T+1 bearish condition triggers, the engine
  compares the 10:05-labelled conditional exit open with the counterfactual
  15:00-labelled time-exit open, forms one mean timing advantage per trigger
  session and applies Newey-West uncertainty. The 5 bps viewed-window rule had
  28 triggers across 10 sessions: raw mean advantage +0.1966%, session mean
  +0.4898%, 64.29% positive, Newey-West t 1.8326 and a 95% interval from
  -0.0340% to +1.0136%. Trigger count, session count and sign passed, but the
  `t > 1.96` gate failed; `exit_factor_effectiveness_passed=false` is therefore
  part of the strategy research hard gate. This counterfactual diagnostic does
  not change the standardized T+1-open label used to validate entry factors.
- Bounded intraday factor discovery now enumerates one fixed library of 104
  opening rules: the 26 non-empty `BULLISH`/`BEARISH`/`IGNORE` three-slot
  direction tuples, `ALL` combination only, crossed with 0/5/10/20 bps body
  thresholds. A 40-session request uses only the first 20 sessions to rank and
  lock one rule, then evaluates only that locked identity on the last 20
  chronological, mutually exclusive sessions. Selection is known at the first
  test day's 09:35 exit completion, before its first possible 10:00 signal.
  On the viewed 2026-06-30 through 2026-08-24 window, training selected
  `DIR-XDU-T0000` (`IGNORE/BEARISH/BULLISH`, 0 bps) with 348 signals, +0.4931%
  session-clustered conditional excess and Newey-West t 2.1897. The locked test
  produced 326 signals but -0.0919% conditional excess, t -0.5462, -6.1491%
  total return, -1.8107% excess, -9.8972% maximum drawdown and Sharpe -4.7951.
  The test gate therefore fails. This entire window is now viewed reused-history
  evidence, not an independent confirmation or permission to retune it; the API
  and Chinese workbench keep it stateless, `NOT_ELIGIBLE`, with no external order.
- Discovery selection bias is now measured with one fixed combinatorially
  symmetric cross-validation audit. The same 40 viewed sessions are divided
  into eight contiguous five-session blocks; all 70 four-block training/four-
  block complement combinations reuse the exact training-gate, Newey-West t,
  conditional-excess, lower-threshold and candidate-ID selection order. Average
  out-of-sample rank handles tied quality tuples. In the real 104-candidate run,
  29/70 selected rules ranked below the complement median, so estimated PBO was
  41.43%; only 35/70 complement results had positive excess and their median
  excess was -0.1788%. Seventeen different candidates were selected, led by
  `DIR-DDD-T0020` in 24/70 splits. All three selection-bias gates failed, agreeing
  with the failed chronological lock. This CSCV diagnostic deliberately reuses
  the original locked-test blocks and is neither causal forward validation nor
  an independent confirmation; it cannot replace the locked candidate or alter
  `NOT_ELIGIBLE`, PAPER/LIVE, state-mutation or external-order boundaries.
- The locked discovery winner now receives a fixed 0/5/10/20 bps cost audit on
  the exact same 326 test signals and 20 chronological test sessions. The rule
  is never reselected from a cost row. Strategy return was already -4.2527% at
  zero cost, then -6.1491%, -8.0089% and -11.6212% as cost increased; maximum
  drawdown worsened from -8.9914% to -13.0167%, and every excess return remained
  negative. Zero-cost profitability, all-cost profitability and 20 bps positive
  excess failed; only the expected non-increasing-return integrity check passed.
  The cost-stage discovery gate requires the chronological lock, CSCV/PBO and
  fixed-cost audit together and was 0/3. It remains a historical research
  veto with no candidate reselection, promotion, state or order authority.
- Locked-test sector stress now holds the same candidate, threshold, 5 bps cost,
  326 signals and 20 test sessions across 15 sector-only evaluations and 15
  whole-sector exclusions. Every sector had a signal, but only 4/15 sector-only
  returns were positive. Electronics was worst at -16.4469%; machinery was best
  at +9.0138%. All 15 leave-one-sector-out portfolios remained negative: the
  range was -7.2180% after excluding power equipment to -4.7210% after excluding
  electronics. Signal coverage passed, while the 60% positive-sector ratio,
  80% positive-exclusion ratio and non-negative worst exclusion all failed.
  This rules out one isolated sector as the explanation. The consolidated
  sector-stage research gate requires chronological test, CSCV/PBO, fixed cost
  and sector stress together and was 0/4.
- Locked-test stock stress now applies that same immutable candidate and test
  identity to each of the 75 stocks without filtering or reselection. Only
  22/75 stocks returned a profit (29.33%); the per-stock total-return median was
  -1.1644%. `600276` had no signal in the locked test, so stock-level signal
  coverage also failed. The five largest absolute per-stock returns contributed
  24.79% of total absolute return magnitude, below the fixed 30% concentration
  ceiling. Three of four stock gates fail. The consolidated discovery gate now
  requires all five chronological, CSCV/PBO, cost, sector and stock stages and
  was 0/5.
- Locked-test market-state stress now classifies each test day only by the sign
  of the previous session's 75-stock equal-weight T+1-open return. That return is
  complete at the current day 09:35 open, before the current factor can be known
  at 10:00. The 12 days following a negative prior session returned -0.7402%
  with +1.4887% excess; the eight days following a non-negative prior session
  returned -5.4493% with -3.2917% excess. Both states had at least five days and
  signals, and every label was causally known, but neither state had positive
  strategy return and not every state had positive excess. The state rows cannot
  reselect the candidate. The consolidated discovery gate is now 0/6, still
  `NOT_ELIGIBLE`, stateless and external-order-disabled.
- Locked-test placebo audit now holds the same selected rule, test window and
  each session's realized signal count fixed, then uses seed 20260827 for 499
  cross-sectional permutations that only reassign those signals among the 75
  same-day stocks. The observed session-clustered conditional excess was
  -9.1894 bp, versus a +0.2642 bp placebo median and +17.9292 bp placebo 95th
  percentile; 387/499 permutations were at least as large as observed, giving
  a +1-corrected one-sided p-value of 77.60%. Coverage and count preservation
  passed, while positive observed effect, p <= 0.05 and observed-above-P95 all
  failed. Placebo rows cannot enter training or trigger candidate reselection.
  The consolidated seven-stage discovery gate is 0/7 and remains viewed-history
  evidence with no PAPER/LIVE, state-mutation or external-order authority.
- Locked-test execution-delay stress now preserves the same 326 signals, 20
  sessions, selected rule and 5 bps cost while moving both the strategy and
  equal-weight benchmark entry from the first causal 10:05 source-Bar open to
  the predeclared 10:10 and 10:15 opens. Total return improved from -6.1491% to
  -5.0013% and -4.6735%, but excess remained -1.8107%, -0.9354% and -0.7027%;
  all three Sharpe ratios and total returns remained negative. Exact delay and
  signal-identity gates passed, while all-delay positive total and excess gates
  failed. Delay rows cannot change factor selection. The consolidated historical
  discovery gate is now 0/8, with no PAPER/LIVE, state or order authority.
- Locked-test share-liquidity/capacity stress now holds the same candidate,
  326 signal identities and 20 test sessions fixed while reading only the
  completed 09:55-10:00 source Bar volume known at the 10:00 signal. BaoStock's
  minute contract defines volume in shares, so fixed 1%/5%/10% participation
  rates are rounded down to 100-share A-share lots without using any future
  entry-Bar volume. At 1%, the minimum/lower-median/maximum capacities are
  400/13,300/241,700 shares and all 326 signals support at least one lot. The
  frozen QFQ OHLCV dataset has no provider-native amount field, so the system
  refuses to present adjusted price times volume as actual RMB capacity and the
  capacity stage fails closed. Capacity rows cannot reselect the rule or enter
  training. The consolidated historical discovery gate is 0/9 and retains no
  PAPER/LIVE, state-mutation or external-order authority.
- Intraday factor comparison: successful previews can be retained as at most 30
  checksum-bound summaries in the current browser. Each summary keeps its typed
  rule, result hash, signal/observation counts, return, Sharpe, drawdown, excess,
  Rank IC, correlation, stitched walk-forward result and hard-gate outcomes.
  Invalid, cross-dataset or safety-relaxed records fail closed; loading a record
  only restores form parameters and requires a fresh rerun. These records are
  neither formal experiments nor independent evidence or PAPER inputs.
- Session-clustered factor significance: effectiveness testing now forms one
  conditional-excess observation per signal-bearing trading day before applying
  an automatically banded Newey-West mean standard error, t-statistic and 95%
  interval. The hard gate requires at least ten such sessions and `t > 1.96`, so
  75 correlated stocks on one day cannot inflate the independent sample count.
  In the fixed 120-day report all 120 sessions had signals, but clustered excess
  averaged -0.0574%, Newey-West t was -0.4800 and the interval was
  [-0.2916%, 0.1769%]; the hypothesis remains `NOT_ELIGIBLE`.
- Intraday factor time stability: every selected factor window is divided by
  date into four fixed, contiguous and mutually exclusive blocks. Each block
  reports signal days and the mean of its session-clustered conditional excess;
  no block outcome can change the factor rule, threshold or split. The hard gate
  requires signals in every block, at least three of four positive block means,
  and less than 60% of absolute block contribution from any one period. The
  disclosed 20-session run had two positive blocks and two negative blocks; the
  signed 120-session result also had only 2/4 positive blocks, with a worst block
  mean of -0.3125% and maximum contribution share 36.88%. Concentration passed,
  but sign stability failed, strengthening the existing `NOT_ELIGIBLE` veto.
- Multi-rule false-positive control: the 2–8 rule same-window diagnostic now
  replays each saved body's threshold rather than silently evaluating every
  candidate at zero. It clusters conditional excess by session, computes a
  two-sided asymptotic p-value from the Newey-West t-statistic, and applies
  Holm-Bonferroni across the complete submitted family. A rule needs at least
  250 stock-day observations, 20 signals, ten signal sessions, positive excess,
  `t > 1.96` and adjusted `p <= 0.05`; correlation de-duplication remains a
  separate continuous-score gate. In the disclosed 20-session, 75-stock check,
  none of three example rules passed (all adjusted p-values were 1), while a
  duplicate pair had correlation 1. The endpoint and UI remain retrospective,
  stateless, `NOT_ELIGIBLE` and external-order-disabled.
- Paired out-of-sample uncertainty: the immutable 120-session report now
  resamples the 60 stitched test days with 1,000 paired stationary-bootstrap
  paths, expected block length ten and a fixed seed. Strategy and benchmark use
  the same sampled indices, preserving their daily pairing and short-range
  dependence. The observed excess was -12.4906%; the bootstrap median was
  -11.8338%, its 5th/95th percentiles were [-23.9107%, +0.4281%], and only 5.7%
  of paths had positive excess. The API recomputes the complete summary from the
  signed daily curve, so a changed and re-signed bootstrap claim fails closed.
  This is another reused-history diagnostic, not a p-value, new blind sample or
  authority for PAPER/LIVE.
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
- Analytical persistence: checksum-verified Parquet/DuckDB bar storage now also
  requires every Parquet row's data version to equal its manifest; immutable
  hashed experiment records are implemented. A direct audit of the four packaged
  daily, 1-minute, native 5-minute and long BaoStock files confirmed all
  1,662,920 rows carry their exact manifest version. Reads preserve physical row
  order through the shared quality gate before returning sorted Bars, so a
  resigned file cannot hide per-instrument disorder or malformed OHLCV by relying
  on reader-side sorting. The exact ten-column Arrow schema is checked before
  row conversion, so missing, extra, reordered or type-drifted columns fail with
  a uniform dataset-integrity error.
- China trading-day integrity: the shared quality gate rejects SSE/SZSE Bars
  whose timezone-aware timestamp does not resolve to the declared `trading_day`
  in UTC+08:00. A direct audit of all 12 business Parquet files found zero
  mismatches across 3,566,133 rows.
- Storage path containment: Parquet reads and writes resolve each `data_version`
  before touching disk and require one direct child under the configured root;
  traversal, absolute paths, nested paths and symlink escapes fail closed.
- Corrupt-storage handling: a checksum-valid but undecodable Parquet payload and
  low-level file read failures are normalized to `DatasetIntegrityError`, so
  research/API callers do not receive backend-specific Arrow or OS exceptions.
- Empty-storage handling: a checksum-valid, schema-valid zero-row Parquet file is
  rejected on read, preserving the non-empty invariant already enforced at
  write time and preventing an empty backtest from appearing successful.
- Instrument identity integrity: every Bar requires non-empty, whitespace-clean
  symbol/exchange values, and SSE/SZSE symbols must be canonical six-digit
  codes. All 105 distinct symbols across the 12 business Parquets pass.
- Row-content decoding: nullable or syntactically malformed timestamp, date and
  decimal fields in a checksum-valid Parquet file fail as
  `DatasetIntegrityError` before the semantic quality gate.
- Atomic dataset publication: Parquet bytes and checksum are completed in a
  same-filesystem staging directory before the immutable data-version directory
  appears; failed writes clean their staging state and allow a safe retry. The
  file, staging directory and published storage-root entry are fsynced in order
  before a PASSED manifest is returned.
- Public deployment provenance: `/deployment` exposes only allowlisted Vercel
  system metadata (validated full Git SHA, ref and target environment), never
  arbitrary environment variables. The monitor shows the 12-character commit;
  missing or malformed identity remains `UNAVAILABLE`.
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
- Locked future-session observation: the development leader
  `dev-floor-098-slow12x48-v1` now has a signed, code-bound preregistration. It
  fixes a 20-complete-session evaluation before the first target-session fetch,
  while disclosing that the detailed contract was written after that session
  opened and therefore is not fully blind. Collection is fail-closed at 75
  stocks × 48 native 5-minute bars, requires an exact frozen 2026-08-25 QFQ
  anchor match, and rejects partial/zero-placeholder sessions. The public
  `/forward-observation` verifier now reports `1/20`, `INCOMPLETE` and
  `NOT_ELIGIBLE`. The immutable 2026-08-26 session contributes exactly 3,600
  Bars and 48 synchronized metric points; no runtime authority changed. At 5
  bps the base returned `0.6338%` and the overlay `0.5004%` (a `-13.34 bp`
  shortfall), while mean exposure was `97.53%` and mean turnover per symbol was
  `0.076`. All five current cumulative gate values are true, but the overall
  gate remains false until 20 complete sessions. A separate publisher now
  accepts only a fully verified candidate
  that strictly appends complete sessions to the already public sequence. It
  rejects historical rewrites, frozen-lock changes and stale generation times,
  then atomically replaces and re-verifies the deployment report. The public
  verifier also requires the report's complete warmup identity to equal the
  preregistered data version/checksum/session contract and explicitly rejects
  reports generated at or before the lock plus Python booleans masquerading as
  integer session counts. A target session cannot postdate the report's China
  market date or appear before the locked 15:10 same-day collection boundary;
  a resigned mismatch falls back to the independently verified `0/20` lock.
  Cost-stress execution values also require finite bounded exposure and finite,
  non-negative total/per-symbol turnover with exact arithmetic consistency; a
  negative value cannot satisfy the predeclared maximum-turnover gate. The 15
  leave-one-sector-out rows must also match the exact sector code/name identities
  extracted from the verified frozen long-history universe.
  Before atomic promotion, the publisher now rereads every formal forward-session
  manifest and checksum-bound Parquet, then requires the candidate session list
  and accepted count to match those source identities exactly.
  The 0/5-bps rows must also share one Buy-and-Hold metric identity, with only
  `1e-12` relative / `1e-15` absolute machine-rounding tolerance, so cost
  comparisons cannot be constructed from materially drifting baselines.
  Each row must preserve the full 11-field execution diagnostic; reduced counts,
  exposure sums, turnover, transaction cost, per-symbol means and reason counts
  are all structurally and arithmetically revalidated. Reduced/reason counts must
  be genuine integers; Python booleans are explicitly rejected. Formal source
  sessions also bind the exact frozen BaoStock `0.9.3` provider version rather
  than trusting a source string that could be rewritten together with its
  self-reported version. Atomic promotion now synchronizes the destination
  directory after replacement, closing the durability gap between a flushed
  temporary file and a persisted directory entry. Candidate generation must
  also be no later than the publisher's timezone-aware validation time, so a
  resigned future timestamp cannot advance and then freeze the append-only chain.
  The raw target-day field must be the collector's canonical `YYYY-MM-DD`, not a
  parser-compatible compact spelling carried consistently into the candidate.
  After the candidate temporary file is flushed, every signed source session and
  checksum-bound Parquet is reopened and revalidated immediately before the
  destination replace; failure leaves the prior public report intact. The source
  Parquet must also contain the exact 75 symbols from the preregistration-bound
  long-history manifest, with 48 native timestamps per symbol/day, canonical
  exchange/day/timezone identities and a clean OHLCV quality gate.
  A composed regression also feeds one all-zero BaoStock suspension placeholder
  into an otherwise complete 48-bar target grid: the provider parser omits it
  without filling, leaving 47 bars, and the locked complete-session validator
  rejects the entire target day.
  The formal report endpoint also requires its exact top-level schema and rejects
  generation times more than five minutes ahead of the server clock. A resigned
  extension or future-dated zero-session report falls back to the separately
  verified preregistration and cannot alter the public `0/20` state.
- Forward source coverage: an independent, signed BaoStock diagnostic checked
  the full 75-stock locked universe after 15:10 China time without saving fetched
  Bars. At `2026-08-26T20:13:08+08:00`, all 75 symbols had an exact frozen anchor
  and a complete 48-point target grid; `300750` supplied the final missing grid,
  and none had a fetch error or anchor revision. `/forward-source-coverage`, the bundled
  minute snapshot, the research page and the data-center inventory expose those
  facts. A UTC-loaded frozen anchor was also replayed against equivalent
  Asia/Shanghai provider Bars to guard the timezone-aware identity contract.
  The coverage report is explicitly not forward evidence and did not itself
  create the separately collected formal `1/20` session; it leaves realtime,
  PAPER, LIVE and external-order authority disabled.
  The public verifier accepts only the exact report schema and rejects generation
  times more than five minutes ahead of its own clock; a recomputed self-hash
  cannot authorize unknown pass-through fields or future-date the observation.
  A separately signed, previous-hash-bound source-coverage history retains the
  six independently published diagnostics at `41 → 47 → 52 → 69 → 74 → 75 READY`. Every
  checkpoint is bound to a content-addressed, immutable full 75-stock release
  and reruns the same report verifier, so a wholly resigned summary cannot
  fabricate an old point. The chain reports whether any regression occurred. The
  API, bundled snapshot, research page and data center expose the bounded chain;
  it remains source diagnostics only and never becomes formal forward evidence.
  All mutable JSON evidence publishers now synchronize the replacement directory
  after the completed file is flushed and atomically renamed, so a successful
  coverage, detail, breadth or continuity write includes the directory entry's
  crash-durability request.
- Public minute read amplification: `/minute-research-snapshot` now verifies the
  24 MB long-history Parquet once per dashboard refresh and reuses that immutable
  identity across nine report validators. The React loader replaced nine
  parallel heavy requests with this single bundle while retaining standalone
  endpoints and per-section fail-closed fallbacks. A test instruments the Parquet
  verifier and requires exactly one open; the bundle currently has zero
  unavailable sections and preserves all realtime/PAPER/order prohibitions.
  Browser GET concurrency is capped at six and covered by a deterministic
  fan-out test so transient cold-start pressure cannot silently return. Every
  GET also requests `cache: no-store`; a production refresh therefore cannot
  keep rendering an older signed intraday checkpoint from the browser cache.
  Each GET is also aborted after 30 seconds and releases its concurrency slot;
  a stalled network request therefore cannot strand the snapshot or queued
  chart reads indefinitely. An HTTP-successful minute bundle also propagates
  every declared or null internal partition into the same visible failure map,
  so fail-closed placeholders cannot suppress the warning or recovery retry.
- Historical chart loading semantics: daily, 1-minute, native 5-minute and long
  5-minute panels now show an explicit verification/loading state while their
  identity-bound series request is pending. They no longer claim that evidence
  is missing during a valid second-stage request; actual failure and verified
  empty responses remain distinct fail-closed states.
- Local backtest comparison refresh: every browser-local experiment save or
  delete advances a dedicated list revision. Consecutive deletes therefore
  update the table even when the user-facing status text is unchanged; these
  summaries remain local-only and never become registered strategy evidence.
- Intraday-factor PAPER admission: the factor-backtest response and Chinese
  research page now expose one structured, exportable eight-gate dossier instead
  of only a `NOT_ELIGIBLE` label. The current three-ten-minute-bullish run passes
  only the checksum-bound historical-data identity gate (`1/8`): its research
  gate and causal return gate fail, the candidate is ad hoc/unregistered, and no
  unseen forward SHADOW evidence, authorized realtime provider, fresh runtime
  attestation or PAPER reconciliation binding exists. The decision is therefore
  `BLOCKED`; it cannot register or start a strategy, change runtime mode, mutate
  state or transmit an external order.
- Intraday strategy construction identity: each factor-backtest response binds
  the exact entry factor, expression, directions and threshold; exit type and
  optional exit factor; causal price sources; equal-weight sizing; matched
  benchmark; costs; and T+1 execution into one deterministic SHA-256. The
  derived ad-hoc ID and full hash are visible and embedded in the PAPER
  registration blocker. Observed exit counts are excluded because they are
  sample outcomes. This is a reproducible read-only identity, not a registry
  insertion: it remains `AD_HOC_UNREGISTERED`, PAPER-ineligible, stateless and
  external-order-disabled.
- Sequential factor-to-strategy advancement: the API and Chinese workbench now
  distinguish a computed diagnostic from permission to advance. Factor
  effectiveness, correlation and rolling robustness are three ordered research
  prerequisites. The current three-bullish-Bar result passes only correlation
  (`1/3`), first blocks at factor validation, and labels strategy construction
  `DIAGNOSTIC_PREVIEW_ONLY`; the causal backtest remains a completed diagnostic,
  not promotion evidence. Reselection on the viewed window, registration,
  PAPER authorization, state mutation and external orders all remain false.
- Consistent rejected-research next action: PAPER admission no longer carries a
  fixed strategy-registration instruction that contradicts sequential
  advancement. Both contracts now share the same computed action. For the
  current `1/3` rejected factor that action is
  `PREDECLARE_NEW_HYPOTHESIS_BEFORE_NEW_EVIDENCE`, rendered in Chinese as
  “在接触新证据前预注册新假设”; strategy registration is not offered.
