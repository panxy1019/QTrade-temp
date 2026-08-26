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
as blind confirmation. Order-book buying intensity is now an explicit fail-closed
data contract rather than a one-line placeholder. It fixes 20 normalized depth
fields, share units, event/receive clocks and sequence, continuous-auction book
validity, provider-certified freshness and recorded replay reconciliation. All
six provider gates are currently false. The proposed `intensity > 1` entry is
untested and has no sell rule; no effectiveness, correlation, strategy backtest
or PAPER claim exists until an authorized five-level L2 source is selected.

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

The notebook comparison now renders its complete stored research surface:
cumulative/annualized return, active excess, Sharpe, drawdown, Rank IC, maximum
absolute correlation, stitched out-of-sample return/excess, Newey-West t and
95% interval. This supports factor-effectiveness, redundancy, robustness and
strategy-PnL comparison on one row without deriving any missing value or
reclassifying the already viewed response as independent evidence.

New notebook rows also preserve the exact read-only strategy ID and behavior
specification hash produced by the factor-plus-exit preview. The client verifies
the hash-derived ID and all immutable/unregistered/no-authority fields before
writing; legacy rows explicitly lack identity. This closes factor-to-strategy
replay lineage without inserting a StrategyVersion or changing registry state.

The effectiveness gate now clusters event returns by trading session before
claiming significance. It computes each signal day's triggered-minus-universe
mean and applies an automatic-bandwidth Newey-West mean standard error,
t-statistic and 95% interval. At least ten signal sessions and `t > 1.96` are
mandatory. The fixed 120-day result has t = -0.4800 and an interval spanning
zero, adding another explicit veto rather than counting 9,000 stock-days as
independent evidence.

Rule de-duplication and time stability are now explicit gates rather than
informal chart review. The browser can select two to eight checksum-bound local
opening rules and rerun their continuous directional scores on one identical
75-stock window; the read-only API returns a symmetric Pearson matrix and marks
`|rho| >= 0.90` pairs as redundant. The same request also evaluates each rule at
its saved body threshold, derives a two-sided p-value from the session-clustered
Newey-West t-statistic and applies Holm-Bonferroni to the full submitted family.
Observation, signal, signal-session, positive-effect, t-statistic and adjusted-p
gates must all pass; inspecting more rules therefore cannot turn unadjusted
chance findings into an effectiveness pass. The disclosed 20-session diagnostic
had zero passing rules out of three and all adjusted p-values equal to one.
Separately, every factor run is divided into
four fixed contiguous date blocks. The stability gate requires signals in every
block, at least three positive session-clustered conditional-excess block means,
and no single block above 60% of total absolute block contribution. Neither
diagnostic may retune a rule, threshold or window. The 20-session and signed
120-session three-bullish results both reached only two positive blocks, so the
existing rejection is stronger rather than repaired by another parameter search.

The factor veto is also persisted as an immutable 120-session report rather than
left as a one-off API observation. It fixes a 60-session training prefix, six
disjoint ten-session tests and thresholds `0/5/10/20 bps`, then binds the exact
BaoStock checksum, factor/API source and complete response hash. The rule lost
27.1715% across the full window and 28.3060% on the stitched tests; only one of
six folds was positive and all fixed thresholds lost money. Public API/UI reads
fail closed on any identity or result drift. This evidence rejects the current
hypothesis; it does not justify another search on the viewed window or PAPER
promotion.
Its 60 stitched test days now also enter one fixed paired stationary bootstrap:
1,000 paths, expected ten-day blocks and a fixed seed, with strategy and
benchmark sharing every sampled index. The median excess was -11.8338%, the
5th/95th percentile range was [-23.9107%, +0.4281%], and only 5.7% of paths
were positive. The API independently reconstructs that distribution from the
signed daily curve, so plausible-looking replacement values cannot survive a
re-sign. This interval describes resampling sensitivity on already viewed data;
it is not a new hypothesis test or forward observation.
The research page now also renders the report's complete 120-session equity and
drawdown curve, six fold identities, 15 sector rows, searchable 75-stock rows,
threshold stress and both gate matrices. The UI only filters presentation; it
cannot submit a filtered universe or overwrite the fixed hypothesis.

Strategy construction now includes a bounded sell-side factor rather than only
a fixed exit. On T+1, three bearish ten-minute bodies are known at the 10:00
close and can execute only at the following 10:05-labelled Bar open; otherwise
the position exits at the final 15:00-labelled Bar open, whose interval begins
at 14:55. The same rule is applied per symbol to the benchmark, and the response
hash binds trigger/time-exit counts. In the viewed 20-session window the 5 bps
variant returned +1.3245% full-window but -10.0783% in stitched walk-forward
tests, so the research gate remains failed and no PAPER authority is created.
The sell factor also has its own effectiveness gate: triggered 10:05 proceeds
are compared with the same position's 14:55 time-exit counterfactual, aggregated
once per session and evaluated with Newey-West uncertainty. Although the viewed
20-session rule's clustered advantage averaged +0.4898%, t was only 1.8326 and
the 95% interval crossed zero. The sell factor therefore fails independently of
the full-window strategy result.

Bounded factor discovery now makes the search/test boundary explicit. Exactly
104 predeclared opening rules are formed from 26 non-empty three-slot direction
tuples and thresholds 0/5/10/20 bps. A 40-session run scans only its first 20
sessions, locks one identity before the first test signal, and evaluates only
that identity on the final 20 sessions. The first real run selected
`IGNORE/BEARISH/BULLISH` at 0 bps after a positive training gate, but the locked
test returned -6.1491% total, -1.8107% excess and Newey-West t -0.5462. The
visible failed result is frozen as viewed reused history; it cannot trigger a
second search on the test window, state mutation, PAPER or external orders.
The same fixed candidate family now has an explicit selection-bias audit. Eight
contiguous five-session blocks produce all 70 symmetric four-block training and
four-block complement splits. Every split repeats the real gate/t-statistic/
conditional-excess/lower-threshold selection order and ranks that winner on the
complement with average tie ranks. The result is unfavorable: PBO is 41.43%,
only 35/70 selected complement results have positive excess, their median excess
is -0.1788%, and all three audit gates fail. Because the construction reuses the
original locked-test blocks and is not chronological, it is a descriptive
overfitting-risk audit only; the failed original holdout remains authoritative.
The unique chronological winner is also replayed under the fixed 0/5/10/20 bps
cost grid without reselection. It loses even with no modeled cost (-4.2527%) and
falls to -11.6212% at 20 bps; all four excess returns remain negative. The cost
audit therefore rules out transaction fees as the sole explanation. A combined
cost-stage discovery gate requires the chronological holdout, CSCV/PBO audit and
locked cost stress simultaneously; the disclosed run passed none of those three.
The same winner now has 15 sector-only and 15 whole-sector-exclusion test rows.
Only 4/15 sectors were positive, and every exclusion remained negative from
-7.2180% to -4.7210%, so one bad sector cannot explain the aggregate loss. The
sector-stage discovery gate adds this sector stress and passes 0/4. The same
fixed result is also recomputed for all 75 stocks: only 22/75 are positive, the
median stock return is -1.1644%, and one stock has no signal. The five largest
absolute stock returns contribute 24.79%, so only the concentration condition
passes. The five-stage gate is 0/5. A final locked-test state split uses only the
previous session's equal-weight T+1-open return sign, known by 09:35 before the
current 10:00 signal. Returns were -0.7402% after 12 negative prior sessions and
-5.4493% after eight non-negative prior sessions; both state returns were
   negative and only the first had positive excess. A final fixed-seed placebo
   audit keeps every test day's signal count unchanged while permuting signal
   ownership across that day's 75 stocks 499 times. The observed -9.1894 bp
   conditional excess was below the +0.2642 bp placebo median and +17.9292 bp
   P95; 387/499 permutations were at least as large, so the +1-corrected one-
   sided p-value was 77.60%. A causal execution-delay stress then preserves all
   326 locked signals and moves the strategy and benchmark entry together from
   10:05 to 10:10 and 10:15 at 5 bps. Returns remain negative at -6.1491%,
   -5.0013% and -4.6735%, with excess -1.8107%, -0.9354% and -0.7027%. The
   consolidated eight-stage gate is 0/8 and cannot create PAPER, state or
   external-order authority.

The locked winner now also has a causal share-capacity diagnostic. It uses only
the completed 09:55-10:00 signal-Bar volume, whose BaoStock minute unit is
shares, and applies fixed 1%/5%/10% participation rates before rounding down to
100-share lots. At 1%, all 326 locked signals support at least one lot, with
400/13,300/241,700-share minimum/lower-median/maximum capacity. The frozen
dataset contains QFQ OHLCV but no provider-native amount, so adjusted
price-times-volume is not reported as actual RMB capacity. That unavailable
currency-capacity condition fails the new stage closed; rows cannot affect
selection, and the consolidated no-reselection discovery gate is 0/9.

The intraday factor workbench now turns the PAPER handoff into an explicit
eight-gate read-only admission dossier. It binds the historical dataset identity,
research and causal-backtest outcomes, immutable strategy registration, unseen
forward SHADOW evidence, authorized realtime readiness, a fresh runtime
attestation and reconciliation readiness. The current run is `BLOCKED` at 1/8;
the dossier has no registration, runtime-control, persistence or order path.

The factor-to-strategy handoff now has a deterministic read-only identity. Its
canonical specification covers the entry factor and threshold, exit rule,
causal execution prices, equal-weight sizing, matched benchmark, costs and T+1
constraint. Observed trigger counts are excluded because they are backtest
outcomes rather than strategy parameters. The derived `adhoc-intraday-*` ID and
full SHA-256 are visible and bound into the failed PAPER registration gate, but
no strategy registry row, version or promotion authority is created.

Sequential advancement is now explicit rather than inferred from a rendered
step list. Factor effectiveness, correlation and rolling robustness must all
pass before strategy construction is research-admitted. The current rule passes
only correlation (`1/3`) and first blocks at factor validation; its strategy
specification and causal backtest remain diagnostic previews. The response
forbids reselection on the viewed window and cannot register, authorize PAPER,
mutate state or send an external order.

PAPER admission consumes that same computed next action rather than returning a
fixed registration instruction. A failed research/backtest chain therefore
points only to predeclaring a new hypothesis before new evidence. The Chinese
workbench renders the action explicitly and cannot simultaneously suggest
strategy registration for the rejected run.

The rejected-result action now has a concrete read-only preregistration
preflight. It binds the failed strategy identity, dataset checksum, viewed
window and first blocker; mirrors the exact hypothesis and experiment-run
fields; and requires a new disjoint evidence period, independent leakage
review, fixed candidate/robustness budgets, fixed costs, an immutable pre-data
specification and contamination disclosure. This creates neither a
preregistration artifact nor a registry row. A future reviewed specification
may enter the registry only at `IDEA`; later PAPER/SHADOW stages retain their
own evidence gates and `APPROVED`/`CHAMPION` remain human-authorized.

Registration itself now has a separate read-only admission preflight. It
enumerates the exact 15-field `StrategySpec`, distinguishes the behavior-only
rule hash from the still-unavailable complete registry-spec hash, and reports
nine gates. The current rejected rule sources 6/15 fields and passes 2/9 gates
(behavior identity and dataset identity only). It cannot create `IDEA`, and the
first `IDEA → EXPERIMENT` transition remains blocked until the experiment spec,
quality-passed data manifest and independent leakage review are present. The
page exposes no registration operation or runtime/state/order mutation path.

The strategy center also exposes a read-only catalog of all five implemented
baseline structures so the workflow can start from known code rather than from
an already-selected experiment row. It labels equal-weight buy-and-hold as a
comparison-only benchmark and the other four as active research baselines;
actual implementation references, entry/exit rules, required inputs, defaults
and limitations are visible. Catalog selection creates nothing: an immutable
`ExperimentRunSpec`, causal backtest and evidence gates are still required
before any separately authorized PAPER/SHADOW step.

The strategy center now carries three behavior-compatible catalog entries into
the bounded causal backtest lab. Template parameters remain visibly unfrozen;
the lab can export a complete 13-field `ExperimentRunSpec` draft and
deterministic hash bound to the verified dataset, PIT universe, implementation,
chronological split, costs and seed. It intentionally performs no immutable
write. The already visible historical test window is not new held-out evidence,
and absent independent leakage review the draft cannot create an experiment,
register a strategy or authorize PAPER/LIVE.

The draft and result are now connected by a single read-only execution request.
The API recomputes the spec, revalidates the exact dataset, runs the causal
preview and hashes the spec/result/data/template identities together. Changing
any browser field invalidates the old draft and binding. This closes an audit
gap without pretending the stateless binding is immutable persistence, an
independent leakage review or fresh held-out evidence.

Bound results can now be retained in the browser-local comparison table without
losing their template/spec/binding identity. Ordinary previews remain visibly
unbound, old schema-v1 rows load as unbound, and save-time validation rejects
any result, data or no-authority mismatch. This is removable local state only,
not an immutable experiment, registry record or runtime admission path.

The local A/B table now carries all common performance fields already present
in each saved result: total/annualized return, matched benchmark, active excess,
Sharpe, information ratio and maximum drawdown. It does not backfill missing
legacy values, and the table-level current-universe/non-PIT survivorship warning
keeps these metrics diagnostic rather than promotable.

Reloading a browser-local bound row now preserves its template identity and
exact backtest parameters, then clears prior result/draft/binding state and
starts a fresh dataset-bounded run-spec form. The old spec/binding hashes remain
historical labels only because the local summary did not persist the original
13 fields. Ordinary rows remain unbound, and incompatible local identity/params
fail closed rather than selecting a misleading template.

The local A/B table now exports one deterministic, dataset-bound JSON checkpoint
containing every saved parameter set, common metric, preview hash and optional
template/spec/binding identity. There is no export timestamp, server write or
import path: unchanged state serializes identically, while the artifact remains
portable read-only browser evidence rather than synchronization, registration,
promotion or PAPER/LIVE authority.

Adjustable previews now include an engine-matched equal-weight Buy-and-Hold
benchmark. It uses only first-decision-known symbols and the same window,
capital, reserve, costs, slippage and next-open execution as the strategy. The
API/UI expose aligned benchmark equity/drawdown, benchmark return, cumulative
excess and information ratio. This completes the common metric surface while
retaining the explicit current-universe survivorship boundary.

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
JavaScript asset at 429.75 KB.

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
