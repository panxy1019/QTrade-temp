# Current state

Last updated: 2026-08-13

- Goal: evolve the v0.1 stable PAPER/SHADOW core into a real-market-data strategy
  research/evolution platform with a professional Chinese web application and
  isolated read-only broker architecture. Real-money trading remains disabled.
- Completed: environment audit; architecture/safety decisions; durable safety
  kernel; versioned data/PIT contracts; research/portfolio baselines; shared-path
  next-bar backtest; control plane/dashboard; reconciliation/recovery contracts;
  authoritative China rule-source registry; vn.py gap analysis.
- Stable baseline: `b502852`, QTrade Platform v0.1 - Stable PAPER/SHADOW Core.
- Current phase: QTrade v0.2 strategy research and real-market PAPER platform.
- Current milestone: V2-M7 AKShare real-market research/PAPER bootstrap.
- Acceptance evidence: 221 tests passed, Ruff clean, strict mypy clean across
  86 source files on Python 3.14.6 (2026-08-13); 15 frontend Vitest tests,
  ESLint and the production build pass.
  The causal finalized-bar -> later tick -> Risk -> OMS -> PaperGateway ->
  accounting path is integration tested without a real-provider credential.
- Environment: Windows 11/PowerShell 5.1; Python 3.14.6; Git 2.53; no Docker,
  database service or vn.py. The pinned optional `akshare==1.18.84` environment
  with NumPy 2.4.6 is now installed; `pip check` and Python 3.11-targeted strict
  mypy pass.
- Known limitations: the local SQLite runtime is single-process and does not
  claim distributed exactly-once semantics; audit hashes lack an external/WORM
  tail anchor; matching is deliberately simple; licensed realtime data, native
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
  canonicalized and live-probed; stock/index snapshots and minute endpoints remain open.
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
- Next: seek a working snapshot endpoint for the recorded PAPER workflow and
  improve verified, data-backed research analytics without weakening the
  `NOT_ELIGIBLE` conclusion.
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
  2026-08-13, one bounded 30-ETF probe still returned `ready=false` with
  `AKShare upstream request failed`. The installed release exposes no third ETF
  snapshot source beyond the already-failed Eastmoney and THS families; Sina is
  daily-history/list only. No additional retries were made.
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
