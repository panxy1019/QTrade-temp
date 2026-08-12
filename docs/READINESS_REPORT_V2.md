# QTrade v0.2 readiness report

Assessment date: 2026-08-12. Assessed checkpoint: the commit created after this
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

Validation on Windows 11, Python 3.14.6 and Node.js on 2026-08-12:

- 186 Python tests passed;
- Ruff clean;
- strict mypy clean across 84 source files;
- no broken Python requirements;
- Vitest, ESLint and Vite production build passed;
- one external Starlette/httpx deprecation warning;
- one non-blocking frontend chunk-size warning (approximately 979 kB minified).

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

The first causal multi-asset research comparison now covers buy-and-hold,
single/multi-horizon momentum, trend, volatility scaling, rebalance sensitivity
and cost stress. It enforces close-decision/next-open execution, whole lots,
cash bounds, declared split end dates, bounded stale valuation and test-window
cost/turnover metrics. The strongest exploratory variant still fails the default
Sharpe threshold and lacks PIT, walk-forward and PAPER/SHADOW evidence; the
machine-readable verdict is `NOT_ELIGIBLE`, with no Champion assigned.
