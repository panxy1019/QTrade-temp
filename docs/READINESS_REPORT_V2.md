# QTrade v0.2 readiness report

Assessment date: 2026-08-13. Assessed checkpoint: the commit created after this
report and its validation evidence.

## Decision

**Local credential-free PAPER/SHADOW software checkpoint: READY, with documented
P1 maturity limits.**

**Authenticated real-market PAPER: `WAITING_FOR_DATA_PROVIDER_AUTH`.**

**Native broker reconciliation bridge: `WAITING_FOR_BROKER_SELECTION`.**

This is not a LIVE-trading approval. `RuntimeMode` contains only BACKTEST, PAPER
and SHADOW; market-data and broker protocols reject external-order capability;
no credential, real account login, or external order path was created.

## Tested safety/correctness boundary

- All strategy writes traverse portfolio/risk/OMS/PaperGateway/accounting.
- Runtime restart is HALTED and requires explicit reconciliation readiness.
- Account-wide fill/cancel/kill/submit mutation is serialized and durably
  convergent across multi-symbol races.
- A post-submit/checkpoint/fill-journal failure preserves accepted order and
  reservation, stops the runtime, and blocks restart recovery until resolution.
- Realtime finalized-bar signals fill only on a later received tick.
- Realtime dispatch requires a fresh provider/reconnect/data-version attestation;
  invalidation latches HALTED before any order is created.
- Provider handshake identity is bridge-returned, data-only and fail-closed.
- Tick storage confines opaque data-version IDs to its configured root.
- Champion authority requires an APPROVED lifecycle state and an immutable,
  persisted PASS promotion decision.
- Daily report history rejects hash-mismatched artifacts.

Independent correctness review reproduced the accepted-order checkpoint failure
window and found no Critical/P0 defect in capital/accounting or restart
convergence. Independent research/rule review identified the limitations below;
claims and UI were tightened accordingly.

## Validation

Run from a clean checkout:

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
python -m mypy src --strict
python -m pip check
npm --prefix web install
npm --prefix web test -- --run
npm --prefix web run lint
npm --prefix web run build
```

Validation on Windows 11, Python 3.14.6 and Node.js on 2026-08-12 (latest
AKShare amendment checkpoint):

- 215 Python tests passed;
- Ruff clean;
- strict mypy clean across 86 source files;
- no broken Python requirements;
- Vitest, ESLint and Vite production build passed;
- one external Starlette/httpx deprecation warning;
- non-blocking frontend chunk-size warnings: the main bundle is approximately
  985 kB minified and the lazy-loaded ECharts chunk approximately 1.14 MB.

## Honest maturity limits

- The RQData surface is an injected bridge adapter, not a licensed `rqdatac`
  subprocess. Authentication, entitlement, reconnect and production latency need
  vendor UAT.
- The broker package is a read-only protocol only. Candidate native gateways need
  the selected broker, sandbox and ABI-isolated environment.
- SQLite is deliberately single-process; audit hashes lack an external/WORM tail
  anchor.
- TradingRule stores sourced/effective lot, step, tick and price-limit fields, but
  the full A-share execution semantics (instrument suspension/resumption, price
  cages/no-limit periods, STAR/ChiNext quantity nuances and odd-lot liquidation)
  are not yet executable. Do not claim comprehensive China-rule simulation.
- Baseline families are deterministic signal/target primitives. They have not
  passed licensed point-in-time multi-asset validation.
- Experiment artifacts and catalog lineage are checksum-bound, while robustness,
  research loop and health contracts remain useful local primitives. Durable
  loop/task execution and broad metric policy remain future work.
- The Chinese web application has twelve routes and honest empty states; several
  pages await richer catalog/analytics APIs. It is not a claim of completed
  provider-backed analytics.
- The market page is data-backed for the verified 30-ETF historical dataset. It
  shows each symbol's latest complete OHLCV daily row only after manifest and
  Parquet checksum verification, and labels it historical rather than realtime;
  Bid/Ask and REAL_DATA_PAPER remain unavailable.
  Reproduce after starting the control plane with
  `Invoke-RestMethod http://127.0.0.1:8000/historical-bars/latest`; the verified
  artifact currently returns 30 rows dated 2026-08-12.
- Selecting an ETF now loads `/historical-bars/{symbol}` on demand and renders
  its real historical daily candlesticks and volume. The response is bounded,
  stream-validated, checksum-bound, and invalidated when the current dataset
  identity changes. It remains historical-only and does not authorize PAPER.
- The backtest page explicitly contrasts the positive exploratory sensitivity
  winner with the three negative, disjoint held-out walk-forward folds. This is
  a selection-bias disclosure, not evidence that either result is promotable.
- It now also independently verifies and aligns the Buy-and-Hold benchmark's
  immutable record and curve artifacts before rendering strategy, benchmark and
  excess returns. The 632-session full-period terminal excess is `-12.5456%`;
  this comparison is not a promotion override.
- The same endpoint verifies the leader's trade and terminal-position artifacts
  and derives month-end returns from the validated equity curve. The current
  v4 evidence contains 32 months, 56 historical simulated trades and 3 terminal
  positions. Every symbol must belong to the immutable experiment universe;
  the dashboard labels all of this research simulation rather than real market
  execution or live/PAPER account state.
- The leader's risk and run metadata are independently checksum-verified:
  drawdown reconciles to the validated curve, each transaction cost reconciles
  to the record's immutable fee rate, turnover reconciles to total notional over
  initial equity, and engine/session/timing fields bind the 632-session causal
  close-decision/next-open run. These are historical research diagnostics, not
  runtime PAPER risk observations. Engine-reported exposure and staleness are
  omitted because this artifact set lacks the daily series/configuration needed
  for independent recomputation.
- Daily reports are runtime-generated, hash-verified, and use trades persisted in
  the atomic fill journal across restart. Automatic scheduling is not wired.
- The control plane remains loopback-only; non-loopback deployment requires read
  authentication, operator identity, TLS/proxy, pagination/rate/body limits and
  security headers.

## External continuation gate

The next meaningful real-market step requires a licensed RQData or XT entitlement
and its SDK/runtime. After that, run authenticated handshake, raw/canonical
recording, gap/reconnect/heartbeat tests, deterministic replay comparison and a
multi-session PAPER soak. Broker work must remain read-only until the user selects
a supported broker and supplies a sandbox. Real order transmission remains
structurally absent regardless of either selection.

## AKShare amendment

The current goal selects AKShare as the first credential-free provider. The
repository now has an optional pinned 1.18.84 ETF adapter with explicit
noncommercial-use acknowledgement, upstream probe-before-connect, polling rate
limits, timeouts/retries, staleness and duplicate checks, health/circuit state,
canonical recording and causal PAPER replay tests. This does not upgrade the
earlier authenticated-provider decision: bounded Eastmoney probes failed before
data receipt on this host. A Sina-backed historical probe and 30-ETF canonical
ingest succeeded (18,960 quality-passed daily bars, 2024-01-02 through
2026-08-12), enabling real-data research. Snapshot authority, real-data PAPER,
and multi-day acceptance remain open.

On 2026-08-13 the pinned AKShare 1.18.84 optional environment installed cleanly
with NumPy 2.4.6 (`pip check` and Python 3.11-targeted strict mypy clean),
but one bounded 30-ETF snapshot probe still returned `ready=false` with
`AKShare upstream request failed`. The installed package exposes only the
already-failed Eastmoney and THS ETF snapshot functions; its Sina ETF functions
are list/daily-history surfaces. Snapshot PAPER therefore remains blocked, with
no historical-price substitution or repeated upstream probing.

The first causal multi-asset research comparison now covers buy-and-hold,
single/multi-horizon momentum, trend, volatility scaling, rebalance sensitivity
and cost stress. It enforces close-decision/next-open execution, whole lots,
cash bounds, declared split end dates, bounded stale valuation and test-window
cost/turnover metrics. The strongest exploratory variant still fails the default
Sharpe threshold and lacks PIT and PAPER/SHADOW evidence; the
machine-readable verdict is `NOT_ELIGIBLE`, with no Champion assigned.

A subsequent three-fold AKShare walk-forward run uses validation Sharpe for
selection, stops candidate evaluation before the held-out test, and makes full
fold ranges disjoint so a prior test cannot become later selection data. Its
checksum-verified result is bound to source commit `0438232` and dataset checksum
`70e3abf151b88382430ce82b333702243618a5eccc0b9deb71f30b19e694ac98`.
All three tests lost money: aggregate total return `-0.2044119541`, Sharpe
`-1.1004932854`, 14 observed trades, and fold pass ratio `0`. The Chinese
research page reads this evidence through a hash-verifying, fail-closed API.

The market and data-center pages also consume the actual 18,960-bar historical
dataset manifest. The API verifies a deterministic evidence-envelope hash plus
the Parquet SHA-256 before exposing provider, version, observed range, quality,
universe and instrument rows. It separately reports provider snapshot
connectivity as false; the simulated execution gateway cannot make the market
feed appear connected. Tampered metadata or Parquet bytes yield an empty,
fail-closed historical view.

A bounded 14-scenario robustness diagnostic is now bound to source commit
`442a48b` and exposed through the same fail-closed evidence boundary. It covers
cost, parameter, calendar subperiod and deterministic current-universe
perturbations. Worst Sharpe is `-1.1429123433`, dispersion is `1.6703725093`,
and at least one scenario has negative Sharpe. The artifact explicitly classifies itself as a
post-rejection diagnostic that reuses observations, not a new independent test;
it cannot alter the `NOT_ELIGIBLE` promotion result.

The backtest-analysis page now renders the verified exploratory leader's 632
full-period equity and drawdown points. The API verifies the comparison envelope,
immutable experiment record, result hash, artifact hashes, chronology, numeric
domains, and drawdown arithmetic before returning a curve. The UI keeps the
2026 test-window metrics separate from the 2024-2026 full simulation curve and
labels the result non-PIT, non-Champion and `NOT_ELIGIBLE`.
