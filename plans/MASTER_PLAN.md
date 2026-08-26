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
The optional JSONL audit sink is hash-chained but not externally/WORM anchored;
the operational SQLite audit table is durable and schema-validated, not hash-chained.

### M2 — Versioned data and instrument foundation (contracts complete; adapters in progress)

Instrument master, date-effective trading rules, calendars, bars, corporate
actions, memberships, checksums, quality gates, point-in-time queries, local
Parquet/DuckDB adapter and fixtures with provenance.

Current evidence: source-backed effective-date rule model, PIT memberships and
corporate actions, multi-check quality gate, checksum-verified Parquet/DuckDB
bar adapter, and strict checksum-versioned canonical CSV importer. Temporal and
jump validation is grouped per instrument and rejects non-finite values.
Parquet reads reject a checksum-valid file when any row-level `data_version`
differs from the supplied manifest, preserving dataset lineage after resigning.
They also rerun the write-time quality gate on physical row order before sorting
returned Bars, preventing a resigned file from repairing disorder during read.
The exact Arrow column contract is validated before row conversion, so renamed,
missing, extra, reordered or type-drifted fields fail closed as integrity errors.
The shared gate also binds SSE/SZSE timestamps to their declared trading day in
China market time, preventing a date-label drift from contaminating session
splits, factors or minute-overlay replay.
Parquet dataset paths are resolved before I/O and must remain a direct child of
the configured root, closing traversal, absolute-path and symlink escape paths.
Checksum-valid payloads that still fail filesystem reads or Arrow decoding are
reported through the same dataset-integrity boundary used by every consumer.
Schema-valid zero-row payloads are also rejected, keeping the write-time non-empty
invariant intact after a file is modified and resigned.
The shared Bar gate rejects blank or whitespace-padded identities and requires
canonical six-digit symbols for SSE/SZSE before instrument grouping begins.
Schema-correct rows with null or syntactically invalid timestamp/date/decimal
content are normalized to the dataset-integrity boundary before semantic checks.
Parquet writes complete and checksum their payload in a same-filesystem staging
directory before atomically publishing the immutable data-version directory;
failed encodes leave no visible partial dataset and the same version can retry.
The staged file and directory plus the final storage-root entry are fsynced in
publication order before the writer returns a PASSED manifest.
Public deployment review reads a format-validated full Git SHA, ref and target
environment from Vercel's documented system variables through a bounded
read-only endpoint; malformed or missing identity is reported as unavailable.
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
The Chinese PAPER, positions, orders/trades and risk pages now bind to the
durable runtime. API contracts expose strategy/portfolio/order identity,
restart-stable APPLIED simulated fills, dated sellability and recomputable
cost-basis exposure. Current equity, market value, unrealized PnL and drawdown
remain unavailable until an authorized fresh market-price source exists.
The frontend/API audit now has a tracked acceptance matrix. Local static serving
supports both root and `/web` entrypoints plus the `/api` prefix emitted by a
Vercel-mode build. The public evidence bundle also carries the complete verified
v4 experiment catalog, three-fold walk-forward and 14-scenario robustness chain,
so all research pages receive the same accepted evidence rather than silent
empty placeholders.
The web client reads these PAPER fields from one dispatch-gated atomic snapshot;
concurrent fill/read regression proves it cannot observe a half-installed
account/OMS projection.
The overview page distinguishes PAPER cash/realized PnL from account equity and
uses only verified historical research analytics for its chart, with an explicit
non-PIT/NOT_ELIGIBLE/non-PAPER label.
Daily report history now validates envelope hash plus identity and summary
schema before rendering; malformed/tampered artifacts are omitted. Automatic
end-of-day generation is now implemented as an optional local worker whose exact
data/strategy identities and bounded open-session horizon are bound at process
startup rather than supplied by an HTTP caller. Missing calendar coverage and
identity conflicts fail closed; public Vercel remains unconfigured and read-only.
Report scans are bounded by directory count, artifact bytes and identity/version
field sizes so periodic dashboard polling cannot amplify malformed report data.
Audit reads now use a bounded, chronological SQLite journal view rather than the
process-local `AuditLog` cache. Restart regressions prove both the control-plane
history and daily incident summaries retain durable events; report assembly is
performed under the account-wide dispatch gate to avoid a half-applied fill.
The monitor page consumes that durable view, separates ERROR/CRITICAL alerts,
and preserves the rest of the dashboard when audit validation returns 503 while
displaying an explicit unavailable state instead of a misleading zero. It now
binds the canonical metric names, supports bounded audit search/severity/event
triage and exports the filtered view. `/metrics/prometheus` provides finite,
low-cardinality Prometheus 0.0.4 counters/gauges and deliberately omits strategy
heartbeat identities. Remaining: external identity/TLS integration and outbound
alert routing. Keep server loopback-only until those are reviewed.

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

The locally executable PAPER/SHADOW safety checkpoint is complete. V2 strategy,
market-data, research-governance, report and Chinese-web foundations are tracked
in `plans/V2_MASTER_PLAN.md`; their maturity limits are recorded in
`docs/READINESS_REPORT_V2.md`. Further real-market integration is externally gated:

The historical research surface now also includes a bounded interactive preview.
It accepts adjustable momentum/reversal/low-volatility, risk and cost parameters,
rereads the selected signed Parquet dataset, returns a deterministic result hash
and never persists or promotes the result. A checksum-bound browser-local notebook
can retain up to 30 parameter/metric summaries for comparison and rerun, without
creating a server experiment or PAPER input. The control-plane runner accepts an
explicit historical manifest path, so newly ingested versions can be evaluated
without editing source. This usability path does not weaken immutable experiment,
held-out or PAPER readiness gates.

The intraday research surface now has one concrete end-to-end factor workflow.
The opening three-ten-minute-bullish hypothesis is defined against native
five-minute Bar intervals, checked cross-sectionally across 75 stocks and 15
sectors, compared with related opening features, converted into an explicit
next-Bar/T+1 equal-weight strategy, and replayed against a same-horizon benchmark
under four cost levels. Its first 20-session result is negative and remains a
rejected, current-universe/QFQ non-PIT hypothesis. A fixed four-threshold
expanding-window diagnostic now selects only from earlier sessions and evaluates
two disjoint five-session folds; both folds and all four fixed thresholds are
negative. It is explicitly reused-history robustness evidence, not an
independent confirmation. Future tuning must use bounded development windows and
a separately locked forward evaluation; the viewed window cannot be relabelled
as blind confirmation. Order-book buying intensity is only a data contract until
an authorized five-level L2 source is selected.

Factor construction now includes one deliberately bounded extension of that
workflow: the three fixed completed intervals accept bullish, bearish or ignored
direction predicates, joined by ALL/ANY with a shared body threshold. It neither
evaluates arbitrary formulas nor changes the 10:00 decision, next-Bar entry or
T+1 exit contract. The immutable three-bullish preset and every custom preview
flow through the same diagnostics and hard gates; no browser selection can
register a strategy or grant PAPER/LIVE authority.

The factor workbench also keeps an optional browser-local comparison notebook.
Its bounded summaries bind the dataset identity and deterministic result hash,
retain the rule, standard metrics, stitched diagnostic and both hard gates, and
fail closed on malformed or safety-relaxed local data. Parameter reload clears
the old result and requires a new run; the notebook has no server write,
StrategyVersion, promotion or cross-device synchronization semantics.

The factor veto is also persisted as an immutable 120-session report rather than
left as a one-off API observation. It fixes a 60-session training prefix, six
disjoint ten-session tests and thresholds `0/5/10/20 bps`, then binds the exact
BaoStock checksum, factor/API source and complete response hash. The rule lost
27.1715% across the full window and 28.3060% on the stitched tests; only one of
six folds was positive and all fixed thresholds lost money. Public API/UI reads
fail closed on any identity or result drift. This evidence rejects the current
hypothesis; it does not justify another search on the viewed window or PAPER
promotion.
The research page now also renders the report's complete 120-session equity and
drawdown curve, six fold identities, 15 sector rows, searchable 75-stock rows,
threshold stress and both gate matrices. The UI only filters presentation; it
cannot submit a filtered universe or overwrite the fixed hypothesis.

The twelve-route frontend completeness audit is closed for every capability the
repository can implement without new authority. Optional evidence failures no
longer erase the full snapshot; all research inventories and minute-symbol
states are inspectable; results and PAPER snapshots can be exported; and local
runtime/cancel controls require memory-only bearer tokens. The Vercel hostname
continues to hide mutation controls and fail all sensitive POSTs closed. Licensed
realtime data, persistent public PAPER, broker reconciliation and new PIT/forward
evidence remain explicit external blockers rather than UI placeholders.

The forward-data surface now separates provider availability from formal
evaluation. A signed, no-Bar-persistence BaoStock diagnostic checks all 75 locked
stocks after close and exposes exact anchor/target completeness through the API,
research page and data-center inventory. Its final 75/75 ready snapshot remains outside
the 0/20 metric and cannot authorize PAPER, LIVE or external orders. A signed
previous-hash chain preserves the
41 → 47 → 52 → 69 → 74 → 75 publication sequence so a later coverage regression remains visible.
Each point also binds a complete content-addressed 75-stock release; public reads
fail closed if an archive is missing, replaced or routed through a symlink.

The shell now also isolates page render failures inside the content region, so
navigation and the explicit no-live boundary remain available during recovery.
ECharts registers only the line, bar, candlestick and supporting components used
by the product, while React and zrender are stable cache chunks; both production
build modes complete without a chunk-size warning and keep the largest minified
JavaScript asset at 429.02 KB.

The factor-research path now predeclares five causal price factors, keeps every
forward label inside the discovery period, bounds the active validation grid and
locks one validation winner before confirmatory access. The first active winner
failed the 2026 confirmation and lagged Buy-and-Hold after costs. The verified
negative result is visible in the Chinese research page and remains
`NOT_ELIGIBLE`; no further parameter search may use that viewed window.

The credential-free research path now includes a checksum-bound 30-ETF AKShare
dataset and a real-data, three-fold held-out walk-forward result. Selection stops
at each validation boundary, complete fold ranges are disjoint, and only the
selected candidate is evaluated on its test window. The negative result remains
promotion-ineligible; this is evidence against the current candidates, not a
reason to weaken thresholds or assign a Champion.

The Chinese market page now displays the latest complete historical daily bar
for every symbol in that verified dataset, with search and pagination. The API
fails closed on manifest/checksum/schema/symbol/OHLC validation and labels the
rows historical-only; this does not satisfy realtime snapshot or PAPER readiness.
Selecting a row also loads a bounded, checksum-bound historical series and
renders real daily candlesticks plus volume. Physical per-symbol disorder is
rejected and stale charts disappear when the verified dataset identity changes.

The 75-stock intraday breadth observation now also exposes an independently
recomputed directional cross-section from signed first-open, previous-close and
latest-close fields. Global and 15-sector relative-open/latest-minute counts and
equal-weight returns are visible on the market page, but remain observation-only:
they are not candidates, tuning inputs, PAPER state or promotion evidence.
The bounded continuity chain preserves those direction summaries from an
explicit first sequence without rewriting older checkpoint hashes, so subsequent
refreshes can be compared as a signed intraday time series.
Latest-minute values use signed basis points in the browser to preserve the
direction of sub-basis-percent moves that two-decimal percentages would hide.
The continuity table derives a signed participation/return delta from adjacent
direction-bearing checkpoints without adding a strategy or persistence path.

The backtest analytics surface now validates the Buy-and-Hold record/result and
curve artifacts independently from the exploratory leader, requires exact
session alignment, and displays strategy, benchmark, excess return and drawdown.
This real comparison remains non-PIT and promotion-ineligible.

The same fail-closed artifact chain now exposes the exploratory leader's monthly
returns, simulated trade ledger and terminal positions. Artifact hashes, trade
chronology, finite positive economics and experiment-universe membership are
validated before the Chinese dashboard renders them; they are not broker trades
or current PAPER state.

Risk and run metadata are also checksum-bound and cross-validated against the
equity/drawdown curve, the immutable fee rate, each simulated trade cost and
notional-derived turnover. The UI presents full-period
drawdown/turnover/cost and the causal next-open engine contract
as historical research diagnostics only.

The strategy, research and experiment surfaces now require the same
comparison -> immutable record -> deterministic result -> artifact hash chain
for all 11 v4 candidates. The UI exposes real models, parameters, held-out
splits and record hashes while explicitly refusing to call these unregistered
records Champion, Challenger, deployed strategy or lineage. Legacy spec hashes
remain format-checked rather than independently recomputed because their full
run-spec fields are absent from the report.

1. select and license a realtime/historical data provider;
2. select a broker and compatible isolated native-gateway Python ABI;
3. certify provider/broker normalization and reconciliation in a sandbox;
4. retain external order transmission disabled until a separate human review.

## Blockers

Licensed realtime data and a broker/native ABI selection are required for the
next integration milestone. Credentials and real order transmission remain an
intentional human gate and were not used.
