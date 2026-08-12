# Current state

Last updated: 2026-08-12

- Goal: deliver a tested A-share/ETF research, backtest, paper, and shadow
  platform while real-money trading remains disabled.
- Completed: environment audit; architecture/safety decisions; in-process safety
  kernel; versioned data/PIT contracts; research/portfolio baselines; shared-path
  next-bar backtest; control plane/dashboard; reconciliation/recovery contracts;
  authoritative China rule-source registry; vn.py gap analysis.
- Current phase: persistence and integrated paper runtime.
- Current milestone: M2/M5 durable analytical and operational state.
- Acceptance evidence: 49 tests passed, 93% coverage, Ruff clean, strict mypy
  clean on Python 3.14.6 (2026-08-12).
- Environment: Windows 11/PowerShell 5.1; Python 3.14.6; Git 2.53; no Docker,
  database service, vn.py, or project dependencies detected.
- Known limitations: current OMS/ledger/reservations are in-process and do not
  claim crash-safe exactly-once semantics; audit hash chain is tamper-evident but
  lacks an external tail anchor; data adapters and operational database remain
  to be integrated; paper matching remains deliberately simple.
- vn.py: core 4.4.0 selected behind adapters; native broker wheels require a
  separate broker-specific Python ABI after the user selects a broker.
- Next: Parquet/DuckDB data adapter and experiment artifacts; durable SQLite or
  PostgreSQL operational journal; failure replay; metrics/factor evaluation.
- Live trading: disabled and structurally unavailable.
