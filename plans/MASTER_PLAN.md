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

### M1 — Deterministic safety kernel (in-process acceptance complete)

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

Known limitations: in-memory state cannot provide crash-safe exactly-once
semantics; uncertain fill replay blocks for reconciliation. JSONL chaining is
tamper-evident but not WORM storage. Durable completion continues under M5.

### M2 — Versioned data and instrument foundation (contracts complete; adapters in progress)

Instrument master, date-effective trading rules, calendars, bars, corporate
actions, memberships, checksums, quality gates, point-in-time queries, local
Parquet/DuckDB adapter and fixtures with provenance.

Current evidence: source-backed effective-date rule model, PIT memberships and
corporate actions, multi-check quality gate, checksum-verified Parquet/DuckDB
bar adapter. Remaining: durable metadata catalog, calendars/status, normalized
corporate-action adjustments and larger fixture coverage.

### M3 — Research, factors, portfolio, and evaluation

Chronological splits/walk-forward experiment records, baseline factors and
strategies, portfolio constraints, cost-aware metrics, reproducibility.

Current evidence: chronological non-overlap gates; buy/hold and moving-average
baselines; top-K/weight/cash constraints; portfolio metrics and factor
IC/RankIC/quantiles/turnover/spread; immutable hashed experiment record; tested
synthetic end-to-end workflow. Remaining: walk-forward orchestrator, additional
baselines, ICIR over periods, and vnpy.alpha adapter.

### M4 — Integrated event-driven backtest

Same strategy/portfolio/risk/OMS/execution contracts; costs, slippage, partial
fills, cancellation, rejection, accounting, benchmark evaluation.

Current evidence: shared pipeline integrated, signals execute at the following
bar open, input ordering/identity and single-use behavior tested. Remaining:
dataset manifest gate, broader metrics, liquidity/matching models and report.

### M5 — Durable paper runtime and recovery

Persistence, reconciliation, replay, restart protocol, duplicate/out-of-order
events, disconnect/freeze/database failure injection.

Current evidence: SQLite FULL/WAL journal, durable account/order/reservation/fill
markers, restart restoration, mandatory HALTED restart, uncertain fill blocking,
incident-to-HALT coupling and recovery sequence tests. Remaining: one atomic
external gateway reconciliation/replay and PostgreSQL multi-process adapter.
Inbound fill APPLIED + projected account/OMS/reservations now commit atomically;
a crash-after-commit test restores exactly one economic effect.

### M6 — Control plane, dashboard, and observability

FastAPI with read/control/trade-sensitive role separation, prominent mode,
structured logs, metrics, audit views, alerts.

Current evidence: required read surfaces, control commands, prominent mode,
metrics/audit views and environment bearer-token authentication implemented.
Remaining: external identity/TLS integration, Prometheus export and richer alert
routing. Keep server loopback-only until those are reviewed.

The control runner now uses the canonical durable PAPER runtime bundle. Recovery
is an authenticated explicit action that refuses uncertain fills/submissions,
issues a short-lived reconciliation attestation, and never auto-resumes.

### M7 — Shadow/live-readiness

Live-data/shadow execution without external order transmission, broker adapter
contracts/mocks, readiness checklist and independent safety audit. Live remains
disabled.

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

1. Complete instrument master, calendars/status, and sellability accounting.
2. Build a reproducible import → research → backtest → report workflow.
3. Expand failure injection and deterministic paper execution plans.
4. Add vn.py process adapter and canonical event protocol.

## Blockers

No external blocker for paper/shadow development. Real broker access remains an
intentional human gate and is not needed for current milestones.
