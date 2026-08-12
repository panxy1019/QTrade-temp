# QTrade v0.2 living execution plan

Baseline: commit `b502852`, frozen as **QTrade Platform v0.1 — Stable
PAPER/SHADOW Core** on 2026-08-12.

## Objective

Build on the verified safety core to deliver a strategy-research and evolution
platform, provider-neutral real-market data recording/replay, real-data-driven
PAPER/SHADOW execution, a professional Simplified-Chinese web application, and
an isolated read-only future broker bridge. Real order submission remains
structurally unavailable.

## Stable-core boundary

The existing risk, OMS, accounting, paper execution, recovery, SQLite runtime
fencing, idempotency, and reconciliation contracts are frozen. They may change
only for a reproduced defect, an explicit adapter-contract requirement, a
security issue, or a data-consistency failure. Every such change requires a
focused regression and independent safety review.

## Acceptance map

### V2-M1 — Baseline and architecture

- [x] Read the v0.1 readiness/current/master-plan records.
- [x] Verify clean `b502852` baseline and toolchain.
- [x] Record the stable-core boundary and V2 living plan.
- [ ] Commit/tag the stable baseline documentation.

### V2-M2 — Strategy and experiment platform

- [x] Durable `StrategyRegistry` with versioning and constrained lifecycle.
- [x] Parent/child lineage with hypothesis, rationale, changes, results.
- [x] Champion/challenger roles with explicit promotion history.
- [x] Unified `ExperimentRunner` and immutable experiment registry/artifacts.
- [x] Promotion gate using OOS, risk, cost, robustness, sensitivity and
  walk-forward evidence; outcomes PASS/REJECT/NEEDS_REVIEW.
- [x] Strategy health monitor and traceable research-task feedback.
- [x] Baseline families: buy-and-hold, ETF momentum rotation, trend+risk, and
  cross-sectional factor ranking.

### V2-M3 — Real-market data and replay

- [x] Provider-neutral historical/realtime protocols and capability model.
- [x] Canonical ticks with exchange/receipt times, sequence and L1 fields.
- [x] Duplicate/order/staleness/session/clock/gap/reconnect quality checks.
- [x] Raw archive, canonical Parquet persistence, configurable bar aggregation.
- [x] Deterministic replay into the same PAPER/SHADOW strategy runtime.
- [x] At least one real-provider adapter package completed without claiming a
  successful connection before legal credentials/SDK are available.
- [x] `REAL_DATA_PAPER` configuration remains `RuntimeMode.PAPER`.

### V2-M4 — Professional Chinese web platform

- [x] React + TypeScript + Vite + Ant Design + ECharts application under `web/`.
- [x] Twelve required pages with Simplified-Chinese terminology and no real-order UI.
- [x] Dark/light themes, consistent status semantics, responsive desktop-first layout.
- [x] Typed API client and FastAPI read/control contracts.
- [x] Build, tests and browser screenshot/interaction verification.

### V2-M5 — Isolated future broker integration

- [x] Canonical read-only broker protocol and capabilities/status/account/
  position/order/trade records.
- [x] Separate bridge-process contract suitable for broker-specific Python ABI.
- [x] Connect/disconnect/heartbeat/query operations only; no real-order method.
- [x] Provider and broker compatibility reports based on official/current sources.

### V2-M6 — Integrated readiness

- [x] Market data -> strategy -> portfolio -> risk -> OMS -> paper -> accounting
  integration test with recording and deterministic replay.
- [ ] Daily traceable research/PAPER report and dashboard history endpoint.
- [ ] Full pytest, Ruff, strict mypy, frontend build/tests, secret scan.
- [ ] Independent leakage, safety and security review with no open Critical/P0.
- [ ] `READINESS_REPORT_V2.md` and all requested architecture/research reports.

## External blockers

- `WAITING_FOR_DATA_PROVIDER_AUTH`: no legal RQData/Xt realtime entitlement,
  token, QMT installation or vendor SDK was supplied.
- `WAITING_FOR_BROKER_SELECTION`: no broker, sandbox, SDK or credential was supplied.

These block only authenticated real-provider and broker validation. Protocols,
mock integrations, recording/replay, strategy research, UI and reports continue.

## Verification commands

```powershell
python -W error::ResourceWarning -m pytest -q
python -m ruff check .
python -m mypy src
python -m pip check
npm --prefix web test
npm --prefix web run build
```

## Current work

V2-M6: independent review, readiness reports, dashboard report-history endpoint,
and an authenticated real-provider validation blocker that cannot be cleared
without the user's licensed data entitlement.
