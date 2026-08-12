# Current state

Last updated: 2026-08-12

- Goal: deliver a tested A-share/ETF research, backtest, paper, and shadow
  platform while real-money trading remains disabled.
- Completed: environment audit; architecture/safety decisions; in-process safety
  kernel; versioned data/PIT contracts; research/portfolio baselines; shared-path
  next-bar backtest; control plane/dashboard; reconciliation/recovery contracts;
  authoritative China rule-source registry; vn.py gap analysis.
- Current phase: integrated paper/shadow platform hardening.
- Current milestone: M5/M6 failure recovery, control, and observability.
- Acceptance evidence: 84 tests passed, Ruff clean, strict mypy
  clean on Python 3.14.6 (2026-08-12); end-to-end synthetic workflow executed.
- Environment: Windows 11/PowerShell 5.1; Python 3.14.6; Git 2.53; no Docker,
  database service, vn.py, or project dependencies detected.
- Known limitations: current OMS/ledger/reservations are in-process and do not
  claim crash-safe exactly-once semantics; audit hash chain is tamper-evident but
  lacks an external tail anchor; data adapters and operational database remain
  to be integrated; paper matching remains deliberately simple.
- vn.py: core 4.4.0 selected behind adapters; native broker wheels require a
  separate broker-specific Python ABI after the user selects a broker.
- Next: historical ingestion and data-quality hardening; additional research
  baselines; paper runtime failure replay and orchestration.
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
  injection, bearer-token control authentication, and reproducible import →
  backtest → metrics → immutable experiment workflow are implemented.
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
- Live trading: disabled and structurally unavailable.
