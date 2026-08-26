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
- [x] Commit/tag the stable baseline documentation.

### V2-M2 — Strategy and experiment platform

- [x] Durable `StrategyRegistry` with versioning and constrained lifecycle.
- [x] Parent/child lineage with hypothesis, rationale, changes, results.
- [x] Champion/challenger roles with explicit promotion history.
- [x] Unified `ExperimentRunner` and immutable experiment registry/artifacts.
- [x] Required evidence artifacts are confined, checksum-bound and automatically
  registered with their PASSED dataset lineage.
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
- [x] Symmetric RQData/XtData data-only bridge adapters and credential-free
  provider-selection diagnostic.
- [x] Native optional `rqdatac 3.6.3` transport with authorized WebSocket
  handshake, snapshots, raw 1d/1m history, Tick/minute normalization and a
  read-only operational probe; authenticated evidence remains externally blocked.
- [x] `REAL_DATA_PAPER` configuration remains `RuntimeMode.PAPER`.

### V2-M4 — Professional Chinese web platform

- [x] React + TypeScript + Vite + Ant Design + ECharts application under `web/`.
- [x] Twelve required pages with Simplified-Chinese terminology and no real-order UI.
- [x] Dark/light themes, consistent status semantics, responsive desktop-first layout.
- [x] Typed API client and FastAPI read/control contracts.
- [x] Build, tests and browser screenshot/interaction verification.
- [x] Audit all twelve routes and their API/evidence dependencies; restore local
  static/API-prefix compatibility and publish the complete verified baseline,
  walk-forward and robustness evidence set.
- [x] Close FE-01 through FE-25: isolate optional snapshot failures, add shell
  refresh/route/responsive behavior, expose complete research tables and JSON
  exports, and add loopback-only runtime/cancel controls while keeping the
  public deployment read-only. Isolate page render failures without losing the
  shell, reduce every production JavaScript chunk below the warning boundary,
  bind canonical runtime metrics and add bounded audit triage/export.

### V2-M5 — Isolated future broker integration

- [x] Canonical read-only broker protocol and capabilities/status/account/
  position/order/trade records.
- [x] Separate bridge-process contract suitable for broker-specific Python ABI.
- [x] Connect/disconnect/heartbeat/query operations only; no real-order method.
- [x] Provider and broker compatibility reports based on official/current sources.

### V2-M6 — Integrated readiness

- [x] Market data -> strategy -> portfolio -> risk -> OMS -> paper -> accounting
  integration test with recording and deterministic replay.
- [x] Daily traceable research/PAPER report schema/store and hash-verified dashboard
  history endpoint, plus optional trusted-config local end-of-day scheduling with
  explicit SSE sessions, Asia/Shanghai cutoff and read-only status. Public Vercel
  remains unconfigured and cannot write reports.
- [x] Low-cardinality Prometheus 0.0.4 runtime export with non-finite rejection,
  no strategy-heartbeat labels and no change to execution authority.
- [x] Full pytest, Ruff, strict mypy, frontend build/tests, dependency and secret scan.
- [x] Independent leakage/correctness review with no open Critical/P0; P1 maturity
  limitations remain recorded in the reports.
- [x] `READINESS_REPORT_V2.md` and all requested architecture/research reports.

## External blockers

- `WAITING_FOR_DATA_PROVIDER_AUTH`: the RQData SDK is installed locally, but no
  legal RQData realtime entitlement/token was supplied; no entitled QMT/XT
  installation was supplied.
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

### V2-M7 — AKShare credential-free real-data acceptance

- [x] Pin and isolate AKShare 1.18.84 behind a provider adapter.
- [x] Require explicit noncommercial research-use acknowledgement.
- [x] Add controlled ETF snapshot/history polling, normalization, health and circuit tests.
- [x] Add record -> DQ -> finalized bar -> causal PAPER -> replay integration test.
- [x] Obtain a successful daily-history endpoint/schema/quality probe on the operating host.
- [x] Freeze a 20-50 liquid ETF universe from recorded real data, explicitly
  classified as current/survivorship-biased rather than PIT.
- [ ] Obtain a successful snapshot endpoint/schema/freshness probe.
- [x] Normalize and live-probe raw A-share and major-index daily history.
- [ ] Normalize and accept A-share and major-index snapshots/minute endpoints.
- [ ] Run the first complete real trading-day PAPER acceptance and replay comparison.
- [x] Produce bounded real-data baseline/sensitivity evidence; no strategy met
  promotion requirements, so no Champion/Challenger role was assigned.
- [x] Bind strategy/research/experiment/backtest Chinese dashboard pages to
  comparison/record/result/artifact-verified backend records with explicit
  NOT_ELIGIBLE labeling and no invented StrategyVersion lineage.
- [x] Add a bounded, ephemeral parameterized backtest preview over the verified
  historical Parquet dataset, with a Chinese web form, deterministic result hash,
  no registry/PAPER mutation and mandatory non-PIT/NOT_ELIGIBLE labelling.
- [x] Add a typed three-interval opening-direction factor builder with
  bullish/bearish/ignore predicates, ALL/ANY combination and one body threshold;
  preserve fixed timestamps, reject arbitrary code/features and route every
  result through the existing causal diagnostics and promotion gates.
- [x] Add one typed T+1 exit factor: three completed ten-minute bearish bodies
  known at 10:00, executed at the next 10:05-labelled Bar open, otherwise exited
  at the final 15:00-labelled Bar open (14:55 interval start). Bound its threshold,
  use the same per-symbol rule for the benchmark, persist its browser-local
  identity and keep every result stateless and `NOT_ELIGIBLE`.
- [x] Validate the exit factor independently of total strategy PnL by comparing
  each triggered 10:05 exit open with its 14:55 time-exit counterfactual, cluster
  timing advantage by session and gate on trigger count, session count, positive
  mean and Newey-West significance before any research pass is possible.
- [x] Add a checksum-bound browser-local notebook for intraday factor summaries,
  comparison, deletion and parameter reload; reject malformed/safety-relaxed
  records and require a new run after reload without creating formal lineage.
- [x] Cluster intraday event effectiveness by signal-bearing trading day and add
  automatic-bandwidth Newey-West standard error, t-statistic, 95% interval and
  minimum-session/significance hard gates across API, UI and signed evidence.
- [x] Split each intraday factor window into four fixed contiguous blocks, report
  session-clustered conditional excess by block, and gate on positive-block ratio
  plus single-period contribution concentration without allowing block outcomes
  to retune the rule or threshold.
- [x] Re-evaluate each selected local rule at its saved body threshold on one
  identical 75-stock window, compute two-sided Newey-West p-values and apply
  Holm-Bonferroni across the 2–8 rule family before displaying an effectiveness
  pass; keep continuous-score correlation as an independent de-duplication gate.
- [x] Add a fixed paired stationary bootstrap over the 60 stitched out-of-sample
  strategy/benchmark days in the signed 120-session factor report; independently
  recompute the 1,000-path excess distribution in the API and expose its median,
  5%/95% interval and positive-excess probability without treating it as blind.
- [x] Extend the preview to bounded momentum/reversal/low-volatility comparison
  and a checksum-bound browser-local experiment notebook with save, compare,
  parameter reload and delete; keep all records outside formal strategy lineage.
- [x] Allow `run_control_plane.py --historical-manifest` to bind a newly ingested
  signed dataset to the dashboard without a source edit.
- [x] Run a bounded price-factor discovery and validation-only parameter search
  on a longer quality-passed real ETF dataset, lock one active candidate for the
  confirmatory window, retain its negative result and expose only hash-verified
  factor/validation/test evidence in the Chinese research page.
- [x] Freeze and run a 36-candidate repeated robustness search across eight
  2024-2025 quarterly windows, then apply doubled-cost, one-parameter-neighbour,
  deterministic-universe and stationary-bootstrap hard gates to every
  window-eligible candidate. Keep 2026 structurally excluded until a labelled
  polluted audit and expose only independently verified `NOT_ELIGIBLE` evidence.
- [x] Freeze a current CSI 300/SW 15-by-5 stock universe before price access,
  ingest 2018-2026 uniform adjusted history with signed resumable caches, and
  transfer the locked ETF factor without stock-side tuning across annual,
  sector-only, leave-one-sector-out, doubled-cost and stationary-bootstrap hard
  gates. Expose the independently verified result while retaining the current-
  universe/HFQ/suspension/price-limit `NOT_ELIGIBLE` boundary.
- [x] Add immutable daily target publication, persistent one-minute risk-only
  correction, recovery cooldown, multi-symbol recorded tick routing and a
  fail-closed Chinese readiness monitor. Keep it `UNCONFIGURED` until an
  authorized realtime RQData/XtData bridge is supplied.
- [x] Add a signed same-day breadth probe for all 75 frozen stocks across the 15
  sectors, independently verify cross-stock/per-sector finalized-minute coverage
  in the API, and expose a bounded Chinese table without treating it as realtime
  or PAPER input.
- [x] Chain multiple same-day detail/breadth checkpoints, verify monotonic source
  progression and the latest evidence identities, and show the bounded timeline
  without dropping or repairing a detected regression.
- [x] Preserve each signed 75-stock forward-source coverage diagnostic in a
  previous-hash chain, verify its exact provider/lock identity and expose the
  `41 → 47 → 52 → 69` progression. Bind every checkpoint to a complete immutable
  content-addressed report so a wholly resigned summary cannot invent an old
  point, without treating the chain as formal `0/20` evidence.
- [x] Pre-register the locked development overlay's future complete-session
  collection and evaluation contract, bind it to collector/evaluator/engine
  hashes, require 75×48 bars plus an exact frozen-anchor match, and publish the
  honest `0/20 INCOMPLETE` state without granting runtime authority.
- [x] Collapse eight public long-minute evidence reads into one verified bundle
  that opens the 24 MB Parquet once, while retaining standalone endpoints,
  independent report validation and typed fail-closed frontend sections.
- [x] Add a fixed 104-candidate opening-factor discovery contract with a 20/20
  chronological train/test split, lock one training identity before any test
  signal, expose the complete failed test in the Chinese workbench and prohibit
  reusing that viewed test window as an independent confirmation or PAPER gate.
- [ ] Accumulate the first 20 immutable post-lock complete BaoStock sessions and
  retain the predeclared result without post-read parameter changes.
- [ ] Record and replay the first complete authorized multi-stock minute session,
  then compare replay targets, orders and fills byte-for-byte with the forward
  SHADOW/PAPER trace.
- [ ] Obtain a PIT/licensed ETF universe and a genuinely unseen forward period
  before starting another factor-tuning hypothesis.

Authenticated RQData/XT validation remains a later authoritative-feed option.
Native broker work remains blocked on broker selection and sandbox access.
