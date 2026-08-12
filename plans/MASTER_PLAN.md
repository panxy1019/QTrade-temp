# Master plan

This is the living execution record for the long-horizon engineering goal.

## Objective

Build a runnable, maintainable, tested platform for Shanghai/Shenzhen A-shares,
on-exchange ETFs, and benchmark indices: ingestion → point-in-time universe →
research → signals → portfolio → backtest → risk → OMS → paper execution →
accounting → monitoring/control → recovery → shadow mode. Real trading stays
off until a separate explicit human authorization gate.

## Environment evidence

Audit date: 2026-08-12.

- `E:\Q` was empty and was not a Git repository.
- Python 3.14.6 and Git 2.53.0 are installed.
- Docker/uv/database services are unavailable.
- vn.py, pytest, FastAPI, Pydantic, DuckDB, PyArrow, pandas, and PostgreSQL
  drivers were not installed.

## Milestones

### M1 — Deterministic safety kernel (acceptance complete)

Scope: domain contracts, operational modes, kill switch, pre-trade checks,
OMS state machine, idempotent paper gateway, accounting, append-only audit.

Acceptance:

- [x] implementation follows the mandatory order pipeline;
- [x] duplicate submission cannot create a second paper order/fill;
- [x] partial fill/cancel/reject and invalid transitions are tested;
- [x] HALTED/PAUSED/REDUCE_ONLY behavior is tested;
- [x] insufficient cash, reservations, and invalid/stale orders fail closed;
- [x] accounting balances and PnL are deterministic;
- [x] no live mode or real gateway is executable;
- [x] unit and end-to-end tests pass;
- [x] two independent safety reviews completed; blocking in-process issues fixed.

Reproduce: `python -m pytest`

Known limitations: the SQLite checkpoint is single-process rather than
distributed exactly-once. Uncertain fill replay blocks for reconciliation.
Audit chaining is tamper-evident but not externally/WORM anchored.

### M2 — Versioned data and instrument foundation (contracts complete; adapters in progress)

Instrument master, date-effective trading rules, calendars, bars, corporate
actions, memberships, checksums, quality gates, point-in-time queries, local
Parquet/DuckDB adapter and fixtures with provenance.

Current evidence: source-backed effective-date rule model, PIT memberships and
corporate actions, multi-check quality gate, checksum-verified Parquet/DuckDB
bar adapter, and strict checksum-versioned canonical CSV importer. Temporal and
jump validation is grouped per instrument and rejects non-finite values.
Sourced normalized corporate-action factors produce point-in-time backward-
adjusted bars with explicit adjustment lineage. A durable immutable SQLite
catalog links PASSED data manifests to experiment hashes and is populated by the
baseline workflow. Remaining: broader calendars/status and larger fixture coverage.

### M3 — Research, factors, portfolio, and evaluation

Chronological splits/walk-forward experiment records, baseline factors and
strategies, portfolio constraints, cost-aware metrics, reproducibility.

Current evidence: chronological non-overlap gates; buy/hold and moving-average
baselines; top-K/weight/cash constraints; portfolio metrics and factor
IC/RankIC/quantiles/turnover/spread; immutable hashed experiment record; tested
synthetic end-to-end workflow. Rolling/expanding walk-forward splits, momentum,
reversal, realized-volatility, liquidity baselines and multi-period ICIR are now
tested. Multi-period evaluation uses instrument-keyed, as-of/known-at observations
so membership turnover cannot depend on array position. Remaining: model training
orchestration and vnpy.alpha adapter. Walk-forward boundaries support session-based
label embargo and reject overlapping held-out test windows.

### M4 — Integrated event-driven backtest

Same strategy/portfolio/risk/OMS/execution contracts; costs, slippage, partial
fills, cancellation, rejection, accounting, benchmark evaluation.

Current evidence: shared pipeline integrated, signals execute at the following
bar open, input ordering/identity and single-use behavior tested. Remaining:
broader metrics, liquidity/matching models and report. T+N sellability advances
over the observed open-session bar sequence rather than calendar days.
Fee schedules now carry source identity and explicit all-in/decomposed policy;
all-in broker commission cannot be combined with separately added handling or
regulatory components, preventing a common backtest double-counting error. Named
fee breakdowns preserve handling/regulatory charges below broker minimums.

### M5 — Durable paper runtime and recovery

Persistence, reconciliation, replay, restart protocol, duplicate/out-of-order
events, disconnect/freeze/database failure injection.

Current evidence: SQLite FULL/WAL journal, durable account/order/reservation/fill
markers, restart restoration, mandatory HALTED restart, uncertain fill blocking,
incident-to-HALT coupling and recovery sequence tests. Remaining: one atomic
external gateway reconciliation/replay and PostgreSQL multi-process adapter.
Inbound fill APPLIED + projected account/OMS/reservations now commit atomically;
a crash-after-commit test restores exactly one economic effect.
The projected effect is validated through the same Ledger/OMS/reservation domain
invariants before APPLIED. Adverse-price/insufficient-cash fills remain FAILED
with no restored economic effect. Monitoring observers are non-throwing after
successful order/fill effects.
After durable APPLIED, the validated projection is installed across all in-memory
economic/order/reservation state before audit I/O. Injected audit failure then
latches HALTED/FAILED and both live and restarted state converge to one fill.
Ordinary account/runtime snapshots also commit in one transaction and injected
database failure latches HALTED. The composition root is tracked in Git after
repairing an over-broad `runtime/` ignore rule.
Terminal cancellation persistence is application-service owned and retryable by
its idempotency key; injected persistence failure halts and a successful retry
restores CANCELLED without a leaked reservation.
The PAPER bar runner serializes callbacks, checkpoints last-event/pending-target
identity, executes targets at the next bar open through the mandatory risk/OMS
path, and persists T+N acquisition lots in the atomic fill projection. Concurrent
callback and restart regressions prove no duplicated target exposure and restored
sellability state.
The SQLite composition claims a single-process owner and rotates a runtime lease;
same-identity runners share a journal-scoped lock and superseded runtime instances
are fenced from writes. Cross-process ownership and stale-owner recovery are tested.

### M6 — Control plane, dashboard, and observability

FastAPI with read/control/trade-sensitive role separation, prominent mode,
structured logs, metrics, audit views, alerts.

Current evidence: required read surfaces, control commands, prominent mode,
metrics/audit views and environment bearer-token authentication implemented.
Order cancellation now uses a typed idempotent application-service command and
the separate trade-sensitive bearer role; it remains available under HALT.
Runtime metrics are wired to newly-created submissions and newly-applied fills,
with duplicate retries excluded from counters and rates.
Remaining: external identity/TLS integration, Prometheus export and richer alert
routing. Keep server loopback-only until those are reviewed.

The control runner now uses the canonical durable PAPER runtime bundle. Recovery
is an authenticated explicit action that refuses uncertain fills/submissions,
issues a short-lived reconciliation attestation, and never auto-resumes.

### M7 — Shadow/live-readiness

Live-data/shadow execution without external order transmission, broker adapter
contracts/mocks, readiness checklist and independent safety audit. Live remains
disabled.

Current evidence: isolated read-only Shadow bridge with pre-connect capability
gate, canonical event integrity/protocol/sequence/dedup checks and no external
order-send operation. Native vn.py/broker process installation remains gated by
Python ABI, data/broker choice and later human authorization.

## Architecture and dependency research

- vn.py source/gap analysis: in progress.
- China trading rules: must use authoritative, date-effective exchange/tax
  sources before implementation; no model-memory constants.
- Infrastructure escalation requires benchmark evidence.

## Global validation gates

Format/lint, type checks, unit tests, integration tests, deterministic replay,
failure injection, secret scan, independent review. High-risk modules require
implementation → independent review → test review → main-agent integration.

## Current work queue

The locally executable PAPER/SHADOW checkpoint is complete. Further integration
is externally gated:

1. select and license a realtime/historical data provider;
2. select a broker and compatible isolated native-gateway Python ABI;
3. certify provider/broker normalization and reconciliation in a sandbox;
4. retain external order transmission disabled until a separate human review.

## Blockers

Licensed realtime data and a broker/native ABI selection are required for the
next integration milestone. Credentials and real order transmission remain an
intentional human gate and were not used.
