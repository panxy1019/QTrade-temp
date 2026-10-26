# Architecture decisions

## ADR-165 — Qlib is the isolated daily research engine

Status: accepted, 2026-10-25, at the user's request.

Reuse QTrade's checksummed raw stock history, data gates, provenance, archives
and public observation transport. Source/reuse audit is in `docs/QLIB_BASELINE.md`.
Use Qlib Alpha158, LGBModel, DatasetH, Recorder and built-in backtest components;
vn.py execution components are outside this research migration. Isolate Python
and dependencies from collectors and the public Vercel runtime.

Recover amount and effective-date HFQ factors from saved public-source responses,
reconcile existing OHLCV, normalize prices and inverse-adjust volumes. Reject
unexplained source drift; do not fill suspension bars or fabricate VWAP. Current
universe bias and unverified historical corporate-action/ST/limit/halt data stay
visible. Conservative execution proxies are documented, not called certified rules.

Fix time segments, seed, costs and model parameters before examining the test
result. Persist the actual source revision, environment, model, signals, portfolio
reports and every input/artifact checksum before publishing COMPLETE. Historical
test results remain post hoc non-PIT diagnostics. Publish a bounded result through
the existing data-only channel; show publication age separately from result dates.
No public research write endpoint, automatic promotion or trading path is added.
LIVE_TRADING_ENABLED remains false.

## ADR-001 — Bootstrap with a framework-independent safety kernel

Status: accepted, 2026-08-12.

The workspace is empty and only Python 3.14 is installed. vn.py compatibility
must be established from source before integration. Core domain, risk, OMS,
paper execution, accounting, and audit contracts therefore use the standard
library first, behind explicit adapter protocols. This does not replace vn.py;
it creates stable seams for reuse after the gap analysis.

## ADR-002 — Live mode is structurally unavailable

Status: accepted, 2026-08-12.

Configuration defaults are not sufficient protection. Runtime mode initially
contains BACKTEST, PAPER, and SHADOW only. A future LIVE adapter requires a new
reviewed implementation, explicit authorization, reconciliation, and an
independent security gate. `LIVE_TRADING_ENABLED` remains false.

## ADR-003 — Local-first persistence

Status: accepted, 2026-08-12.

Use Parquet/DuckDB for analytical data and defer PostgreSQL runtime persistence
until it is available. Do not add TimescaleDB, ClickHouse, or QuestDB without a
measured bottleneck.

## ADR-004 — Integrate vn.py through adapters; retain Q safety ledger

Status: accepted, 2026-08-12.

Source audit supports reusing vn.py 4.4 core/event/gateway/data contracts and
select application modules. `vnpy_paperaccount` does not meet this project's
cash, fee, partial-fill, idempotency, and recovery requirements, so it is a
reference rather than the authoritative paper ledger. Broker gateways are
future adapters behind Q risk/OMS/reconciliation. See
`docs/VNPY_GAP_ANALYSIS.md`.

## ADR-005 — Shadow integration is read-only and capability-gated

Status: accepted, 2026-08-12.

An isolated vn.py process may provide versioned canonical market/account/status
events only after declaring `external_order_transmission=false`. The capability
is checked before connection, and the bridge rejects mutation, protocol mismatch,
out-of-order sequence, duplicate IDs and command event types. SHADOW orders remain
inside Q's `PaperGateway`; no adapter send operation exists.

## ADR-006 — Freeze b502852 as the v0.1 stable core

Status: accepted, 2026-08-12.

Risk, OMS, accounting, paper execution, recovery, persistence fencing,
idempotency and reconciliation at commit `b502852` form the stable core. QTrade
v0.2 work is additive around explicit protocols. Core changes require a
reproduced defect or contract/safety/data-consistency need, focused regression,
and independent review.

## ADR-007 — Real market data never changes execution authority

Status: accepted, 2026-08-12.

`REAL_DATA_PAPER` is a configuration of `RuntimeMode.PAPER`, not a new runtime
authority. Authentic ticks/bars may drive strategies, but orders still traverse
portfolio, risk, OMS, `PaperGateway`, accounting and recovery. `RuntimeMode.LIVE`
and externally transmitting broker commands remain absent.

## ADR-008 — Native providers and brokers live behind process protocols

Status: accepted, 2026-08-12.

Vendor SDKs and native vn.py gateways must not be imported into the Python 3.14
core. Provider/broker bridges use isolated environments and versioned canonical
protocols. The broker protocol in this goal is read-only and deliberately has no
order-submit or cancel operation.

## ADR-009 — Realtime PAPER dispatch requires revocable market-data authority

Status: accepted, 2026-08-12.

Provider connection alone does not authorize a signal-driven PAPER write. A
session/reconnect/data-version-bound readiness attestation must remain fresh at
the tick dispatch point. Disconnect, data-quality failure or identity mismatch
invalidates that authority and incident-latches the PAPER runtime. Runner
checkpoints bind exact strategy version/spec hash, provider, data version, bar
interval and trading-rule source identity.

## ADR-010 — Promotion evidence is durable authority, not a caller boolean

Status: accepted, 2026-08-12.

Champion assignment cannot trust a supplied `promotion_passed` flag. Promotion
evidence is evaluated by the repository policy, hashed and persisted under an
immutable evidence ID. Only an APPROVED strategy with a stored PASS decision may
be assigned CHAMPION; post-SHADOW lifecycle transitions remain human-authorized.

## ADR-011 — PAPER valuation remains unavailable without a fresh price authority

Status: accepted, 2026-08-13.

Persistent PAPER cash, quantities, cost basis, realized PnL, orders, fills and
date-effective sellability may be shown directly from the operational journal.
Without an authorized fresh market-price source, account equity, market value,
unrealized PnL, weights and drawdown must remain explicitly unavailable.
Historical research closes cannot be relabelled or substituted as current PAPER
valuation. The interim risk summary is named and documented as position cost
basis plus remaining BUY limit-price notional, not realtime market exposure.

## ADR-012 — Interactive parameter changes are ephemeral research previews

Status: accepted, 2026-08-24.

The web application may submit bounded strategy parameters to a read-authority
historical preview endpoint. The endpoint must revalidate the signed dataset,
use the causal close-decision/next-open research engine, perform no persistent
write, and expose a deterministic result hash. A preview is always current-
universe/non-PIT, ad hoc and `NOT_ELIGIBLE`; it cannot create or transition a
`StrategyVersion`, become promotion evidence, authorize PAPER, or affect any
execution gateway. Formal experiments continue through the immutable experiment,
walk-forward, robustness and promotion-governance path.

## ADR-013 — Factor tuning is bounded before one locked active test

Status: accepted, 2026-08-25.

Price-only factor discovery may use causal daily histories and forward labels
that end inside a declared discovery window. Candidate families and local
parameter refinements must have an explicit trial cap; cross-sectional factors
are rank-normalized before combination. Portfolio candidates are selected only
from the validation window, Buy-and-Hold is a benchmark rather than an active
candidate, and only the locked active winner may enter the confirmatory window.
After confirmatory access, that window cannot be used for another tuning loop.

The current universe is survivorship-biased, the source is non-authoritative and
the 2026 window was already visible to earlier project research. Consequently,
even a positive result would remain `NOT_ELIGIBLE`; the observed negative result
is retained as evidence and cannot be tuned away. A subsequent hypothesis needs
PIT/licensed constituents and genuinely new forward PAPER/SHADOW observations.

## ADR-014 — Repeated tuning requires cross-window and perturbation hard gates

Status: accepted, 2026-08-25.

After a confirmatory period has been viewed, repeated tuning may continue only
as explicitly post-selection diagnostics. The candidate grid and all thresholds
must be frozen before execution. Candidates are ranked across multiple bounded
time windows, but only candidates meeting the minimum positive-window ratio,
median Sharpe and worst-window Sharpe may enter cost, neighbouring-parameter,
deterministic-universe and dependency-preserving bootstrap stresses. A claim
that no candidate passed is valid only after every window-eligible candidate has
received the complete gate.

The 2026 observations are structurally excluded from all ranking and gate
calculations and may be opened only as a labelled polluted audit after the
candidate identity is fixed. Passing this research robustness gate does not
authorize promotion, registration, PAPER or execution. Current-universe
survivorship bias, repeated use of 2024-2025, single-provider history and missing
forward PAPER/SHADOW evidence keep the result `NOT_ELIGIBLE`.

## ADR-015 — Stock transfer cannot repair a non-PIT research universe

Status: accepted, 2026-08-25.

A factor chosen on ETFs may be transferred to a larger stock universe only after
the stock identities, sector allocation, adjustment policy, evaluation windows
and robustness thresholds are frozen. No stock outcome may change the factor,
lookback, Top-K or rebalance interval. Data-pipeline corrections made before any
strategy result exists must be explicit and content-bound; they may not silently
drop a losing symbol or relax a return gate.

The current transfer uses 15 current SW sectors with five current CSI 300 members
each, uniform HFQ, seven annual windows, all sector-only and leave-one-sector-out
perturbations, doubled costs and a dependency-preserving bootstrap. Passing this
gate is diagnostic evidence of cross-sectional portability only. Projecting
2026 constituents backward, revised adjustment factors, last-close suspension
valuation, missing limit-up/down execution and a single public source can all
inflate historical results. Therefore the result stays `NOT_ELIGIBLE` until a
licensed point-in-time universe and genuinely new forward PAPER/SHADOW evidence
exist.

## ADR-016 — Minute adaptation is a bounded risk overlay, not online retuning

Status: accepted, 2026-08-25.

The daily strategy owns symbol selection and maximum target quantities through
an immutable per-session target published before the relevant minute bar. The
one-minute layer may only preserve or reduce that target in whole lots. It may
not add symbols, increase exposure, replace the strategy version or search
lookback/Top-K/rebalance parameters during the trading session.

Only finalized one-minute bars may update the overlay, and any resulting target
must execute on a later observed tick through readiness, Risk, OMS and
`PaperGateway`. Insufficient warmup data is zero-target, adverse states re-risk
only after cooldown, and all rolling state is checkpointed. Provider disconnect,
invalid status, unknown symbol, late/duplicate data or persistence failure stays
fail-closed. Until an authorized tick provider and forward SHADOW/PAPER evidence
exist, minute readiness remains `NOT_ELIGIBLE` and external order transmission
remains structurally false.

## ADR-017 — RQData is an optional read-only native bridge with Tick-led replay

Status: accepted, 2026-08-25.

RQData is the primary authoritative realtime-data option for the current macOS
host; XTData remains the QMT-entitled fallback. The SDK is an optional, pinned
dependency and is imported only inside the native bridge. Credentials come only
from the bridge process environment and are never included in capability,
diagnostic or failure payloads. A connection is not established until SDK login
and realtime WebSocket token exchange both succeed.

The bridge exposes snapshots, raw 1d/1m history, Tick and pushed 1-minute bars,
but no account or order method. Canonical Tick callbacks receive a session-global
ingestion ordinal, and the recorded PAPER layer preserves it through per-symbol
storage for deterministic multi-stock replay. Tick aggregation is the strategy
clock; vendor-pushed minute bars are comparison evidence because the vendor warns
that late ticks can cause live bars to differ from cleaned historical bars.
SDK-managed reconnects are not sufficiently observable to claim production-grade
continuity, so an invalid message/callback closes the feed and every new session
requires a fresh quality reconciliation and readiness attestation.

## ADR-018 — Public web deployment is stateless and read-only

Status: accepted, 2026-08-25.

The public Vercel project may serve the Chinese research application and a
checksum-verified subset of historical/research evidence. Its FastAPI context is
created from the zero-capacity bootstrap runtime and has no control or trade
token. Consequently every control/trade-sensitive route fails closed; the
deployment must not include SQLite PAPER state, provider credentials, broker
adapters, an active minute session or any external order-transmission authority.

Interactive backtests remain ephemeral read-authority computations over the
packaged signed Parquet dataset. The public deployment is a research and
observability surface, not the realtime PAPER process. Realtime minute tracking
continues to require an authorized provider and a separately operated persistent
runtime; it cannot be inferred from a healthy Vercel page.

The public bootstrap state and packaged evidence are identical for every
anonymous visitor, so successful GET responses may use a short shared CDN cache.
Requests with authorization/cookies, mutation methods and error responses must
not be cached. Interactive backtest POST responses remain uncached. This boundary
reduces serverless load without turning stale public research data into PAPER or
realtime authority.

## ADR-019 — Interactive factor comparison stays bounded and browser-local

Status: accepted, 2026-08-26.

The ad hoc preview may compare only the implemented causal price-factor families:
momentum, reversal and low volatility. Factor periods remain bounded to one to
three explicit horizons, and every family uses the same signed dataset,
close-decision/next-open simulator, trend/risk controls and modeled costs. Adding
a selector does not convert repeated parameter inspection into held-out evidence.

The public application may keep up to 30 curve-free preview summaries in the
current browser so a user can compare and reload parameters. A record is accepted
only when its schema, result hash, data version and checksum validate, and it is
shown only against that exact dataset identity. Browser records are not server
experiments, strategy lineage, promotion evidence, PAPER targets or synchronized
account state; corrupted or unavailable local storage fails closed.

## ADR-020 — Frontend completeness is evidence parity plus graceful degradation

Status: accepted, 2026-08-26.

A route existing is not sufficient frontend completion. Each page must load its
static assets, receive checksum/schema-verified data, expose the implemented
interaction, preserve other successful sections when one optional evidence
source fails, and show an explicit unavailable reason for external blockers.
The gap inventory is maintained in `docs/FRONTEND_GAP_AUDIT.md`.

The public read-only node may package the complete accepted research artifact
chain when every file is content-bound and independently revalidated on read.
This includes the baseline catalog/artifacts, walk-forward and robustness
reports; packaging them does not create new evidence or change promotion state.
Local serving accepts both ordinary and Vercel-mode frontend builds, but this
transport compatibility does not add credentials, persistence or control
authority to the public node.

## ADR-021 — Browser controls are host-bound and credentials stay ephemeral

Status: accepted, 2026-08-26.

The frontend determines control authority from the browser hostname: loopback
may render the existing PAPER/SHADOW control and PAPER-cancel routes, while any
non-loopback hostname is a read-only research node and must omit those controls.
This is only a UI boundary; every backend mutation still requires its scoped
bearer token and public Vercel functions retain no such token or state.

Control and trade tokens may exist only in React component memory. They are not
written to localStorage, query strings, telemetry, logs or exported JSON. Stop,
kill and cancel require confirmation, cancel keeps a stable per-order idempotency
key for the page lifetime, and no action creates a LIVE mode or external gateway.
Optional read-only evidence failures are reported per partition without hiding
successful sections, but system, health and the atomic PAPER snapshot remain the
required core needed to replace the last good snapshot.

## ADR-022 — Page failures stay inside the shell and charts load only used modules

Status: accepted, 2026-08-26.

A runtime exception in one lazy page must not remove navigation, runtime mode or
the visible no-live safety boundary. The route content is therefore wrapped in a
page error boundary that shows a generic reload action without rendering error
details. Navigating to another route remounts the boundary and provides a clean
recovery path; API partition failures continue to use their existing data-level
degradation instead of being converted into render exceptions.

The chart runtime registers only the line, bar, candlestick, grid, legend,
tooltip, data-zoom and canvas modules used by QTrade. React and zrender are stable
transport chunks so ordinary and Vercel builds remain below Vite's 500 KB warning
boundary. This changes loading and caching only; it does not change evidence
validation, strategy state, credentials, persistence or trading authority.

## ADR-023 — Prometheus export is finite, low-cardinality and read-only

Status: accepted, 2026-08-26.

The existing JSON `/metrics` contract remains the dashboard snapshot. A separate
`/metrics/prometheus` read endpoint exposes only predefined runtime counters and
gauges plus bounded mode/risk/reconciliation labels in Prometheus 0.0.4 text.
Non-numeric or non-finite values fail closed with 503. Strategy-heartbeat maps,
symbols, order identifiers, account identifiers and credentials are excluded to
avoid cardinality growth and information leakage.

The browser monitor reads the canonical JSON field names and may search or filter
the already bounded 500-row durable audit snapshot, then export only that current
view. These controls do not acknowledge alerts, mutate the journal, route messages
externally or change runtime/trading authority; outbound delivery requires a
separate authenticated integration decision.

## ADR-024 — Automatic PAPER reports use startup-bound identity and explicit sessions

Status: accepted, 2026-08-26.

Automatic daily reports may run only inside the persistent loopback PAPER
process. Data version, every strategy version, the SSE venue and a bounded set
of operator-verified open sessions are bound at process startup; an HTTP caller
cannot provide or override them. The scheduler uses an Asia/Shanghai wall-clock
cutoff, emits at most one immutable artifact per configured day and treats a
missing/ambiguous session, conflicting existing identity or failed hash check as
a closed failure rather than overwriting or fabricating a report.

The report snapshot remains serialized by the existing account-wide dispatch
gate, and trade attribution uses the Asia/Shanghai calendar day. Scheduler state
is a read-only API/UI surface and adds no order action. Stateless public Vercel
has neither the trusted bindings nor writable runtime storage, so it remains
explicitly `UNCONFIGURED`; enabling a real session still requires authorized
market data and the actual PAPER strategy identities.

## ADR-025 — Minute history preserves the provider grid and synchronizes on stocks

Status: accepted, 2026-08-26.

The recent 1-minute research source is Sina
`CN_MarketDataService.getKLineData`, accessed through a bounded retrying HTTP
client. A completed provider-native session has 238 timestamps: 09:31–11:30,
13:01–14:57 and the 15:00 close. QTrade records the source omission of 14:58 and
14:59 and does not interpolate or relabel those minutes. Every included
symbol/session must match that grid and pass the canonical OHLCV quality gate.

The 15 sector representatives define the latest five common completed sessions.
An ETF is included only when it has all five of those exact sessions; every
exclusion and missing day is retained in the signed manifest. This creates an
availability-filtered, current non-PIT research sample, not promotion evidence
or realtime readiness. No snapshot, PAPER or external-order authority follows
from successful historical HTTP access.

## ADR-026 — Minute decisions execute on the next open and use a locked two-day read

Status: accepted, 2026-08-26.

The minute overlay simulator observes signal inputs only at close `t` and applies
the resulting exposure at open `t+1`. Gap P&L belongs to the previously effective
exposure, costs are charged when the next-open exposure changes, and a signal
cannot avoid the same bar that created it. A constant unit daily target is known
before each session and the overlay remains bounded to `[0, 1]` exposure.

Seven policies are frozen before evaluation. The first three sessions select one
policy using excess return plus twice the drawdown improvement; the last two
sessions are then read once. Cost stress, instrument subgroups, daily partitions
and leave-one-sector-out results are diagnostics around that locked read, not new
independent samples. Five recent current-universe sessions, two test sessions and
no PAPER/SHADOW record make the report permanently `NOT_ELIGIBLE`, even when the
overlay reduces loss or drawdown relative to the always-exposed baseline.

## ADR-027 — Public minute charts require full dataset and artifact verification

Status: accepted, 2026-08-26.

The public node packages the 44,030-row minute Parquet, its signed manifest and
the six minute-backtest JSON artifacts. A minute series request verifies the
manifest signature, Parquet checksum/metadata and every symbol/day count against
the 238-timestamp source grid before returning one bounded series. The research
endpoint separately verifies the report envelope and every named artifact hash,
then checks the frozen split, candidate count, cost scenarios, sector exclusions
and causal close-to-next-open contract.

The frontend may render these verified historical artifacts as a selectable
分时 line and a minute-overlay comparison, but must keep them visually distinct
from the `UNCONFIGURED` realtime minute monitor. Missing or tampered evidence
removes the affected chart rather than using browser cache, interpolation or a
partial file. Public responses remain read-only and cannot change runtime state,
strategy role or external-order permissions.

## ADR-028 — Minute cross-section uses a frozen 15×5 stock design

Status: accepted, 2026-08-26.

The synchronized minute study now replaces the single representative per sector
with the already signed CSI 300 / SW level-1 universe: 15 sectors and the five
highest-current-weight members in each sector. The universe proves it was frozen
before candidate minute data access, but remains current and non-PIT. All 75
stocks must contain the same latest five complete 238-point sessions; the ingest
fails rather than silently substituting another stock or partially filling a
sector. Eligible ETFs must contain those same sessions.

Leave-one-sector-out now removes all five stocks in the selected sector and
records the exact excluded symbol list, excluded count and retained universe
count. The public verifier recomputes sector membership from the signed dataset
and rejects a report if those fields disagree. This supersedes the packaged
44,030-row count in ADR-027 with 115,430 rows and 97 synchronized symbols without
changing the historical-only, `NOT_ELIGIBLE`, next-open or no-order boundaries.

## ADR-029 — Public API keeps one Parquet engine and a bounded Vercel bundle

Status: accepted, 2026-08-26.

The Parquet store now uses its existing PyArrow dependency for both writes and
reads instead of loading DuckDB for one read path. Dataset checksums, PASSED-only
reads, timestamp ordering and symbol filtering are unchanged. Python 3.12 and
the resolved dependency graph are committed so a clean deployment cannot
silently restore the removed binary dependency.

The Vercel function excludes tests, research runtime state, web build sources
and unused PyArrow Flight/development files from the function bundle. It still
packages every signed public evidence artifact and the Parquet libraries needed
to recompute metadata and rows. A production build must pass locally before a
prebuilt deployment is uploaded; bundle reduction cannot replace evidence
verification or enable mutable/public trading controls.

## ADR-030 — Native 5-minute walk-forward is a falsification gate, not a substitute

Status: accepted, 2026-08-26.

The public Sina endpoint exposes at most 1,970 observations per request. That
limit covers only eight complete provider-native 1-minute sessions, so QTrade
must not fabricate a 20- or 40-day 1-minute history by interpolation, relabeling
5-minute bars or silently joining incompatible sources. A separate 40-session
study may use the provider's native 5-minute bars only when the interval and its
48-timestamp session grid are embedded in the signed manifest and every API/UI
label says 5-minute historical data.

The 5-minute study uses four expanding folds: 16 initial training sessions,
then four frozen selections followed by mutually disjoint six-session test
windows. All seven candidate states are advanced causally before selection;
signals observed at the current 5-minute close affect exposure only at the next
5-minute open. Cost stress, ETF/stock splits and 15 whole-sector exclusions are
diagnostics, while the 24-session stitched test is the decision evidence.

The observed failure—negative relative return even at zero modeled cost and only
one positive test fold—must be published as a robustness veto. It may not be
hidden by the favorable five-day 1-minute relative result or tuned on the four
already viewed test windows. Because interval transfer failed, the strategy
remains `NOT_ELIGIBLE`; authorized realtime 1-minute data and new forward
PAPER/SHADOW sessions are still required before any later promotion review.

## ADR-031 — Intraday breadth is a signed summary, not a second realtime feed

Status: accepted, 2026-08-26.

The point-in-time intraday source audit may cover all five frozen stocks in each
of the 15 sectors, but it must not multiply the public dashboard payload by
embedding full-session OHLCV for all 75 stocks. The breadth artifact instead
retains each stock's finalized same-day timestamp set, source request window and
latest-bar age. The API binds the exact frozen 15×5 identities, recomputes the
cross-stock and per-sector intersections, and removes raw timestamp sets from the
response after verification. Detailed charts remain a separate 15-representative
artifact.

A successful 75-stock fetch demonstrates only public historical-source breadth
at that signed instant. Unequal latest timestamps and source latency remain
visible, partial coverage fails the all-stock claim, and the result cannot set
realtime entitlement, mutate strategy/PAPER state, promote a candidate or enable
external order transmission.

## ADR-032 — Intraday source continuity is an explicit bounded hash chain

Status: accepted, 2026-08-26.

The public deployment may retain up to 32 same-day checkpoints that each bind
one verified 15-representative detail report and one verified 75-stock breadth
report. A checkpoint records only their identities and independently validated
coverage, common-minute, request-window and lag summaries. It includes the prior
checkpoint hash, while the outer report binds the ordered chain.

The API recomputes checkpoint digests, sequence, China trading day, monotonicity
and the exact binding between the final checkpoint and the currently deployed
source reports. If common-minute counts or latest timestamps regress, that
checkpoint remains present with a failed monotonic flag; it is not rewritten or
dropped. This detects deployment/source continuity only and creates no realtime
entitlement, strategy input, PAPER readiness, promotion evidence or order path.

## ADR-033 — Future-session evaluation is immutable and complete-day only

Status: accepted, 2026-08-26.

The retrospective development leader may be observed on future BaoStock data
only under a signed contract that binds the policy, 368-session warmup dataset,
collector, evaluator and causal engine. The policy itself was locked before the
first eligible open. Because the detailed evaluation contract was fixed after
that open, the evidence must disclose this timing and cannot be called a fully
blind preregistration.

Each future observation is an immutable provider-native 5-minute snapshot with
exactly 75 frozen stocks and 48 bars per stock. The latest frozen warmup day is
re-fetched in the same QFQ request and must match every OHLCV field exactly; a
revision, suspension placeholder, missing bar or partial day rejects the target
session. Warmup sessions advance state but never enter future metrics.

The first 20 complete future sessions are evaluated at the predeclared 0/5 bps
costs, exposure/turnover limits and whole-sector exclusions without parameter
changes. Before 20 sessions the only valid conclusion is `INCOMPLETE`. A later
pass remains research-only because the current universe is non-PIT, QFQ can
contain future adjustment knowledge, and no authorized PAPER/SHADOW trace has
been recorded. It cannot enable LIVE or external order transmission.

## ADR-034 — The public dashboard shares one verified minute-dataset identity

Status: accepted, 2026-08-26.

The public dashboard previously requested eight research and intraday sections
in parallel. Every endpoint independently read and hashed the same 24 MB long
5-minute Parquet, creating avoidable serverless cold-start I/O. The read-only
`/minute-research-snapshot` endpoint must open and checksum that dataset once,
then pass the resulting immutable identity to the existing independent report
validators.

Aggregation cannot relax a verifier or substitute a failed section: invalid
sections are returned as null and listed explicitly, while every standalone
endpoint remains available for focused inspection. The frontend retains typed
fail-closed defaults if the whole bundle request fails. Tests instrument the
Parquet boundary and require one open for all eight sections. This is a read-path
optimization only; it does not cache mutable runtime state, authorize realtime
data, change strategy status or expose a write/order route.

The browser may have many independent lightweight sections, but it must queue
GETs above a six-request ceiling. This keeps partial-section fault isolation
without triggering a serverless cold-start burst. POST/control calls are not
queued by this read limiter and remain protected by their existing local-only
authentication boundary.

## ADR-035 — Forward evidence advances by verified atomic publication

Status: accepted, 2026-08-26.

The immutable collector and locked evaluator always write new paths and refuse
overwrites. Their candidate report may replace the public forward-observation
baseline only through a separate publisher that runs the same long-dataset,
development-lock, preregistration and semantic report validators as the API.

The accepted complete-session count must strictly increase, the already
published session list must be an exact prefix, the preregistration and policy
hashes cannot change, and the signed generation time must advance. Publication
uses an atomic same-directory replacement and immediately verifies the published
digest. A failed candidate leaves the previous public evidence unchanged; this
workflow cannot change strategy parameters, grant realtime/PAPER authority or
enable LIVE/external orders.

## ADR-036 — Mutable intraday evidence is published atomically

Status: accepted, 2026-08-26.

The detail, 75-stock breadth and continuity reports are intentionally refreshed
at the same deployment paths during a trading day. Each writer must serialize to
a same-directory temporary file, flush and `fsync` the complete payload, and only
then atomically replace the previous signed JSON. A process interruption before
replacement therefore leaves the last complete checkpoint readable.

Atomic publication changes only crash consistency. The API still independently
recomputes every evidence and checkpoint hash, and any source regression remains
recorded as non-monotonic rather than being hidden. No such report can grant
realtime/PAPER authority, strategy promotion or external order transmission.

## ADR-037 — Verified charts distinguish loading from missing evidence

Status: accepted, 2026-08-26.

Historical chart series are fetched after the platform snapshot establishes the
expected data version and checksum. Until that second verified request resolves,
the chart must show an explicit loading state rather than the empty-evidence
message. A response is rendered only when its symbol, version and checksum match
the current signed dataset identity; request failure remains a visible error and
does not fall back to stale, interpolated or synthetic bars.

## ADR-038 — Local experiment inventory has an explicit revision trigger

Status: accepted, 2026-08-26.

Browser-local backtest summaries are read from storage during render, but list
refresh must not depend on an unrelated status string changing. Every successful
save or delete increments a dedicated in-memory revision so repeated identical
actions still update the comparison table immediately. The revision adds no
server persistence, formal experiment registration or strategy promotion path.

## ADR-039 — Intraday directional breadth is observable, not a strategy input

Status: accepted, 2026-08-26.

The signed 75-stock breadth report may retain each stock's first same-day open,
previous finalized close and latest finalized close. The API must rederive every
relative-open and latest-minute return, then recompute global and 15-sector
direction counts and equal-weight means. A mismatched value fails the entire
report closed even when its outer digest is regenerated.

These fields describe a bounded point-in-time cross-section from a free
historical endpoint. They cannot become a factor candidate, influence parameter
selection, mutate minute-overlay/PAPER state, establish realtime entitlement or
authorize external order transmission without a separately preregistered study.

## ADR-040 — Directional continuity starts without rewriting history

Status: accepted, 2026-08-26.

Continuity schema v2 names the first checkpoint whose 75-stock breadth summary
includes relative-open/latest-minute counts and equal-weight returns. Every
checkpoint at or after that sequence must carry a structurally valid direction
summary; removing one fails the full chain closed. Checkpoints before the start
remain byte-for-byte and hash-for-hash unchanged because their overwritten source
reports cannot be independently reconstructed.

The latest checkpoint must still equal the separately verified current detail
and breadth reports. This chain supports observable intraday comparison only; it
does not create a strategy feature, parameter update, PAPER decision, realtime
entitlement or external order path.

## ADR-041 — Forward reports inherit the exact preregistered warmup identity

Status: accepted, 2026-08-26.

A signed forward-observation report is valid only when its warmup data version,
checksum, session count and warmup-metric exclusion flag exactly equal the
already verified preregistration. Merely supplying a syntactically valid SHA-256
is insufficient because it would detach the result from the causal state used
by the locked policy. Its signed generation time must also strictly follow the
lock creation time.

All future-session count fields must also be genuine integers rather than
booleans, despite Python's `bool` subclassing `int`. A target session must not
postdate the report under the China market clock, and a same-day report must be
generated no earlier than the locked 15:10 collection boundary. A mismatch fails
the report closed and leaves the independently verified preregistration state
visible; it cannot advance the public count or grant strategy, PAPER, realtime
or order authority.

## ADR-042 — Public research refreshes bypass the browser HTTP cache

Status: accepted, 2026-08-26.

Every read-only API GET issued by the web client uses the Fetch `no-store` cache
mode. Signed evidence identities remain the server's source of truth; a manual or
periodic refresh must not preserve a previously cached snapshot after a verified
deployment advances its intraday or forward chain.

This changes only freshness of read requests. The six-request concurrency cap,
optional-section fault isolation, public mutation failures and all no-realtime,
no-PAPER and no-external-order boundaries remain unchanged.

## ADR-043 — Forward cost metrics cannot use negative turnover

Status: accepted, 2026-08-26.

Every forward cost-stress row must contain finite exposure, total turnover and
mean turnover per symbol. Exposure remains bounded to `[0, 1]`; both turnover
values must be non-negative and the mean must equal total turnover divided by
the frozen 75-symbol count.

This prevents a resigned report from passing the maximum-turnover gate with a
negative economic quantity. A one-complete-session report remains explicitly
`INCOMPLETE` and `NOT_ELIGIBLE` even when all provisional gates are true.

## ADR-044 — Forward sector exclusions inherit the frozen universe identity

Status: accepted, 2026-08-26.

The normalized forward lock derives the exact 15 sector code/name pairs from the
already verified long-history dataset. Once at least one future session is
evaluated, every leave-one-sector-out row must match that complete identity set;
unique but invented sector labels are not sufficient.

This binds sector-stability arithmetic to the intended multi-sector universe.
It does not turn the current-universe/QFQ evidence into PIT data or grant PAPER,
realtime or external-order authority.

## ADR-045 — Forward promotion reopens the immutable source sessions

Status: accepted, 2026-08-26.

Before replacing the public forward-observation report, the publisher must load
every formal forward-session manifest and its checksum-bound Parquet from the
configured source root. The candidate accepted count and ordered target-day,
evidence-hash, 3,600-Bar and exact-anchor fields must equal those verified source
reports.

An outer report digest or syntactically valid per-session hash is insufficient.
A mismatch leaves the previous public report untouched and cannot advance
strategy, PAPER, realtime or external-order authority.

## ADR-046 — Forward cost rows share one benchmark identity

Status: accepted, 2026-08-26.

The 0-bps and 5-bps forward cost-stress rows must contain the same Buy-and-Hold
total return, maximum drawdown, realized minute volatility and point count. Cost
stress changes the overlay's simulated transaction deduction; it cannot redefine
the common comparator.

A resigned report with internally consistent but different baselines fails
closed and cannot affect gates, public progress or runtime authority.

## ADR-047 — Forward execution diagnostics are all-or-nothing

Status: accepted, 2026-08-26.

Each forward cost row must carry the complete execution diagnostic contract:
symbol/observation/reduced counts, exposure sum and mean, reduced ratio, turnover
and per-symbol mean, transaction cost and per-symbol mean, and reason counts.
The shared minute-execution validator rechecks every bound and arithmetic ratio.

Removing an inconvenient cost or reduction field and regenerating the outer
digest fails the report closed. Partial diagnostics cannot influence gates or
be rendered as verified evidence.

## ADR-048 — Execution counts reject Python booleans

Status: accepted, 2026-08-26.

Reduced-exposure counts and per-reason counts in the shared minute execution
diagnostic must be genuine integers. Python's `bool` subclassing `int` does not
make `true` or `false` a valid economic observation count.

This applies to forward and long-history execution evidence. A boolean count
fails closed before ratios, gates or UI summaries are accepted.

## ADR-049 — Forward cost and metric counters use exact integer identities

Status: accepted, 2026-08-26.

Forward cost-stress identities must use genuine non-boolean integer cost levels
in the predeclared order `0, 5`. Metric point counts must likewise be genuine
non-boolean integers and exactly equal the evaluated-session window; lossy
numeric coercion is not evidence validation.

This closes Python's `False == 0` and `int(48.9) == 48` compatibility paths.
A resigned report using either ambiguous representation fails closed before it
can advance public progress, gates, PAPER, realtime, or order authority.

## ADR-050 — The forward lock binds exact implementation digests at runtime

Status: accepted, 2026-08-26.

The public verifier must compare all three implementation digests in the
forward preregistration to the exact values frozen before the first eligible
session. Valid key names and SHA-256 syntax alone do not establish code
identity. The bound files are the provider-session probe, the locked collector
and evaluator, and the causal minute backtest engine.

Vercel intentionally excludes development scripts from its function bundle, so
the verifier carries the already frozen digest values rather than reopening
unavailable paths. Any implementation change requires a new preregistration;
resigning the old document with replacement digests fails closed and cannot
grant strategy, PAPER, realtime, or external-order authority.

## ADR-051 — Forward publication revalidates the exact candidate bytes

Status: accepted, 2026-08-26.

The publisher reads the candidate payload once immediately before creating the
temporary destination file. Its embedded evidence digest must still equal the
identity returned by full candidate verification, and the digest is recomputed
over those exact bytes' decoded unsigned document before any replacement.

This closes the local time-of-check/time-of-use gap between verification and
atomic publication. A swapped or modified candidate fails while the previous
public report remains byte-for-byte unchanged; post-replacement verification
continues as an additional invariant rather than the first point of detection.

## ADR-052 — Forward source sessions bind the recomputed frozen anchor

Status: accepted, 2026-08-26.

The publication boundary does not trust an `anchor_exact_match` boolean by
itself. The frozen 2026-08-25 warmup tail was independently reopened in the
predeclared 75-symbol order, with exactly 48 native five-minute bars per symbol,
and produced anchor digest
`c5f74be0063c2aa0d518ebd77055909970e5216c6a5a2240ea2188f42b204f52`.

Every source session considered for promotion must carry both that exact anchor
day and digest in addition to the checksum-verified target Parquet identity. A
resigned manifest with a fabricated boolean or different anchor cannot advance
the public count or affect strategy, PAPER, realtime, or order authority.

## ADR-053 — Forward source semantics cannot drift from the lock

Status: accepted, 2026-08-26.

Each source session admitted by the publisher must retain the locked
preregistration identity and the exact `BaoStock` / native five-minute /
`adjustflag=2` collection semantics. It must also keep the current-universe QFQ
limitations explicit: non-PIT, future-adjustment knowledge possible,
`NOT_ELIGIBLE`, realtime and real-data PAPER not ready, and external order
transmission disabled.

The top-level candidate's safe interpretation is not enough if a constituent
source says otherwise. Any resigned source-level semantic drift rejects the
entire publication before the prior public report can be replaced.

## ADR-054 — Forward session versions and collection times are exact

Status: accepted, 2026-08-26.

For each source session, the manifest data version must be exactly
`baostock-forward-<target-day>-qfq-v1`, with the matching BaoStock versioned
source string, canonical bar schema, `PASSED` quality status and a SHA-256
Parquet checksum. A plausible but differently dated label is not equivalent.

All timestamps must be timezone-aware and ordered as manifest ingestion no
later than collection, and collection no later than candidate generation. A
same-day collection is valid only from 15:10 Asia/Shanghai onward. This binds
the source artifact to the predeclared completed-session window before any
public replacement or runtime interpretation occurs.

## ADR-055 — Forward source sessions use the frozen provider version

Status: accepted, 2026-08-26.

The first formal forward-session publication accepts only BaoStock `0.9.3`, the
same provider environment used to freeze and verify the long-history and anchor
evidence. Requiring only a source string derived from the report's own provider
version would let both fields drift together after resigning.

Every constituent source report must therefore retain the exact provider
version as well as the matching native five-minute `adjustflag=2` source string.
A version drift fails before atomic replacement and cannot change public
progress, strategy/PAPER authority, realtime readiness or external-order state.

## ADR-056 — Forward atomic publication synchronizes the parent directory

Status: accepted, 2026-08-26.

Flushing the same-directory temporary report before atomic replacement prevents
readers from seeing a partially written candidate, but it does not by itself
confirm persistence of the replacement directory entry across a sudden crash.
The publisher must therefore `fsync` the destination parent after the replace.

Tests require file synchronization to precede directory synchronization. This
durability step does not relax candidate verification, overwrite a failed
publication, or grant strategy, PAPER, realtime or external-order authority.

## ADR-057 — Forward publication rejects future candidate timestamps

Status: accepted, 2026-08-26.

Ordering ingestion, collection and candidate generation is insufficient when a
resigned candidate can place the entire sequence in the future. Publishing such
a report would make later legitimate candidates fail the strict generation-time
progression rule.

The publisher therefore compares candidate generation with its timezone-aware
validation instant and requires `ingestion <= collection <= candidate <= check`.
A future-dated candidate fails before replacement and cannot advance public
progress or affect strategy, PAPER, realtime or external-order authority.

## ADR-058 — Forward target days use one canonical spelling

Status: accepted, 2026-08-26.

Python accepts compact ISO dates such as `20260901`, but the frozen collector
emits `YYYY-MM-DD`. Allowing a source report and candidate to drift together to a
parser-compatible spelling would admit a resigned identity not produced by that
collector.

The publisher therefore requires the raw source `target_day` to equal the parsed
date's canonical ISO representation exactly. A compact or alternative spelling
fails before replacement and cannot alter public progress or runtime authority.

## ADR-059 — Forward sources are reopened at the replacement boundary

Status: accepted, 2026-08-26.

Validating immutable source sessions only once leaves a local time-of-check to
time-of-use interval before the public destination is replaced. Source manifests
or their checksum-bound Parquet could change during that interval even though the
earlier in-memory reports still match the candidate.

After the candidate temporary file has been flushed, the publisher must reopen
and fully validate every source session immediately before replacement. Any
failure removes the temporary file and leaves the prior report unchanged; this
cannot grant strategy, PAPER, realtime or external-order authority.

## ADR-060 — Forward Parquet inherits the frozen 75-stock identity

Status: accepted, 2026-08-26.

A checksum proves that a Parquet file is unchanged after signing, but a resigned
file with 3,600 bars from 75 substitute symbols could previously satisfy only
the count and target-day checks. That does not prove the frozen 15-sector sample
was observed.

The publisher derives the exact 75 symbols from the long-history manifest bound
by the preregistration. Every source day must provide all 48 native timestamps
for each symbol, the canonical exchange and China trading-day identity, and pass
the shared OHLCV quality gate. Substitution or partial coverage fails before
replacement and cannot affect runtime authority.

## ADR-061 — Parquet rows retain the manifest data version

Status: accepted, 2026-08-26.

A file checksum binds bytes but does not prove that the `data_version` stored in
each Parquet row equals the manifest used to open it. A modified file can receive
a new checksum while carrying rows labelled as a different dataset.

`ParquetBarStore` must reject a checksum-valid file when any row omits or changes
that version before constructing domain Bars. This shared fail-closed lineage
check applies to research, charts, backtests and forward-session sources; it does
not change runtime mode or grant external-order authority.

## ADR-062 — Parquet physical order is validated before sorting

Status: accepted, 2026-08-26.

Sorting raw rows before validation can silently repair per-instrument timestamp
disorder in a modified and resigned file. The resulting Bars look canonical even
though the stored replay sequence is no longer the one that originally passed
the quality gate.

Reads must construct Bars in physical order and rerun the shared write-time
quality gate before any presentation sorting. Duplicate/disordered timestamps,
invalid OHLCV, non-finite values and abnormal jumps therefore fail closed rather
than entering research, replay or forward evidence.

## ADR-063 — Parquet reads enforce the exact Arrow schema

Status: accepted, 2026-08-26.

A checksum proves file identity, not compatibility with the canonical Bar
contract. A modified and resigned Parquet file could previously omit a required
column, add an untracked field or change a field type; callers then received a
low-level conversion exception or silently ignored the drift.

Reads compare the complete ordered ten-column Arrow schema before converting any
row. Missing, extra, reordered and type-drifted columns now fail uniformly as a
dataset-integrity error, so every chart, research job and replay sees the same
versioned storage contract.

## ADR-064 — China Bar timestamps are bound to the declared trading day

Status: accepted, 2026-08-26.

Session grouping, walk-forward splits and minute-overlay evaluation use
`trading_day`, while causal ordering uses `timestamp`. If those fields describe
different China calendar dates, a checksum-valid Bar can enter the wrong split
or session without violating timestamp order.

The shared quality gate now resolves timezone-aware SSE/SZSE timestamps at
UTC+08:00 and requires the resulting date to equal `trading_day`. The rule is
limited to exchanges whose timezone is known; it does not impose China time on
future markets with a different calendar.

## ADR-065 — Parquet data versions cannot escape the storage root

Status: accepted, 2026-08-26.

`data_version` participates in a filesystem path. Treating it as an unchecked
path segment allows traversal, an absolute path, nested directories or a symlink
to select a Parquet file outside the configured store even when later checksum
or row-lineage validation rejects the content.

Both reads and writes now resolve the candidate directory before any dataset I/O
and require it to be a direct child of the resolved storage root. Path escape is
reported as a dataset-integrity error and no outside file is created or read.

## ADR-066 — Corrupt Parquet failures use the dataset-integrity boundary

Status: accepted, 2026-08-26.

A manifest checksum can intentionally bind bytes that are truncated, corrupted
or not Parquet at all. Allowing Arrow or OS exceptions to escape makes API and
research behavior depend on the storage backend and bypasses the error contract
used for checksum, schema and quality failures.

File-read and Arrow-decode failures are now wrapped as `DatasetIntegrityError`
while retaining the original exception as the cause for local diagnostics. No
malformed payload reaches row conversion or the backtest engine.

## ADR-067 — Parquet datasets remain non-empty after read-time verification

Status: accepted, 2026-08-26.

The writer rejects an empty Bar list, but a valid ten-column Parquet file can be
rewritten to zero rows and resigned. Duplicate, lineage and quality checks are
vacuously satisfied on that payload, allowing consumers to confuse absence of
data with a successful query.

Reads now reject zero rows after checksum and schema verification but before row
lineage or quality validation. Symbol filtering may still legitimately return no
matches from a non-empty verified dataset.

## ADR-068 — Bar identity is validated before instrument grouping

Status: accepted, 2026-08-26.

Quality, factor and replay state are grouped by `(symbol, exchange)`. Empty or
whitespace-padded values create artificial instruments, while a non-canonical
China code can bypass the stricter CSV importer when it arrives through another
adapter or a resigned Parquet file.

The shared gate requires non-empty, whitespace-clean symbol and exchange values.
For SSE and SZSE it additionally requires an exact six-digit symbol; other future
venues remain free to define their own format instead of inheriting this rule.

## ADR-069 — Parquet row decoding has one integrity error contract

Status: accepted, 2026-08-26.

Arrow field types do not guarantee non-null values or syntactically valid ISO
timestamps, dates and decimal strings. A modified and resigned payload could
therefore pass checksum and schema validation before exposing Python or Decimal
conversion exceptions to a chart, API handler or backtest.

Bar construction now wraps nullable, missing and malformed row content in
`DatasetIntegrityError` while retaining its original cause. Values that decode
successfully still pass through the independent semantic quality gate.

## ADR-070 — Parquet datasets publish only after a complete staged write

Status: accepted, 2026-08-26.

Creating the immutable data-version directory before `write_table` succeeds
leaves a visible but incomplete dataset after an I/O or encoder failure. The
empty directory also prevents a corrected retry with the same version identity.

Writes now build the Parquet file and compute its checksum inside a temporary
directory under the same storage root, then rename that completed directory to
the final version path. Failures remove staging state, while an already published
version remains immutable and cannot be overwritten.

## ADR-071 — Atomic Parquet publication is synchronized before success

Status: accepted, 2026-08-26.

An atomic directory rename prevents readers from seeing a partial payload, but
does not by itself request that file bytes and directory entries survive a host
crash. Returning a PASSED manifest before those writes are synchronized can
therefore overstate the durability of a newly collected minute dataset.

After checksumming, the writer fsyncs the staged Parquet file and its directory,
renames the completed directory, and fsyncs the storage root. A synchronization
failure prevents a PASSED manifest from being returned; no runtime or promotion
authority is inferred from filesystem publication alone.

## ADR-072 — Public deployments expose a bounded build identity

Status: accepted, 2026-08-26.

A successful provider status and a reachable custom domain do not prove which
Git commit currently serves that domain. Without a runtime build identity, a
public review can accidentally validate an older production alias.

The read-only `/deployment` endpoint reads only Vercel's documented commit SHA,
ref and target-environment variables, validates their formats, and never returns
arbitrary environment content. The frontend displays the shortened SHA while
the API retains all 40 characters for exact comparison; absent or malformed
metadata is `UNAVAILABLE` and conveys no runtime or trading authority.

## ADR-073 — Source coverage is diagnostic, not forward evidence

Status: accepted, 2026-08-26.

A formal forward count of zero cannot distinguish a delayed provider publication
from an anchor revision or a collection failure. Repeatedly fetching and storing
partial sessions would also let operational probing contaminate the locked
evaluation dataset.

The BaoStock coverage probe therefore reads all 75 locked symbols only after the
China-market close and publishes a signed summary without persisting fetched
Bars. The API revalidates the frozen dataset and preregistration identities,
China-time boundary, per-symbol grid/reason arithmetic and aggregate counts.
`READY_FOR_FORMAL_COLLECTION` means source availability only: the report always
stays outside the 0/20 metrics and never grants PAPER, LIVE or order authority.

## ADR-074 — Self-hashed public evidence has an exact semantic envelope

Status: accepted, 2026-08-26.

A SHA-256 field detects accidental drift but is not an authorization signature:
any modified JSON can be hashed again. Returning an otherwise valid report with
unknown fields could expose unintended content, while a far-future timestamp
could make stale evidence appear newer than every legitimate refresh.

The forward-coverage and formal forward-observation verifiers now require their
exact v1 top-level field sets and reject generation times more than five minutes
ahead of the server clock. These checks precede returning either report, so a
resigned extension or future-dated payload fails closed as unavailable (or to
the independently verified preregistration) rather than being partially exposed.

## ADR-075 — Mutable JSON snapshots synchronize their directory entry

Status: accepted, 2026-08-26.

Flushing a temporary JSON file and atomically renaming it prevents readers from
seeing partial content, but the replacement directory entry may still be lost
after a host crash if the parent directory is not synchronized.

The shared JSON publisher now fsyncs the completed temporary file, performs the
same-directory replace, then fsyncs the parent directory before returning. The
coverage, intraday detail, 75-stock breadth and continuity publishers inherit
this durability order without changing their evidence or trading authority.

## ADR-076 — Source coverage releases form a bounded evidence chain

Status: accepted, 2026-08-26.

A latest-only coverage report answers which symbols are currently available but
erases whether provider completeness improved or regressed across repeated
post-close probes. Treating the snapshots as formal sessions would instead
contaminate the locked 0/20 evaluation with diagnostics that persist no Bars.

Each independently signed coverage report is now retained as a complete,
content-addressed immutable release and summarized into a second signed,
previous-hash-bound chain of at most 64 checkpoints. Immutable release
publication writes and synchronizes a private temporary file before atomically
linking its final non-overwritable name. The public verifier rejects missing or
symlinked releases, reruns the full 75-stock report contract, recomputes every
checkpoint hash, provider/preregistration identity, timestamp, count arithmetic,
delta and monotonic flag, and requires the final checkpoint to match the current
coverage report exactly. Therefore resigning the whole summary cannot fabricate
an old point without also supplying the original full report. The chain is
displayed as source
publication history only; it cannot increment formal forward metrics or grant
realtime, PAPER, LIVE or external-order authority.

## ADR-077 — CI actions use least privilege and immutable release identities

Status: accepted, 2026-08-26.

The hosted CI matrix warned that its checkout and Python setup actions still
declared the retired Node 20 runtime and were being force-run on Node 24. Major
version tags also remain movable references, which weakens reproducibility even
when the action publisher is trusted.

The workflow now grants only `contents: read` and pins the official checkout
v7.0.1 and setup-python v7.0.0 releases to their full Git commit identities.
Python 3.11 and 3.13 continue to run Ruff, strict mypy and the complete test
suite; this CI maintenance does not change any runtime or trading authority.

## ADR-078 — Public cold-start recovery retries once, then returns to cadence

Status: accepted, 2026-08-26.

A newly deployed stateless function can return a partial optional snapshot on
its first fan-out while dependencies and edge paths warm. Keeping that fallback
for the full 60-second browser cadence makes a transient failure look persistent;
unbounded retries would instead amplify load during a real outage.

After the initial public snapshot only, a whole-request failure or any optional
partition failure schedules one retry five seconds later. A complete snapshot
does not retry, and cadences of five seconds or less already provide recovery so
they do not add another timer. Partial data stays visible between attempts and
the retry does not change any server state or trading authority.

## ADR-079 — Browser GET slots have a bounded lifetime

Status: accepted, 2026-08-26.

The browser limits public GET fan-out to six concurrent requests, but a network
path that neither resolves nor rejects can retain a slot forever. Enough stalled
requests would block the complete snapshot, its recovery retry and every queued
chart request even though the backend might already be healthy.

Every read-only GET now carries an AbortController deadline of 30 seconds. The
deadline covers both the response and JSON body, reports a stable Chinese timeout
error, and always clears its timer and releases the concurrency slot. A fake-time
test stalls all six active requests, queues a seventh and proves that timeout
failure cannot strand the queue. This is client-side availability control only;
it sends no mutation and changes no trading authority.

## ADR-080 — A successful bundle preserves internal partition failures

Status: accepted, 2026-08-26.

The minute research bundle can validly return HTTP 200 while one independently
verified report is unavailable. Treating only transport failure as an error made
the browser replace null values with fail-closed placeholders but still report
zero failed partitions, suppressing both the operator warning and cold-start
recovery retry.

The client now binds all ten bundled values to the server's
`unavailable_sections` contract. A null value or an explicitly unavailable name
selects the existing fail-closed placeholder and records a stable partition
failure; healthy siblings remain visible. The same failure map drives the one
bounded initial retry. No missing evidence is promoted and no server mutation or
trading authority is introduced.

## ADR-081 — Minute runtime checkpoints reject lossy JSON coercion

Status: accepted, 2026-08-26.

Python converts JSON `true` to integer `1` and any non-empty string to boolean
`True`. Applying those conversions before domain validation could therefore
restore a damaged daily target quantity or minute readiness flag as a different,
apparently valid PAPER/SHADOW state.

Daily-target quantities, overlay cooldown counters, snapshot quantities and the
snapshot readiness flag now require their exact JSON primitive types before
object construction. Existing range and reduction-only validation then runs on
the uncoerced values. Malformed checkpoints fail during restart; they cannot
silently modify a target or authorize any execution path.

## ADR-082 — Realtime PAPER pending targets restore without coercion

Status: accepted, 2026-08-26.

The causal realtime runner persists a finalized-bar target until a strictly
later recorded Tick can execute it. Coercing that durable quantity with `int()`
would turn JSON `true` into target quantity one; accepting booleans as ingest
ordinals would similarly weaken replay ordering after restart.

Pending quantities and ingest ordinals now require non-boolean, non-negative
integers. Pending decision/signal timestamps and the last finalized bar require
timezone-aware values with decision time no earlier than its signal bar, and an
inflight order identity must remain a non-empty string. Invalid recovery state
fails before the runner can consume another Tick.

## ADR-083 — Durable PAPER accounts restore exact finite economics

Status: accepted, 2026-08-26.

The SQLite account snapshot is an economic source of truth after process
restart. Silently skipping malformed positions or accepting `NaN` cash and a
boolean position quantity could produce a coherent-looking but false local
account before reconciliation.

Recovery now requires a positions mapping with named object rows, exact
non-boolean non-negative integer quantities, finite non-negative cash and
average cost, and finite realized PnL. Any malformed row rejects construction of
the PAPER/SHADOW bundle. A successfully restored account remains HALTED and still
requires the existing reconciliation flow before runtime start.

## ADR-084 — Durable orders reject malformed economic fields

Status: accepted, 2026-08-26.

Restored PAPER orders and their requests previously coerced quantities with
`int()` and silently skipped non-object rows. A boolean could therefore become a
one-unit order, while a damaged row could disappear from the local OMS view
before reconciliation.

The durable order collection and every request row are now mandatory objects.
Request quantities are exact positive non-boolean integers, prices are finite
and positive, filled quantities are exact non-negative integers bounded by the
request, and creation/update timestamps are timezone-aware and ordered. Any
violation rejects runtime construction; valid restored orders remain HALTED and
subject to the existing uncertain-order reconciliation gate.

## ADR-085 — Durable reservations and sellability lots reject coercion

Status: accepted, 2026-08-26.

Reserved cash/quantity and T+1 acquisition lots constrain the next risk
decision after restart. Treating a malformed collection as empty could release
exposure silently, while `int(true)` could fabricate a one-unit reservation or
sellability lot and a non-finite cash value could poison aggregate risk math.

Both collections and every row are now mandatory. Reservation and lot
quantities require exact positive non-boolean integers, reservation cash must be
finite and non-negative, sell reservations carry zero cash, identifiers are
non-empty strings, and lot dates use exact ISO date strings with the existing
chronology check. Any invalid durable row rejects construction before PAPER or
SHADOW recovery.

## ADR-086 — Forward cost baselines tolerate machine rounding only

Status: accepted, 2026-08-26.

The frozen forward evaluator runs the 0 and 5 bps paths independently. Their
Buy-and-Hold return is identical, but normalizing a cost-scaled equity curve can
produce differences near machine precision in drawdown and realized volatility.
Byte-level float equality therefore rejected the first otherwise verified
future session.

The public and promotion verifier now requires identical point counts and uses
an explicit `1e-12` relative / `1e-15` absolute tolerance for the three baseline
metrics. This accepts only numerical roundoff; the existing resigned material
baseline-drift test remains rejected. Collector, evaluator, policy and frozen
minute-backtest hashes are unchanged.

## ADR-087 — Forward promotion binds the collector's timezone identity

Status: accepted, 2026-08-26.

The frozen 2026-08-25 anchor has one sequence of 3,600 economic Bars, but its
canonical JSON differs when the same aware timestamps are rendered from stored
UTC or provider-native Asia/Shanghai objects. The prior promotion constant used
the UTC representation (`c5f74b...04f52`), while the code-bound collector first
checks instant-equivalent Bars and then signs the provider representation
(`e057f6...e6a29`). This made the first genuine source session unpublishable.

Promotion now accepts exactly the collector's Shanghai representation hash and
the frozen anchor date. The UTC hash remains documented as the equivalent
storage identity; no hash set or general timezone coercion was introduced. The
collector, evaluator, policy and minute-backtest freeze remain unchanged.

## ADR-088 — Individual-stock minute previews are bounded and side-effect free

Status: accepted, 2026-08-26.

The signed 75-stock BaoStock dataset could be inspected one session at a time and
used by frozen portfolio studies, but the public control plane had no way to
recompute a selected stock and interval. Reusing the daily cross-sectional
preview would have hidden the different 5-minute timing and reduction-only
contract.

The dedicated preview accepts only a stock and trading days present in the
verified long-history manifest, limits the scored interval to 2-120 sessions,
and bounds every cost and overlay parameter. Prior sessions are replayed only to
warm causal state; signals observed at Bar close can change exposure only at the
next Bar open, and exposure cannot exceed the Buy-and-Hold target. Results bind
the dataset checksum and policy hash, include deterministic curves and execution
diagnostics, and are always ad hoc, QFQ/current-universe non-PIT,
`NOT_ELIGIBLE`, state-mutation false and external-order transmission false.

## ADR-089 — Public market readers bind the exact Arrow schema

Status: accepted, 2026-08-26.

The public API's bounded Parquet helper verified checksum, row count, row-group
count and uncompressed size, while downstream readers requested the ten columns
they needed. A locally re-signed file could therefore add or reorder columns, or
change an ignored field type, without failing at the common open boundary.

The common public helper now requires exact equality with the ten-column Bar
Arrow schema before any daily, minute, selected-session or individual-stock
reader receives the file. A regression writes a checksum-matching extra-column
file and verifies rejection. This does not change the accepted production data,
the frozen strategy engine or any realtime/PAPER readiness state.

## ADR-090 — Intraday factor research uses next-Bar/T+1 pricing and cannot self-promote

Status: accepted, 2026-08-26.

The opening three-ten-minute-bullish hypothesis is evaluated on the signed
75-stock BaoStock dataset rather than on a synthetic example or a single stock.
The first six native five-minute Bars form three non-overlapping ten-minute
intervals. The signal is unavailable until the Bar labelled `10:00` closes, so
its entry uses the open of the next source Bar labelled `10:05`; because source
timestamps label interval ends, that price belongs to the interval starting at
`10:00`. A-share sellability is respected by exiting at the next trading
session's first Bar open, labelled `09:35` and starting at `09:30`. Signal-Bar
close execution and same-day exit are forbidden.

Each ad-hoc request must use 20-120 consecutive verified sessions plus a complete
next session, the exact 48-Bar grid, all 75 stock identities and all 15 frozen
sector identities. The result reports factor, event, correlation, performance,
cost, sector and stock evidence with a deterministic hash, but it never mutates
state or transmits an order. Viewed current-universe QFQ history is not blind or
point-in-time evidence, so even a passing retrospective run remains
`NOT_ELIGIBLE` until a separately authorized and locked forward PAPER stage.
Five-level order-book buying intensity cannot be computed without an authorized
L2 bid/ask feed and must remain `UNCONFIGURED`; OHLCV substitution is prohibited.

## ADR-091 — Intraday threshold tuning uses expanding disjoint test folds

Status: accepted, 2026-08-26.

Repeatedly choosing the body threshold on the same displayed 20-session result
would convert ordinary overfitting into a favorable-looking backtest. The
diagnostic therefore fixes the candidate set at `0/5/10/20 bps`, reserves the
second half for disjoint five-session test folds, and selects each fold's
threshold using only the expanding training prefix. The final training signal's
T+1 exit is known at the test day's 09:35 open, before the 10:00 factor signal;
the test fold's returns do not enter its own selection. A regression perturbs
future test prices and proves that the first selected threshold and its training
metrics do not change.

The stitched out-of-sample curve, fold metrics and all four fixed-threshold
sensitivity rows are returned and hashed with the rest of the response. The
2026-07-28 through 2026-08-24 diagnostic selected 5 bps in both folds and lost
4.6675% when stitched; every fixed threshold was negative. The source is still
current-universe QFQ history viewed after the hypothesis existed, so the result
is classified as reused-history diagnostic evidence, remains `NOT_ELIGIBLE`,
and cannot authorize PAPER, LIVE, state mutation or external orders.

## ADR-092 — Long-window intraday factor evidence is immutable and result-bound

Status: accepted, 2026-08-26.

The 20-session interactive result is too short to establish stability, but
repeatedly rerunning or selectively reporting a longer viewed window would make
the evidence easier to rewrite after seeing it. The latest 120 eligible sessions
are therefore evaluated once under a fixed contract: 60 initial training
sessions, six disjoint ten-session tests, prior-only threshold selection and the
pre-existing `0/5/10/20 bps` threshold set.

The immutable report binds the exact 75-stock/15-sector BaoStock manifest and
Parquet checksum, the factor and API source hashes, the evaluation contract and
the complete deterministic preview hash. The loader independently verifies
every fold identity, threshold sequence, summary arithmetic and safety field,
and also pins the known result hash. A payload whose result is changed and then
fully re-signed therefore still fails closed instead of becoming new accepted
evidence.

The fixed result is a veto: 9,000 stock-days, 1,001 signals, -27.1715% full-window
return, -28.3060% stitched test return, -28.7245% stitched maximum drawdown and
only one positive fold out of six. Every fixed threshold is negative. Because
the history had already been viewed, it is a reused historical diagnostic, not
independent confirmation; it remains `NOT_ELIGIBLE` and cannot mutate state or
authorize PAPER, LIVE or external orders.

## ADR-093 — Intraday factor stability uses fixed contiguous time blocks

Status: accepted, 2026-08-27.

A full-window event mean can look favorable because one market regime dominates
the sample, even when the factor reverses sign elsewhere. Random folds would
blur this chronology, and selecting subperiod boundaries after reading returns
would add another tuning degree of freedom. Every 20-120 session intraday factor
request is therefore divided by date into exactly four contiguous, mutually
exclusive blocks whose sizes differ by at most one session. The split is fixed
before any block statistic is interpreted and block outcomes cannot change the
rule, threshold, universe or evaluation window.

Each block computes the mean of signal-stock minus same-day-universe forward
returns at the trading-session level, consistent with the Newey-West event
sample rather than treating 75 same-day stocks as independent. Stability
requires at least one signal-bearing session in every block, at least three of
four positive block means, and less than 60% of total absolute block-mean
contribution from any one period. The disclosed 20-session and signed 120-session
three-bullish results both produced only two positive blocks. The 120-session
worst block was -0.3125% and its largest absolute contribution share was 36.88%;
concentration passed but sign stability failed. This is a retrospective veto,
not a new blind test or parameter search, and cannot authorize PAPER, LIVE,
state mutation or external order transmission.

## ADR-094 — Multi-rule effectiveness controls the submitted hypothesis family

Status: accepted, 2026-08-27.

Selecting several browser-local factor rules and reading each unadjusted
significance result would make false positives more likely as the number of
attempts grows. It would also be misleading to correlate a saved rule while
silently discarding the body threshold that defined its binary entry signal.
Every 2–8 rule comparison therefore reruns one identical 75-stock date window,
uses each rule's saved threshold for effectiveness, forms one conditional-excess
observation per signal-bearing session, and derives a two-sided asymptotic
normal p-value from its Newey-West t-statistic. Holm-Bonferroni adjustment is
then applied to the entire submitted family at alpha 0.05.

A rule passes only with at least 250 stock-day observations, 20 signals, ten
clustered signal sessions, positive mean conditional excess, Newey-West
`t > 1.96`, and Holm-adjusted `p <= 0.05`. Continuous directional scores remain
threshold-independent for the separate Pearson redundancy gate, where
`|rho| >= 0.90` rejects a pair. Neither diagnostic can change a rule, threshold,
strategy, dataset or PAPER state. The disclosed three-rule 20-session run had
zero effectiveness passes, all adjusted p-values equal to one, and a duplicate
pair at correlation one; it remains retrospective current-universe QFQ evidence,
`NOT_ELIGIBLE`, with LIVE and external order transmission disabled.

## ADR-095 — Long-window excess uncertainty uses paired stationary resampling

Status: accepted, 2026-08-27.

The fixed long-window report disclosed one stitched out-of-sample excess-return
point estimate, but its 60 daily strategy and benchmark observations are paired
and serially dependent. Independently resampling the two legs would destroy the
same-day market relationship, while an ordinary iid bootstrap would ignore
short runs in market conditions. The report therefore uses exactly 1,000 paired
stationary-bootstrap paths, expected block length ten and seed 20260827. Each
path applies one shared index sequence to strategy and benchmark, compounds both
legs, and records their total-return difference.

The signed summary reports the observed difference, 5th, median and 95th
percentiles, and the fraction of paths with positive excess. The API does not
trust those fields: it verifies the 60 test-day identities and independently
recomputes the entire distribution from the result's daily returns. The fixed
result had -12.4906% observed excess, -11.8338% median, a
[-23.9107%, +0.4281%] 5th/95th percentile range and 5.7% positive paths. These
are descriptive resampling diagnostics on reused history, not a calibrated
p-value or an unseen confirmation. They cannot retune the factor or threshold,
mutate state, authorize PAPER/LIVE, or transmit an external order.

## ADR-096 — Intraday strategies may use one bounded causal T+1 exit factor

Status: accepted, 2026-08-27.

Treating factors only as entry conditions leaves strategy construction
incomplete, but accepting arbitrary sell expressions would reopen the same code,
future-field and timing risks already closed on the entry side. QTrade therefore
adds one typed alternative to the unchanged next-session-open baseline: all
three T+1 ten-minute bodies from 09:30 through 10:00 must be bearish by more than
a bounded 0-1000 bps threshold. The condition is known only at the 10:00 close;
when true, execution uses the open of the following 10:05-labelled source Bar.
When false, the time exit uses the open of the final 15:00-labelled Bar, whose
interval begins at 14:55. Both paths are T+1 and prohibit same-Bar execution.

The benchmark applies the identical per-symbol exit rule, the API binds the
threshold and trigger/time-exit counts, and browser-local experiments preserve
the exit identity. Entry-factor effectiveness and correlation deliberately keep
their standardized next-session-open forward label, so a sell-policy profit is
not mistaken for stronger entry-factor evidence. The first viewed 20-session,
75-stock run at 5 bps triggered 28 of 192 entries, returned +1.3245% with
+4.9200% excess and -11.3789% maximum drawdown, but its stitched walk-forward
return was -10.0783% and the overall research gate failed. It remains reused
current-universe QFQ evidence, `NOT_ELIGIBLE`, with no state mutation, PAPER/LIVE
authority or external order transmission.

## ADR-097 — Exit factors require counterfactual timing-advantage evidence

Status: accepted, 2026-08-27.

A strategy can profit in one viewed window even when its conditional sell rule
adds no value. QTrade therefore does not treat total return, excess return or a
trigger count as proof that the T+1 bearish factor is effective. For each actual
entry where the factor triggers, it retains both causally available historical
sell prices: the open of the 10:05-labelled conditional-exit Bar and the open of
the 15:00-labelled time-exit Bar. Timing advantage is defined as conditional
exit price divided by time-exit price minus one; positive values mean selling
early preserved more proceeds. This is a sell-versus-sell comparison, so the
round-trip entry cost and common sell cost do not alter the contrast.

Same-session stocks are not independent. Triggered advantages are first averaged
by entry cohort/session, then evaluated with the existing automatically banded
Newey-West mean statistic. Passing requires at least 20 triggers, ten clustered
trigger sessions, positive clustered mean and `t > 1.96`. The first 5 bps run
had 28 triggers over ten sessions, +0.4898% clustered mean advantage, 64.29% raw
hit rate, t 1.8326 and a [-0.0340%, +1.0136%] 95% interval. Sign and coverage
passed, but statistical uncertainty did not; the exit factor and overall
research gate therefore fail. Entry-factor validity remains standardized to its
T+1-open forward label so sell-policy outcomes cannot inflate entry evidence.
The result is reused-history, non-PIT and `NOT_ELIGIBLE`, with no PAPER/LIVE,
state-mutation or external-order authority.

## ADR-098 — Bounded factor discovery locks one rule before a disjoint test

Status: accepted, 2026-08-27.

Allowing repeated edits after seeing one test period would turn that period into
training data and overstate robustness. QTrade therefore fixes the opening-rule
search space before evaluation: all 26 non-empty tuples of `BULLISH`, `BEARISH`
and `IGNORE` across the three completed ten-minute slots, `ALL` combination
only, crossed with thresholds 0/5/10/20 bps, for exactly 104 candidates. A
request must contain 40–120 complete sessions. Its first half is the only search
and ranking segment; the second half evaluates only the locked winner. Ranking
uses training-gate status, Newey-West t, conditional excess, lower threshold and
candidate ID in that order. The selection is complete at the first test day's
09:35 standardized exit, before the first possible 10:00 test signal.

The first viewed 40-session run selected `DIR-XDU-T0000`, the 0 bps
`IGNORE/BEARISH/BULLISH` rule. Training had 348 signals over 20 sessions,
+0.4931% session-clustered conditional excess and t 2.1897. The mutually
exclusive test had 326 signals over 20 sessions, -0.0919% conditional excess,
t -0.5462, -6.1491% total return, -1.8107% excess and -9.8972% maximum
drawdown. Effect, significance, total-return and excess-return gates failed.
This is an honest falsification, not grounds to search the same test segment
again. Since the current universe is non-PIT and the full history has been
viewed, the result remains `NOT_ELIGIBLE`, cannot mutate strategy/PAPER state,
and cannot authorize LIVE or external order transmission.

## ADR-099 — Factor-mining selection bias uses a fixed CSCV audit

Status: accepted, 2026-08-27.

One chronological holdout is necessary but does not show how strongly the
training winner depends on the particular split. QTrade therefore partitions
the same fixed 40-session discovery window into eight contiguous five-session
blocks and evaluates all 70 combinations of four training blocks against their
four-block complements. Within each combination it repeats the actual discovery
ordering: training hard-gate status, Newey-West t-statistic, session-clustered
conditional excess, lower body threshold and finally candidate ID. The selected
candidate is ranked on its complement with the same quality components; exact
ties receive an average rank. PBO is the fraction of selected rules whose
complement-rank logit is non-positive.

The viewed 104-candidate run produced 29 below-median winners out of 70, for PBO
41.43%. Only 35/70 selected complement returns had positive excess, their median
excess was -0.1788%, 17 different rules won at least once, and
`DIR-DDD-T0020` was most frequent at 24/70. The fixed descriptive gates
require PBO below 25%, positive complement excess in at least 60% of splits and
a positive median; all three fail. This agrees with, but cannot replace, the
failed chronological 20-session locked test. CSCV deliberately reuses every
viewed block, including the original locked-test segment, and its interleaved
training sets are not causal forward validations. It cannot change the original
winner, authorize a second search, mutate state, promote to PAPER/LIVE or send
an external order.

## ADR-100 — Discovery cost stress never reselects the locked factor

Status: accepted, 2026-08-27.

Testing only the caller's modeled cost leaves two ambiguities: a losing result
could be blamed entirely on fees, and repeated requests could use a favorable
cost setting as another hidden selection dimension. After the chronological
training segment locks one candidate, QTrade therefore keeps that identity,
its binary signals and its test sessions unchanged while recomputing exactly
0/5/10/20 bps round-trip cost rows. None of those rows can alter the training
rank or replace the locked candidate.

For `DIR-XDU-T0000`, the same 326 signals over the same 20 test sessions returned
-4.2527%, -6.1491%, -8.0089% and -11.6212% as costs rose. Maximum drawdown moved
from -8.9914% to -13.0167%, while excess returns remained negative at every
level. The fixed audit requires positive zero-cost total return, positive total
return at every fixed cost, positive 20 bps excess and mechanically non-
increasing total return as costs rise. Only the last integrity condition passes.
The cost-stage discovery research gate is the conjunction of chronological
locked test, CSCV/PBO selection-bias audit and locked-test cost stress; this run
is 0/3 before the later sector-stress extension.
Even a future historical pass would remain non-PIT/reused evidence without
PAPER, LIVE, state-mutation or external-order authority.

## ADR-101 — Sector robustness uses the locked test without stock reselection

Status: accepted, 2026-08-27.

An aggregate 75-stock loss could conceal profitable sector pockets or be driven
by one unusually weak industry. QTrade therefore holds the discovered candidate,
threshold, 5 bps cost, 326 signals and 20 chronological test sessions fixed while
computing two complementary diagnostics: the strategy inside each of the 15
sectors, and the remaining 70 stocks after excluding each complete sector. No
sector result may alter the factor, threshold, stock list or training rank.

Only four sector-only returns were positive. Electronics was worst at -16.4469%,
while machinery was best at +9.0138%. More importantly, all 15 leave-one-sector-
out portfolios stayed negative: removing power equipment gave the worst
-7.2180%, and removing electronics gave the least-negative -4.7210%. The locked
rule therefore fails broadly rather than because of one isolated sector. The
sector gate requires every sector to contain a signal, at least 60% positive
sector-only returns, at least 80% positive exclusion returns and a non-negative
worst exclusion. Only signal coverage passes. The consolidated historical
discovery gate now requires chronological lock, CSCV/PBO, cost stress and sector
stress and is 0/4. It remains non-PIT, reused, `NOT_ELIGIBLE`, stateless and
external-order-disabled.

## ADR-102 — Stock robustness keeps all locked-test symbols visible

Status: accepted, 2026-08-27.

Sector aggregates and whole-sector exclusions can still hide a rule that works
for only a minority of stocks, misses some constituents entirely or depends on
a few extreme names. QTrade therefore replays the exact locked candidate,
threshold, 5 bps cost, 326 signals and 20 chronological test sessions once per
stock for all 75 frozen symbols. Per-stock rows cannot change the training rank,
factor, threshold or stock universe and cannot be filtered back into selection.

Only 22/75 stocks had positive total return, a 29.33% positive ratio, and the
median stock return was -1.1644%. `600276` had no locked-test signal. The five
largest absolute stock returns belonged to `002558`, `002353`, `002028`,
`000408` and `603993` and contributed 24.79% of total absolute per-stock return
magnitude. The fixed stock gate requires every stock to have a signal, at least
60% positive stocks, a positive median return and less than 30% top-five
absolute-return concentration. Only concentration passes. The consolidated
historical discovery gate now requires chronological lock, CSCV/PBO, cost,
sector and stock stress and is 0/5. It remains non-PIT, viewed reused history,
`NOT_ELIGIBLE`, stateless and external-order-disabled.

## ADR-103 — Market-state stress uses only a previous-session label

Status: accepted, 2026-08-27.

Grouping a test by its own realized return would use future information and
could make an untradeable retrospective explanation look causal. QTrade instead
labels each locked-test day from the sign of the previous session's 75-stock
equal-weight gross return measured from 10:05 entry to the next trading day's
09:35 open. The label is therefore complete at 09:35 on the current day, before
the current opening factor is known at 10:00. The first test label may use the
last training session's already completed return. State rows never enter the
candidate rank and cannot replace the locked candidate, threshold or universe.

The 20-day locked test contained both predefined states: 12 days followed a
negative prior session and carried 188 signals; eight followed a non-negative
prior session and carried 138. The first state returned -0.7402% with +1.4887%
excess, while the second returned -5.4493% with -3.2917% excess. The fixed state
gate requires both states, at least five sessions each, signal coverage, causal
label timing, positive strategy return in both and positive excess in both. The
first four integrity/coverage conditions pass and both performance conditions
fail. The consolidated historical discovery gate now requires chronological
lock, CSCV/PBO, cost, sector, stock and causal state stress and is 0/6. It
remains non-PIT, viewed reused history, `NOT_ELIGIBLE`, stateless and
external-order-disabled.

## ADR-104 — Locked-test placebo permutations preserve each session's signal count

Status: accepted, 2026-08-27.

A weak locked result may simply identify a random subset of stocks on each test
day. Comparing it with a global shuffle would be misleading because the opening
rule produces different signal counts across sessions and market-wide returns
also differ by day. QTrade therefore keeps the selected candidate, chronological
test dates and every session's observed signal count fixed. For each of exactly
499 replications, a deterministic generator with seed 20260827 shuffles only the
stock identities within that session and computes the same signal-stock minus
same-day-universe forward return, averaged once per signal-bearing session.

The viewed locked result had -9.1894 bp mean session-conditional excess. The
placebo distribution had -0.0331 bp mean, +0.2642 bp median and +17.9292 bp 95th
percentile; 387/499 placebo values were at least as high as observed. The
predeclared +1-corrected one-sided p-value is therefore 388/500, or 77.60%.
Passing requires at least ten signal sessions, exact per-session signal-count
preservation, positive observed excess, p <= 0.05 and observed excess above the
placebo 95th percentile. Only coverage and count preservation pass. Placebo
assignments never enter candidate training, ranking or reselection. The audit
reuses the already viewed locked test and is not independent confirmation. The
consolidated historical discovery gate now requires chronological lock,
CSCV/PBO, cost, sector, stock, causal market-state and placebo stages and is
0/7; no result can authorize PAPER/LIVE, state mutation or external orders.

## ADR-105 — Execution-delay stress moves strategy and benchmark entry together

Status: accepted, 2026-08-27.

A factor known at 10:00 may not reach an executable order at the first causal
source-Bar open. Reporting only the 10:05-labelled Bar can therefore hide
sensitivity to data handling, risk checks or queue latency. QTrade now retains
the original 10:05 open and the next two provider-native opens at 10:10 and
10:15 in every factor observation. The locked-test stress fixes the selected
candidate, 326 signal identities, 20 test sessions, exits and requested 5 bps
cost. For each delay, both selected stocks and the same-day 75-stock benchmark
use the corresponding entry open, so the comparison does not mix execution
clocks. Delay rows never enter training, ranking or candidate reselection.

For `DIR-XDU-T0000`, total return at 10:05/10:10/10:15 was respectively
-6.1491%, -5.0013% and -4.6735%; excess was -1.8107%, -0.9354% and -0.7027%.
Sharpe remained -4.7951, -3.9658 and -3.7751, with maximum drawdown -9.8972%,
-8.9241% and -8.0363%. The fixed gate requires all three delay identities,
unchanged signal/session counts, positive total return at every delay and
positive excess at every delay. Only identity and coverage pass. The result
shows that modest delay changes loss magnitude but does not rescue the factor.
The consolidated historical discovery gate is 0/8 and remains reused-history,
non-PIT, `NOT_ELIGIBLE`, stateless and external-order-disabled.

## ADR-106 — Intraday PAPER admission is an eight-gate read-only dossier

Status: accepted, 2026-08-27.

A generic PAPER `NOT_ELIGIBLE` label does not show whether a run failed research,
failed economic backtesting or simply lacks operational authority. Every
intraday-factor preview therefore reports eight separately evidenced gates:
verified historical identity, research quality, causal total/excess return,
immutable strategy registration, genuinely unseen forward SHADOW evidence,
authorized realtime-provider readiness, a fresh runtime attestation and
reconciliation readiness. Historical identity alone cannot substitute for any
later gate.

The current three-ten-minute-bullish preview passes 1/8: the dataset identity is
checksum-bound, while research and causal-return gates fail and all five
operational/lineage gates are unavailable. Admission is always `BLOCKED` while
those fixed unavailable gates remain false. The dossier is part of the existing
read-only preview; it cannot register a strategy, initiate PAPER, change runtime
mode, persist state or transmit an external order. LIVE remains separately
disabled and requires explicit user authorization.

## ADR-107 — Capacity uses the completed signal Bar and refuses synthetic RMB amount

Status: accepted, 2026-08-27.

A profitable factor can still be untradeable when the intended order consumes
too much minute liquidity. Using the 10:05 entry Bar's realized volume would be
future leakage because the factor is known at 10:00. QTrade therefore keeps the
selected `DIR-XDU-T0000`, its 326 locked signals and 20 test sessions unchanged,
and uses only the completed 09:55-10:00 Bar volume already known at signal time.
BaoStock's minute-data contract defines `volume` as interval transaction
quantity in shares. Capacity is predeclared at 1%, 5% and 10% participation and
rounded down to the 100-share A-share order lot. No capacity row enters training,
ranking, filtering or reselection.

At 1% participation, the minimum, lower median and maximum capacity are 400,
13,300 and 241,700 shares; all 326 signals support at least one lot. The 5% row
is 2,000/66,700/1,208,900 shares and the 10% row is
4,000/133,500/2,417,900 shares. These are share-capacity diagnostics only. The
frozen source contains QFQ OHLCV and no provider-native `amount`; multiplying
adjusted price by volume would not reconstruct actual traded RMB amount. QTrade
therefore returns `UNAVAILABLE_QFQ_OHLCV_WITHOUT_PROVIDER_AMOUNT`, labels RMB
capacity unavailable and fails that criterion closed. The consolidated viewed-
history discovery gate becomes 0/9 and remains non-PIT, `NOT_ELIGIBLE`,
stateless and external-order-disabled.

## ADR-111 — Sequential advancement and PAPER admission share one next action

Status: accepted, 2026-08-27.

The sequential contract correctly rejected the current factor and required a
new predeclared hypothesis, while PAPER admission still returned a fixed
“register strategy then forward SHADOW” instruction. Those simultaneous actions
were contradictory and could encourage registration of failed research.

PAPER admission now consumes the exact next action computed by sequential
advancement. When any prerequisite research stage or the causal return gate
fails, both surfaces return
`PREDECLARE_NEW_HYPOTHESIS_BEFORE_NEW_EVIDENCE`. Only a chain that passes those
checks may return `REGISTER_IMMUTABLE_STRATEGY_THEN_AUTHORIZED_FORWARD_SHADOW`.
The frontend maps the reason code to Chinese without replacing the exported
machine-readable value.

This is a read-only routing decision. It does not predeclare a hypothesis,
register a strategy, start SHADOW/PAPER, mutate state or transmit an order.

## ADR-108 — L2 buying intensity is an untested hypothesis behind a six-gate data contract

Status: accepted, 2026-08-27.

The expression `(bid_size_1+...+bid_size_5)/(ask_size_1+...+ask_size_5)` is not
a valid factor observation until the underlying order book has an authorized,
causally timestamped and replayable identity. QTrade therefore defines exactly
five bid prices/sizes and five ask prices/sizes, normalized to shares only after
the provider's native quantity unit is certified. Every observation also needs
symbol, exchange, provider event time, local receive time and monotonic sequence,
and is restricted to continuous auction trading.

Missing, non-finite or negative values, a non-monotonic or crossed book, a stale
or future snapshot and zero total ask size reject the observation; zero ask size
does not produce infinity. A finalized-minute correction may use only a same-
symbol, same-provider snapshot already received before its decision. The actual
freshness threshold must be certified against the selected provider rather than
invented before integration.

Readiness requires six independent gates: authorized L2 provider, certified
five-level schema, quantity-unit normalization, event clock plus sequence,
certified freshness, and recorded-session replay reconciliation. All are false
today, so readiness is 0/6 and `UNCONFIGURED`. `buying_intensity > 1` remains the
user's untested buy hypothesis, not an effectiveness result; no sell condition
has been defined and no correlation test, strategy backtest or PAPER admission
exists. The contract is read-only, derives nothing from OHLCV, mutates no state
and cannot transmit an external order.

## ADR-109 — Factor backtests derive a stable read-only strategy identity without registration

Status: accepted, 2026-08-27.

Displaying entry, exit and cost fields does not prove which strategy a result
belongs to. QTrade therefore canonicalizes the entry factor ID, expression,
directions and threshold; entry rule; exit type and optional exit factor;
causal prices; equal-weight sizing; matched benchmark; cost; and T+1 constraint.
The resulting SHA-256 derives the `adhoc-intraday-*` strategy ID.

Observed conditional-exit and fallback-time-stop counts are outcomes, so they
are excluded from the specification hash. The same strategy can therefore keep
the same identity across evaluation windows. Tests independently reconstruct
the declared hash scope and verify the derived identity.

The research page exposes the ID and full hash, and the PAPER registration
blocker includes both. This creates no `StrategyRegistry` insertion, registered
version or lifecycle change. The strategy remains `AD_HOC_UNREGISTERED`, PAPER-
ineligible, read-only, stateless and external-order-disabled.

## ADR-110 — Computed strategy diagnostics do not imply sequential advancement

Status: accepted, 2026-08-27.

Running a strategy backtest is useful even when the underlying factor fails,
but displaying that computed strategy as `AVAILABLE` confuses an observed
diagnostic with permission to advance. QTrade therefore evaluates three ordered
research prerequisites before research-admitting strategy construction: factor
effectiveness, correlation and rolling robustness.

The response preserves each raw diagnostic outcome while also reporting the
first blocking stage. The current three-bullish-Bar rule passes correlation but
fails factor validity and robustness, so it passes `1/3`, first blocks at
`FACTOR_VALIDATION`, and labels its strategy `DIAGNOSTIC_PREVIEW_ONLY`. Its
causal backtest is still computed for rejection evidence, not promotion.

No response from this historical non-PIT endpoint can register the strategy or
authorize PAPER. Reselection on the viewed window is forbidden; the next
hypothesis must be predeclared before new evidence. The contract is read-only,
stateless and external-order-disabled.

## ADR-112 — Rejected results expose a preregistration preflight, not a registry write

Status: accepted, 2026-08-27.

Telling a researcher to predeclare a new hypothesis is insufficient when the
interface does not state what must be frozen or which evidence is already
contaminated by inspection. Every intraday-factor result therefore includes a
read-only next-hypothesis preflight bound to its strategy ID and specification
hash, data version and checksum, viewed date range, first blocking stage and
computed next action.

The preflight mirrors the seven required `ResearchHypothesis` fields and the
thirteen immutable `ExperimentRunSpec` fields. It additionally requires a new
hypothesis identity, a new evidence period disjoint from the viewed and prior
held-out periods, an independent leakage-review PASS, fixed candidate and
robustness budgets, a fixed cost model, an immutable specification/hash before
new data access and disclosure of prior evidence used as motivation. The
rejected result may explain a new motivation but may not directly choose new
parameters.

Preregistration is distinct from `StrategyRegistry.register`. This response
creates neither artifact nor row; a future completed preregistration and review
may register an immutable specification only as `IDEA`. The legal research and
operations path remains `IDEA → EXPERIMENT → CANDIDATE → VALIDATED → PAPER →
SHADOW → APPROVED → CHAMPION`, with explicit human authorization still required
for `APPROVED` and `CHAMPION`. The preflight cannot start PAPER/LIVE, mutate
state or transmit an external order.

## ADR-113 — Behavior identity cannot substitute for StrategySpec admission

Status: accepted, 2026-08-27.

The factor preview's deterministic hash identifies entry, exit, execution,
sizing, benchmark and cost behavior. It intentionally omits registry metadata,
so treating it as the immutable `StrategySpec` hash would leave name, family,
version, code commit, model, time split, timestamps and lineage undefined.
QTrade therefore exposes a separate read-only registration preflight rather
than allowing the behavior hash to authorize a registry insert.

The preflight lists all fifteen `StrategySpec` fields and nine required gates:
ordered research stages, causal backtest, signed hypothesis preregistration,
independent leakage review, behavior-hash binding, complete registry-spec/hash
binding, dataset identity, a disjoint frozen time split and either an explicit
root declaration or a valid parent binding. The current rejected rule can
source six fields from its preview and passes only behavior and dataset identity
(`2/9`). Its complete registry hash is therefore not computable.

No registration operation is exposed by this historical endpoint. Even after
a future valid insert creates `IDEA`, transition to `EXPERIMENT` requires a
complete `ExperimentRunSpec`, a quality-passed data manifest and an independent
leakage-review PASS. The preflight writes no registry row, starts no PAPER/LIVE
runtime, mutates no state and transmits no external order.

## ADR-114 — Implemented strategy baselines are a catalog, not admission authority

Status: accepted, 2026-08-27.

The repository already contained five baseline strategy structures, but the
strategy center exposed only experiment rows selected from existing evidence.
That made the intended “start from a known strategy, freeze it, then backtest”
workflow invisible and encouraged treating a past candidate as the only entry
point.

QTrade now publishes a deterministic read-only catalog for equal-weight
buy-and-hold, moving-average trend, ETF momentum rotation, ETF momentum with
trend/volatility constraints, and cross-sectional factor ranking. The catalog
uses the actual implementation references and states data interval, universe
scope, entry and exit rules, unfrozen defaults, required inputs and limitations.
Equal-weight buy-and-hold is explicitly comparison-only; the other four are
active research baselines. “Implemented” does not mean profitable, mature or
production-validated.

Reading a catalog row performs no selection or write. It cannot create an
experiment, register a strategy, execute arbitrary code, start PAPER/SHADOW or
LIVE, mutate runtime state or transmit an order. The earliest next step is to
freeze a complete `ExperimentRunSpec` before causal backtesting; all later
research and authorization gates remain unchanged.

## ADR-115 — A hash-complete run-spec draft is not an immutable experiment

Status: accepted, 2026-08-27.

The template catalog made implemented starting points visible, but selection
could not carry an implementation-consistent configuration into the bounded
causal backtest lab. Researchers also had no single pre-run artifact containing
the complete experiment identity, data identity, time split, parameters, costs
and seed.

Only the three templates whose behavior the existing multi-asset preview can
faithfully reproduce are connected: ETF momentum rotation, ETF momentum with
trend/volatility controls, and cross-sectional reversal or low-volatility
ranking. Pure momentum disables risk filters explicitly rather than retaining
hidden defaults. The backend rejects template/parameter mismatches, split/test
window mismatches and dates outside the verified dataset, then constructs the
existing 13-field `ExperimentRunSpec` and its canonical deterministic hash.

This endpoint remains stateless. A complete field set and stable hash prove
reproducible identity, not immutable preregistration, independent leakage
review or fresh evidence. The historical test interval is already visible, so
the draft cannot be relabelled as a new held-out result. It creates no
experiment or registry row, starts no PAPER/SHADOW/LIVE runtime, mutates no
state and transmits no order.

## ADR-116 — Draft and preview share one request before receiving a binding hash

Status: accepted, 2026-08-27.

A complete draft hash did not prove that a separately requested preview used the
same parameters. An operator could edit the form between the two calls and
export two individually valid artifacts with no verifiable relationship.

The bound-preview endpoint therefore accepts one complete draft request,
recomputes and validates its `ExperimentRunSpec`, reloads the exact verified
dataset, runs the causal preview and then hashes the spec hash, preview hash,
template identity, data version and checksum together. The UI executes only its
stored draft request and invalidates the draft and binding after any form field
changes. This is a fail-closed lineage check, not an implicit parameter lock.

The combined response remains stateless. It is not an immutable experiment
artifact, an independent leakage-review PASS or a new unseen test. It cannot
create a registry row, start PAPER/SHADOW/LIVE, mutate state or transmit an
order.

## ADR-117 — Adjustable previews use an engine-matched benchmark

Status: accepted, 2026-08-27.

Reporting a strategy return without a matched benchmark left “excess return”
undefined. Reusing a benchmark from another experiment could silently mix date
windows, cash reserve, costs, slippage, symbol availability or execution timing.

Each preview now runs `EqualWeightBuyHold` through the same
`DailyMultiAssetBacktester`. The comparison uses the same selected history,
initial cash, reserve, transaction cost, slippage and close-decision/next-open
timing. The benchmark can allocate only to symbols present in history at its
first decision close. Strategy and benchmark sessions must match exactly before
the API emits aligned equity/drawdown curves, benchmark return, cumulative
excess and information ratio.

Matching the engine does not fix the data universe. The current universe is not
point-in-time and retains survivorship bias, so these metrics remain diagnostic
and cannot provide research admission, PAPER/LIVE authority, state mutation or
external order transmission.

## ADR-118 — Browser-local comparison preserves binding identity without claiming persistence

Status: accepted, 2026-08-27.

The bound-preview response exposed template, spec and binding hashes, but the
existing local comparison record discarded them. A saved row could therefore
not distinguish a hash-bound replay from an ordinary adjustable preview.

Bound saves now retain those three identifiers under an explicit
`BROWSER_LOCAL_REPLAY_BINDING_NOT_IMMUTABLE` classification. Before writing,
the client requires the bound response and current result to share their result
hash, data version/checksum and template strategy identity; all binding gates
must hold and immutable-write, registry, runtime, state-mutation and external-
order flags must remain false. A mismatch fails closed without changing local
storage. Ordinary previews store an explicit null binding, and legacy schema-v1
records lacking the field normalize to that same state.

The comparison table is browser-local and removable. Preserving replay lineage
does not create an immutable experiment, registry record, leakage-review PASS,
fresh evidence, promotion eligibility, PAPER/LIVE authority, state mutation or
external order transmission.

## ADR-119 — Local A/B comparison uses the full matched metric surface

Status: accepted, 2026-08-27.

Saved preview records already retained the complete metric dictionary, but the
browser table displayed only total return, Sharpe and maximum drawdown. That
made repeated tuning omit annualization and the matched benchmark relationship
even though the single-run panel exposed them.

Each local row now displays total return, annualized return, matched benchmark
return, active excess, Sharpe, information ratio and maximum drawdown. Values
come only from the checksum- and result-hash-bound saved summary; older records
with missing fields display unavailable values rather than reconstructed or
invented metrics. The wider table scrolls horizontally instead of hiding a
metric.

The matched benchmark still uses the current non-PIT universe and retains
survivorship bias, which is stated beside the table. A complete comparison
surface improves diagnosis but does not provide independent validation,
immutable persistence, registry admission, promotion, PAPER/LIVE authority,
state mutation or external order transmission.

## ADR-120 — Reloading a local binding does not restore its full run spec

Status: accepted, 2026-08-27.

A browser-local bound row retained its template, spec and binding identities,
but the existing load action always cleared the selected template. Restoring the
old spec hash directly would be worse: the compact row does not contain the
original experiment ID, commit, chronological split or seed needed to reproduce
the 13-field `ExperimentRunSpec`.

Loading a compatible bound row now restores only the saved template identity
and exact backtest parameters. All prior result, draft and binding state is
cleared, and the run-spec form resets to fresh dataset-bounded defaults with an
empty experiment ID. The prior spec and binding hashes remain visible in the
load message as historical identifiers, and the operator must enter a new ID
and generate a complete draft before another bound run. An ordinary row remains
unbound; an incompatible local template/parameter pair loads parameters but
restores no template.

This preserves a useful replay starting point without fabricating missing
lineage. Reloading creates no immutable experiment or registry record, provides
no fresh evidence or promotion authority, starts no PAPER/LIVE runtime, mutates
no state and transmits no external order.

## ADR-121 — Local comparisons export deterministically without becoming evidence

Status: accepted, 2026-08-27.

Browser-local A/B rows contained the parameters, common metrics and replay
identities needed for repeated tuning, but the table could not be carried out of
one localStorage instance as a complete set. Exporting only the currently visible
single result would omit the comparison context and any saved binding identity.

The table now builds one schema-versioned JSON value bound to the current
provider, provider version, data version, checksum and PIT flag. It includes the
ordered valid rows with their parameters, metrics, preview hashes and optional
template/spec/binding identities. The export omits a generation timestamp, so
the same browser state serializes to the same JSON. No-record state disables the
download rather than emitting an empty artifact that might be mistaken for
completed research.

The export is a removable, portable read-only checkpoint. It has explicit false
flags for immutable persistence, registry writes, PAPER/LIVE starts, state
mutation and external order transmission. There is no import, automatic sync or
server write, and the file provides no independent leakage review, new unseen
evidence, promotion or runtime authority.

## ADR-122 — Local factor comparison shows stored research dimensions together

Status: accepted, 2026-08-27.

The intraday-factor local record already retained effectiveness, correlation,
robustness and strategy metrics, but its table omitted several of them. Comparing
only total return, Sharpe, drawdown, stitched return and Newey-West t made the
saved notebook less complete than the factor workflow it was intended to audit.

Each row now displays cumulative and annualized return, active excess, Sharpe,
maximum drawdown, Rank IC, maximum absolute correlation, stitched out-of-sample
return and excess, Newey-West t and its 95% interval, together with the existing
research and robustness gates. The UI reads these fields directly from the
validated checksum- and preview-hash-bound summary; it performs no recomputation
or missing-value inference.

This is a diagnostic comparison of already viewed history. Wider visibility does
not provide a new independent sample, immutable experiment, factor-selection
permission, registry admission, promotion, PAPER/LIVE authority, state mutation
or external order transmission.

## ADR-123 — Local factor rows preserve read-only strategy identity

Status: accepted, 2026-08-27.

The factor response bound entry factor, exit rule, sizing, benchmark, cost and
T+1 timing into a read-only strategy ID and specification hash. Saving the
response locally discarded that identity, so a retained factor row could not be
traced to the exact combined strategy behavior it represented.

New rows retain the `AD_HOC_READ_ONLY_STRATEGY_IDENTITY` object. Before writing,
the client requires a 64-character SHA-256, the exact hash-derived
`adhoc-intraday-*` ID, the declared scope excluding observed exit counts, an
unregistered status, immutability within the response, and false PAPER,
promotion, state-mutation and external-order flags. Legacy schema-v1 rows without
the field normalize to an explicit null identity; mismatched or relaxed
identities fail closed.

The preserved identity is replay lineage only. It does not supply the remaining
StrategySpec fields, create a StrategyVersion, register or promote a strategy,
start PAPER/LIVE, mutate state or transmit an external order.

## ADR-124 — Individual minute preview reports common performance metrics

Status: accepted, 2026-08-27.

The individual-stock five-minute preview returned cumulative return, drawdown,
minute volatility, exposure, turnover and cost, but omitted annualized return,
annualized volatility, Sharpe and a risk-adjusted comparison with its matched
Buy-and-Hold curve. That made repeated parameter previews use a narrower metric
surface than the rest of the backtest workflow.

The API response layer now derives aligned five-minute returns from the same
selected causal equity curves and passes them through the shared research metric
calculator. Annualization is explicit and fixed at 48 native Bars per session,
252 sessions per year and a zero risk-free rate. Base and overlay results expose
annualized return, volatility and Sharpe; the overlay also exposes information
ratio against the aligned base returns. The browser displays the values and the
annualization convention, and the existing full-result export retains them.

This change does not alter the minute overlay engine, its signal timing or any
preregistered collector/evaluator. The preview remains ad hoc, current-universe,
QFQ/non-PIT, `NOT_ELIGIBLE`, stateless and unable to start PAPER/LIVE or transmit
an external order.

## ADR-125 — Current factor results can locate an individual stock

Status: accepted, 2026-08-27.

Each intraday factor run already produces independent per-stock group metrics for
the same 75-stock causal replay, but the current-result tab only paginated them.
That made checking a named stock unnecessarily difficult and obscured an existing
individual-stock diagnostic capability.

The stock tab now filters the already returned rows by six-digit identity or
name, reports the displayed/total count and retains signal count, conditional
mean, hit rate, strategy return, active excess and drawdown. Filtering is purely
client-side and explicitly labelled as navigation within one result. It does not
rerun the endpoint, alter the result hash, select a stock or tune a parameter.

The filter adds no evidence, promotion, registry, PAPER/LIVE, state-mutation or
external-order authority.

## ADR-126 — Individual minute previews compare within one page session

Status: accepted, 2026-08-27.

The individual-stock five-minute page supported repeated runs and complete
single-result exports, but each successful run replaced the visible result.
That prevented direct comparison of parameter changes using the common metric
surface now returned by the API.

The browser now keeps the fifteen latest distinct preview hashes for the lifetime of
the open page. Each row identifies the stock, interval, core parameters, cost,
sector, cumulative and annualized return, Sharpe, drawdown, active excess, information
ratio, exposure and turnover. Loading a row copies only its request parameters
back into the form, clears the current result and requires a new API run.

This deliberately uses component memory rather than localStorage or a server
write. Refreshing clears the comparison; it is not an immutable experiment,
independent evidence, promotion input, PAPER/LIVE authorization, state mutation
or external-order capability.

## ADR-127 — Overview exposes the ordered quantitative promotion gates

Status: accepted, 2026-08-27.

The dashboard provided separate pages for research, strategies, experiments,
backtests and PAPER operations, but its overview did not express their required
ordering. A user could navigate to a downstream diagnostic after a failed factor
test and mistake page availability for promotion eligibility.

The overview now renders the explicit sequence from verified data and factor
definition through effectiveness/correlation/robustness, strategy construction,
causal backtest and PAPER/SHADOW to separately authorized LIVE. Each linked stage
uses current verified snapshot facts: the signed minute dataset completes only
the data stage, while the existing three-bullish-bars report stops at factor
validation with `NOT_ELIGIBLE`. Templates and historical backtests are labelled
diagnostic, and the public PAPER stage is read-only.

The sequence is a read-only navigation and explanation surface. It does not
change API admission, create a strategy or experiment, promote a result, start a
runtime, enable LIVE, mutate state or transmit an external order.

## ADR-128 — Six-stage workflow becomes vertical below 1200 pixels

Status: accepted, 2026-08-27.

The six-step promotion path fitted a wide desktop, but Ant Design's default
responsive threshold kept it horizontal at 700 pixels. A real browser check
showed compressed titles and gate reasons even though unit and build checks were
green.

The overview now uses the shared responsive breakpoint state and selects a
vertical Steps orientation whenever the viewport is below `xl` (1200 pixels).
The component's lower built-in switching threshold is disabled, so tablet and
narrow desktop widths do not fall back to the unsuitable horizontal layout.
The wide desktop layout remains unchanged.

This is presentation-only. It does not modify research evidence, gate results,
runtime state, PAPER/LIVE admission or external-order capability.

## ADR-129 — Parameter edits invalidate visible ad-hoc results

Status: accepted, 2026-08-27.

The general backtest lab already removed a result when its inputs changed, but
the individual-stock minute and intraday-factor forms did not. A user could edit
cost, windows, dates or factor conditions while the prior result hash and metrics
remained visible, creating an incorrect visual association between new inputs
and old output.

Both forms now clear their current result and error on every input change while
preserving explicitly retained comparison rows. The factor workbench also clears
the prior save status and rule-correlation result. Its 104-candidate discovery is
independent of most form fields, so it is invalidated only when its actual inputs,
end date or cost, change.

Clearing is client-side provenance hygiene. A new result appears only after a
fresh API run; no dataset, research engine, registry, promotion, PAPER/LIVE state
or external-order permission changes.

## ADR-130 — Factor forms expose a readable pre-run entry and exit draft

Status: accepted, 2026-08-27.

The bounded factor builder separated interval directions, combination, body
threshold and exit behavior across several controls. Although safe to submit, it
was difficult to review the combined mathematical and temporal rule before a
run.

The workbench now derives a readable draft from the live form. It emits one
body-return predicate per active ten-minute interval, joins them with the chosen
AND/OR operator, shows the factor-known and next-Bar entry clock, and displays
the full T+1 exit branch including its threshold and fallback time stop. Ignored
intervals are omitted rather than represented as a false predicate.

The draft is deliberately not called canonical. It is not a server-validated
StrategySpec, hash, experiment, effectiveness result or admission record. Only a
fresh backend run supplies authoritative identity and evidence; the draft cannot
change promotion, PAPER/LIVE or external-order authority.

## ADR-131 — Session comparison rows retain visible result lineage and export

Status: accepted, 2026-08-27.

The individual minute comparison deduplicated rows by the backend preview hash,
but did not display that identity. Once a form edit correctly cleared the main
result, the retained full response also had no export action, so a prior tuning
run could no longer be recovered from the UI.

Each comparison row now displays a short preview-hash prefix and can export its
complete original JSON response. The shared JSON button accepts optional visual
size and type properties so the action fits in a table without changing its
serialization or download behavior elsewhere.

The browser does not recompute the hash, upload the export, persist the session
or create an experiment. The row remains an ephemeral diagnostic and supplies no
registry, promotion, PAPER/LIVE, state-mutation or external-order authority.

## ADR-132 — Individual-stock minute parameters expose a pre-run policy draft

Status: accepted, 2026-08-27.

The individual-stock minute form exposed every bounded overlay parameter, but a
user still had to infer how the short and long averages, volatility threshold,
drawdown threshold and cooldown combine into actual exposure changes. That made
the reduction-only risk overlay easy to mistake for an independent stock-entry
strategy.

The form now derives a readable draft from its live values. It shows the constant
full-exposure base target, trend and volatility reductions, drawdown-to-zero
condition, safe-Bar recovery, required history, next-five-minute-open execution
and per-exposure-change cost. Changes update the draft while continuing to clear
any old result.

The draft is browser-derived and explicitly non-canonical. It does not define a
stock-selection factor, create server strategy identity, prove performance,
register or promote a policy, start PAPER/LIVE, mutate state or transmit an
external order.

## ADR-133 — Session comparison can represent one stock from every signed sector

Status: accepted, 2026-08-27.

The verified minute universe spans fifteen sectors, while the individual-stock
session comparison retained only ten runs and omitted sector identity. Even an
intentional one-stock-per-sector diagnostic therefore could not be reviewed in
one page session.

The comparison now retains fifteen distinct result hashes, displays the dataset-
bound sector name on every row and reports the count of distinct sector codes in
the current session. It still evicts older results deterministically and remains
small enough for direct visual comparison.

Sector coverage is descriptive only. The browser does not automatically select
or run stocks, prove cross-sectional robustness, persist evidence, promote a
strategy, start PAPER/LIVE, mutate state or transmit an external order.

## ADR-134 — Factor catalog is derived from signed evidence, not a new registry

Status: accepted, 2026-08-27.

Factor definitions existed across the daily factor-search response, the
intraday long-window report and its optional exit/L2 contracts. Adding a new
backend catalog endpoint would change the API module whose source digest is
bound into the signed long-window evidence, invalidating that evidence for a
presentation-only improvement.

The research page therefore derives a read-only catalog from those existing
responses. Daily rows are the union of diagnosed and passing signed factor IDs
with explicitly registered formulas; the opening factor may use the recognized
signed report identity as a fail-closed fallback. Exit and L2 rows appear only
when the report actually contains those contracts. Unknown daily definitions
remain visibly non-executable instead of accepting arbitrary expressions.

The catalog is a browser evidence index with deterministic JSON export. It is
not `FactorRegistry`, does not persist or execute definitions, and cannot create
a strategy/experiment, alter promotion or PAPER/LIVE state, mutate the runtime
or transmit an external order.

## ADR-135 — Cross-sector individual diagnostics freeze one shared form snapshot

Status: accepted, 2026-08-27.

The individual-stock lab could retain one result from every signed sector, but
the operator still had to choose and run each stock manually. That made it easy
to change a parameter between sectors or stop after favourable rows, while the
session coverage count alone could not distinguish a failed request from a
completed sector diagnostic.

The new batch action sorts the signed stock inventory by sector code and symbol,
selects the first stock from each sector, validates the form once and reuses that
exact date/cost/policy snapshot for every request. Calls are sequential to keep
the public stateless service load bounded. Successful responses replace the
session comparison; failures are counted and omitted without retry, fabrication
or a full-coverage claim. The complete session array is exportable.

This is a convenience diagnostic over the existing single-stock endpoint, not a
new engine or statistical robustness test. It does not select parameters per
stock, represent every stock in a sector, persist evidence, register/promote a
strategy, start PAPER/LIVE, mutate runtime state or transmit an external order.

## ADR-136 — Batch cross-section summaries preserve the successful-response denominator

Status: accepted, 2026-08-27.

The deterministic sector batch exposed every successful result, but an operator
still had to scan the table to find how many representative stocks were positive
and which one carried the worst return or drawdown. A favourable row could receive
attention while a failed request or weak tail remained easy to overlook.

The browser now derives one bounded summary from the batch's successful responses.
A row enters the calculation only when overlay return, excess return and maximum
drawdown are finite. The UI reports the valid metric count against successful
responses, positive return and excess counts, the median overlay return, the
worst-return identity and the deepest-drawdown identity. Failed requests are not
assigned zero, copied values or a place in the denominator.

The calculation covers one deterministic representative per sector in one current
browser session. It is a fragility diagnostic, not a registered robustness gate,
independent sample-out evidence, per-stock tuning result, experiment, promotion,
PAPER/LIVE state change or external-order capability.

## ADR-137 — Same-parameter batch claims require response identity binding

Status: accepted, 2026-08-27.

Freezing one form snapshot controls requests but does not by itself prove that
every received result belongs to that snapshot. Accepting a stale response with
another symbol, parameter set, dataset identity or loosened safety flag would
corrupt the session comparison and its cross-sectional denominator.

Before admission, the browser now compares the result with the exact normalized
request, the deterministic representative's stock/sector identity, and the signed
long-history data version/checksum. Classification and promotion must remain the
ad-hoc `NOT_ELIGIBLE` contract, while realtime readiness, real-data PAPER readiness,
state mutation and external-order transmission must all remain false. A mismatch
is counted as an identity rejection and is not retained or summarized.

This duplicates only the minimum client-side binding needed to make the visible
same-parameter claim honest. The signed API remains authoritative; the check does
not create a second engine, repair a response, retry it, persist evidence, register
or promote a strategy, change PAPER/LIVE state or add order capability.

## ADR-138 — Batch exports retain the attempted denominator without becoming evidence

Status: accepted, 2026-08-27.

A success-only result array cannot reconstruct which sector representatives were
attempted, whether each request used the same normalized parameters, or how many
requests failed or were rejected by identity checks. That makes an exported batch
less auditable than the page that produced it.

While batch status is active, the comparison export is therefore a bounded
browser-session diagnostic envelope. It carries the signed dataset identity, the
deterministic selection method and representative roster, every expected request,
all status counts, admitted results and the finite-metric cross-sectional summary.
Worst return and deepest drawdown identities remain explicit.

The envelope declares that refresh clears it, it creates no immutable evidence or
registry write, promotion is `NOT_ELIGIBLE`, and state mutation/external orders are
false. It is not uploaded or persisted and cannot be used as an experiment,
statistical robustness gate, PAPER/LIVE admission or order authorization.

## ADR-139 — Batch attempt ledgers retain stable outcomes, not rejected payloads

Status: accepted, 2026-08-27.

Aggregate failure counts preserve the denominator size but cannot identify which
representative request failed or distinguish transport failure from an otherwise
successful response that violated the frozen identity/safety contract.

Each cross-sector batch therefore retains an ordered browser-session attempt row
for every frozen request. The only outcomes are `COMPLETED`, `REQUEST_FAILED` and
`IDENTITY_REJECTED`. A completed row binds the admitted preview hash. Request
failures discard exception text; identity failures discard the rejected response;
both keep a null preview hash and remain outside results and metric denominators.

The ledger is shown and exported only with the active browser batch. It does not
retry, repair, persist or promote anything, does not create evidence or registry
state, and cannot start PAPER/LIVE or transmit an order.

## ADR-140 — Bounded within-sector diagnostics stop at two full responses per sector

Status: accepted, 2026-08-27.

One representative per sector can reveal cross-sector fragility but cannot show
whether the next deterministic stock in the same sector immediately contradicts
the result. Conversely, fetching and retaining all 75 full curve responses in one
browser action would create unnecessary public-function and client-memory load.

The browser therefore offers an explicit one- or two-stock-per-sector choice. It
sorts by sector code then symbol and takes the first N without per-stock tuning.
For the signed 15-sector universe, two means exactly 30 sequential requests. The
batch freezes N, expands the session cap to 30, paginates after 15 rows, and keeps
requested/completed stock counts separate from requested/completed sector counts.

This is a bounded within-sector fragility diagnostic, not full-universe evidence.
It does not replace the signed 75-stock report, retry failures, persist an
experiment, promote a strategy, start PAPER/LIVE or transmit an order.

## ADR-141 — Within-sector agreement requires two complete finite admitted responses

Status: accepted, 2026-08-27.

Running two deterministic stocks per sector makes within-sector disagreement
observable, but stock-level rows alone do not define a valid paired denominator.
Counting a sector after only one successful response would hide the missing
partner and overstate the evidence available for an agreement claim.

A sector enters the pair summary only when exactly two representatives were
requested, both responses passed the frozen identity and safety contract, and
both retained rows have finite return, excess return and drawdown metrics. The
browser then reports return-sign and excess-sign agreement, absolute return
spread, its median across complete pairs and the worst-divergence sector. Zero is
its own sign. Missing requests, identity rejections and non-finite metrics exclude
the entire pair without zero filling or copying another value.

The browser-session export retains every admitted pair and the requested versus
complete pair-sector counts. This is a bounded fragility diagnostic, not a
statistical robustness threshold, new sample-out evidence, registry record,
promotion decision, PAPER/LIVE state transition or external-order capability.

## ADR-142 — Complete within-sector pairs are inspectable without export-only evidence

Status: accepted, 2026-08-27.

An aggregate consistency rate and worst-divergence label are not enough to audit
all complete sectors. Keeping the remaining pair values only in a JSON download
forces an operator to leave the page before checking whether the summary matches
the admitted stock-level outcomes.

The page therefore renders one bounded row for every already admitted complete
pair, sorted by sector code. Each row shows the sector, both stock codes, both
overlay returns, both active excess returns, return/excess sign agreement and the
absolute return spread. It reads the same `pairs` objects used by the session
export; it does not issue another request, derive a second denominator or render
an incomplete pair.

The table is limited by the two-per-sector, fifteen-sector batch contract. It is
browser-session diagnostic visibility only and cannot tune a parameter, create
evidence or registry state, promote a strategy, start PAPER/LIVE or transmit an
external order.

## ADR-143 — A comparable stock pair requires the complete common metric surface

Status: accepted, 2026-08-27.

Total return and active excess alone are insufficient for the requested strategy
comparison. Two stocks can have similar terminal returns while carrying materially
different annualized performance, risk-adjusted return or drawdown. Admitting a
pair whose missing Sharpe or information ratio is rendered as unavailable would
also make its denominator broader than the visible evidence.

Each admitted batch row is therefore parsed for cumulative return, annualized
return, Sharpe, maximum drawdown, active excess return and information ratio. A
within-sector pair exists only when every metric is finite for both stocks. The
pair object and detail table retain both values for every metric; a non-finite
value excludes the whole pair without imputation, while the stock response remains
visible in the ordinary successful-session table.

These are response-bound diagnostics, not recalculated curves or a new research
gate. They do not create persistence, registry or promotion evidence, change
PAPER/LIVE state or permit external-order transmission.

## ADR-144 — Cross-sector medians share one complete common-metric denominator

Status: accepted, 2026-08-27.

A cross-sector batch that reports only median cumulative return still requires
the operator to inspect every row to understand typical annualized performance,
risk-adjusted return, drawdown and active performance. Mixing separate valid-row
sets per metric would make those headline values incomparable.

The batch summary therefore computes median cumulative return, annualized return,
Sharpe, maximum drawdown, active excess return and information ratio from the same
admitted rows for which all six values are finite. Exact values are retained in
the existing browser-session export. A missing or non-finite value excludes that
row from every median without imputation; failed and identity-rejected requests
remain outside the denominator as before.

These descriptive medians add no request, parameter choice or statistical gate.
They are not independent sample-out evidence and cannot create persistence,
registry or promotion state, start PAPER/LIVE or transmit an external order.

## ADR-145 — Repeated cross-sector tuning keeps bounded summaries, not winners

Status: accepted, 2026-08-27.

Replacing the previous cross-sector batch on every run prevents an operator from
checking whether a manual parameter change improved one headline while degrading
coverage, failure counts or another common metric. Retaining every full response,
however, would duplicate up to 30 curves per run and encourage unbounded browser
memory use.

The page therefore retains the newest five compact batch entries. Each binds its
dataset identity, one/two-stock selection mode, complete shared parameters,
stock/sector request and completion denominators, failure and identity-rejection
counts, the shared complete finite-metric summary and all admitted preview hashes.
The sixth entry evicts the oldest. Export preserves this order and identity; loading
an entry only refills the form and still requires a fresh server-validated run.

The table does not rank, highlight or select a winner. It is refresh-cleared
browser state without full duplicate curves, import or server synchronization.
It cannot become an immutable experiment, independent sample-out evidence,
registry or promotion state, PAPER/LIVE transition or external-order capability.

## ADR-146 — Local factor comparisons export identities, not decisions

Status: accepted, 2026-08-27.

The factor notebook already preserves the complete rule, effectiveness,
correlation, robustness and strategy-performance surface, but browser-local
storage alone makes cross-device review depend on manual transcription. Exporting
individual backtest responses also loses the comparison set and its common data
identity.

The page therefore exports every valid record bound to the current data version
and checksum in one deterministic JSON value. It includes provider/version,
evidence and PIT identity, full entry/exit parameters, factor definition, common
metrics, result hash and read-only strategy identity. Empty state disables the
action, and no export timestamp is added, so unchanged state serializes
identically.

The artifact is explicitly not an experiment or decision. It cannot be imported
or automatically synchronized, ranks no winner, writes neither FactorRegistry
nor StrategyRegistry, and provides no promotion, PAPER/LIVE, state-mutation or
external-order authority.

## ADR-147 — Export the multi-rule response without creating a second result

Status: accepted, 2026-08-27.

The browser displays the complete 2–8 rule effectiveness and correlation result,
but local factor summaries do not contain the submitted-family Holm adjustment,
continuous-score matrix or redundant-pair set. Reconstructing those fields from
screenshots or individual records would lose the exact response identity and
could silently change the statistical family.

After a successful request, the page therefore downloads the exact server JSON
under a filename derived from its `preview_hash`. It preserves each saved
threshold, session-clustered effectiveness row, Newey-West statistic, raw and
Holm-adjusted p-value, Pearson matrix, redundant pairs, data version/checksum,
PIT limitation and no-authority fields. Before a result exists the action is not
rendered; parameter or selected-rule changes invalidate the old result.

The browser does not wrap, rehash, recalculate or persist the response. Export
cannot select a winner, create an immutable experiment or registry record,
promote a strategy, start PAPER/LIVE, mutate state or transmit an order.

## ADR-148 — Discovery hands off an entry factor, not a tested strategy

Status: accepted, 2026-08-27.

The bounded discovery response identifies one training-locked opening rule, but
the editable strategy form previously required manual transcription. Copying the
whole discovery request or its test metrics would be worse: dates, cost, exit
behavior and already viewed test conclusions are not part of the selected entry
factor and must not silently become a strategy identity.

The handoff therefore copies exactly the selected three directions, fixed `ALL`
combination and body threshold into the custom opening-factor fields. It leaves
the current date window, cost and exit rule untouched, clears stale strategy and
multi-rule correlation responses, and performs no API request. The discovery
card remains visible and labels the transfer as diagnostic; an exit condition,
manual review and a new explicit run are still required.

This operation copies no test conclusion and does not create or select another
candidate, persist an experiment, write FactorRegistry/StrategyRegistry, promote
a strategy, start PAPER/LIVE, mutate state or transmit an order.

## ADR-149 — Opening-factor drafts precede strategy construction

Status: accepted, 2026-08-27.

The multi-rule effectiveness/correlation diagnostic previously selected rules
from locally saved full strategy backtests. That made exit choice and causal
backtest an accidental prerequisite for factor screening, reversing the intended
research order and mixing a pure opening-factor identity with strategy fields.

The browser now stores a separate, dataset-bound opening-factor draft containing
only three directions, `ALL`/`ANY` and a normalized body threshold. Exact rule
duplicates replace their earlier draft and the library is bounded at 30. Two to
eight selected drafts feed the existing server diagnostic; dates come from the
current analysis window, while exit, cost and all strategy metrics are absent.
Completed strategy results remain in their own local comparison table.

Every draft is explicitly `NOT_RUN` and not evidence. Local save/load/delete
does not call the API; the diagnostic remains stateless and read-only. Neither
path writes FactorRegistry/StrategyRegistry, promotes a rule, starts PAPER/LIVE,
mutates runtime state or transmits an order.

## ADR-150 — A tested opening factor needs an identity-bound manual handoff

Status: accepted, 2026-08-27.

A displayed effectiveness/correlation row is insufficient to seed a strategy:
the response could belong to another data version, window or ordered candidate
family, and a statistically effective rule can still be redundant with another
selected factor. Copying by visible label would break the evidence lineage.

Before rendering any strategy-construction action, the browser therefore checks
the exact data version/checksum, requested dates and ordered rules, deterministic
rule/effectiveness/matrix IDs, complete correlation pair set, response hash and
all read-only safety fields. It retains a browser-memory binding from each
verified `Rxx` identity to the exact selected local draft. Any mismatch rejects
the whole response.

An action appears only for a rule whose effectiveness gate passed and whose ID
is absent from every redundant pair. All eligible rules remain visible, so the
UI does not choose a winner. A click copies only directions, combination and
threshold into the custom opening form; date, cost and exit remain unchanged and
the response stays visible. It performs no backtest, registry write, promotion,
PAPER/LIVE start, state mutation or order transmission.

## ADR-151 — Reject a strategy response before rendering unbound metrics

Status: accepted, 2026-08-27.

The factor workbench previously trusted a typed strategy-backtest response for
immediate display and performed only a subset of identity checks when the user
later saved a local summary. A response from another request or data checksum,
or one with relaxed PAPER safety fields, could therefore contaminate the visible
metrics and curves before persistence rejected it.

The browser now admits a response only when it reproduces the normalized request
and verified dataset scope; binds the opening and exit behavior, next-bar and
T+1 execution, strategy ID/spec hash and preregistration identity; and retains
blocked registration, PAPER/LIVE, state and order fields throughout the nested
workflow. Missing nested fields and any mismatch fail closed to one canonical
error before result rendering. A valid response displays its binding explicitly.

This is a client boundary check, not a second backtest or cryptographic
recalculation. It creates no experiment or registry record, does not promote or
start PAPER/LIVE, and cannot mutate state or transmit an order.

## ADR-152 — A manual factor handoff retains one bounded strategy lineage package

Status: accepted, 2026-08-27.

The effectiveness/correlation response and strategy backtest could each be
exported, but a manual handoff did not leave one portable identity proving which
effective, non-redundant factor seeded a particular strategy preview. Simply
placing both JSON objects beside each other would also risk implying that the
factor diagnostic validated the later exit rule or strategy profitability.

After an identity-bound manual handoff, the browser therefore retains the exact
local candidate, `Rxx` identity, passing Holm-adjusted effectiveness row,
non-redundant decision, correlation hash and same-window data identity. Only a
subsequent FE-154-admitted response with the unchanged opening rule and window
can produce the deterministic joint export; it additionally binds the complete
strategy request, result hash and read-only behavior hash. Any form, selected
candidate or evidence change clears the handoff and package. A direct backtest
remains a valid diagnostic but is visibly unbound to a preceding factor screen.

The package explicitly records that the exit rule and strategy returns were not
validated by factor correlation and that reuse of one viewed historical window
is not independent evidence. It is browser-session state, not immutable
persistence, FactorRegistry/StrategyRegistry evidence, promotion authority,
PAPER/LIVE admission, state mutation or external-order capability.

## ADR-153 — Correlation admission validates scope and arithmetic, not typed shape

Status: accepted, 2026-08-27.

Matching a data checksum, ordered rule IDs and a complete pair-key set does not
prove that a factor-correlation response describes the requested experiment. A
response can retain those identifiers while shrinking the stock universe,
changing the trading-day grid, reporting a non-Holm p-value adjustment or using
an asymmetric matrix whose pair summary hides the conflicting cell.

Before exposing a factor-to-strategy handoff, the browser now derives the exact
selected sessions and next exit-completion session from the verified dataset.
Those identities determine the required 75-stock observation count and native
5-minute source-Bar count. It checks the causal score contract, fixed
effectiveness gates and submitted-family Holm-Bonferroni adjustment. It also
requires a finite correlation matrix in `[-1, 1]`, exact rule keys, symmetry and
unit diagonal, then derives every pair's redundancy flag, maximum absolute
correlation and overall result from the returned matrix and fixed threshold.

This is an independent client admission boundary over an already computed
read-only response; it does not rerun factor returns or replace the signed server
preview hash. Rejection creates no fallback result. Acceptance still creates no
immutable evidence, registry or promotion state, PAPER/LIVE transition, runtime
mutation or external-order capability.

## ADR-154 — Unavailable L2 readiness is an exact transport contract

Status: accepted, 2026-08-27.

The six-gate L2 boundary is safety-relevant even while every gate is false. A
typed browser response can still drift at runtime: it may omit one depth level,
drop the event sequence, remove a rejection rule or report a shorter gate family
while retaining the top-level `UNCONFIGURED` and no-order flags. Rendering that
payload would make an incomplete contract look equivalent to the reviewed one.

The browser therefore admits the current unavailable L2 payload only when it
exactly matches the reviewed formula and untested hypothesis, twenty ordered
five-level price/size fields, five identity fields, share units, continuous-
auction scope, all invalid-book rejection policies and the finalized-minute
causal clock. The six ordered gate identities and reasons must all remain false,
and their reported counts must equal the actual gate family. Missing and extra
fields both reject the complete strategy response; so do future-book permission
or any claim that OHLCV supplied depth.

This intentionally fails closed when a future provider integration changes the
contract: provider authorization, schema/unit/clock/freshness certification and
recorded replay require a reviewed type and validator update together. The
current implementation does not fetch or synthesize L2, calculate an intensity,
test a factor, define an exit, start PAPER/LIVE, mutate state or transmit an
external order.

## ADR-155 — PAPER admission is a dynamic exact transport contract

Status: accepted, 2026-08-27.

An eight-row PAPER dossier is not trustworthy merely because its array length is
eight and several obvious runtime gates are false. A response could report a
forged passed count, repeat a false gate to hide the research or backtest gate,
or detach its evidence from the dataset and ad-hoc strategy while retaining the
top-level `BLOCKED` decision. Rendering the surrounding backtest would make that
drift look like a reviewed promotion boundary.

The browser now derives the expected dossier from the verified data version and
checksum, actual research and causal-backtest results, read-only strategy ID and
spec hash, and sequential next action. It requires the exact ordered eight gates
and evidence strings, `1 + research + backtest` passed-count arithmetic, a fixed
required count of eight, and every automatic-start/runtime-mode/state/order flag
to remain false. Recursive exact matching rejects missing and extra fields as
well as duplicate gates, count or evidence drift before any metric renders.

This is a response-admission boundary, not a promotion evaluator. It neither
changes the server's research result nor creates registration, unseen-forward,
realtime, attestation or reconciliation evidence. PAPER remains blocked and LIVE
and external order transmission remain disabled.

## ADR-156 — Strategy registration preflight is an exact dynamic contract

Status: accepted, 2026-08-27.

Checking only that registration is `BLOCKED`, the ad-hoc strategy identity
matches and write flags are false does not validate a registry preflight. A
payload could forge `8/9`, duplicate a passing gate, claim all fifteen fields are
available or permit `IDEA → EXPERIMENT` while retaining those shallow fields.
The surrounding backtest would then render an unreviewed registry boundary.

The browser now derives ordered-research and causal-backtest gate values from
the response's factor, correlation, robustness and backtest facts. It requires
the exact 15-field `StrategySpec` inventory, six available and nine missing
fields, all nine gate IDs and evidence strings, `2 + all_research + backtest`
passed-count arithmetic, the fixed three-item transition evidence and every
registration/runtime/state/order prohibition. Recursive exact matching rejects
missing, duplicate and extra fields as well as evidence or authority drift.

This remains transport admission for an ad-hoc historical preview. It does not
compute a complete registry specification, attach preregistration or leakage
review, freeze a disjoint split, declare lineage, insert an `IDEA`, start PAPER
or LIVE, mutate runtime state or transmit an external order.

## ADR-157 — Sequential advancement is an exact derived contract

Status: accepted, 2026-08-27.

Checking only the final no-registration, no-state and no-order flags does not
validate the research sequence that users rely on. A payload could forge a pass
count, reorder the three prerequisites, skip the earliest failed stage or offer
the registry action while preserving those shallow prohibitions.

The browser now reconstructs the exact ordered factor-validity, correlation and
rolling-robustness rows from their separately validated booleans. It derives the
pass count, all-passed state, earliest blocker, diagnostic/admitted construction
status, causal-backtest branch and next action, then recursively compares the
complete object. Missing, reordered or extra fields, non-boolean drift,
inconsistent arithmetic and any registration/state/order authority reject the
whole backtest response before its metrics render.

This is transport admission only. It does not advance a stage, register a
strategy, reuse the viewed window, authorize PAPER/LIVE, mutate state or
transmit an external order.

## ADR-158 — Next-hypothesis preregistration is an exact transport contract

Status: accepted, 2026-08-27.

Binding only the strategy, dataset, viewed dates and final no-write flags does
not validate the preregistration checklist. A payload could omit a governance
gate, weaken its requirement, permit reuse of an already viewed test window or
claim an `IDEA` lifecycle state while leaving those shallow bindings intact.

The browser now derives the preregistration status from sequential advancement
and requires the exact current-result binding, ordered seven hypothesis fields,
thirteen experiment locks, eight governance gates and requirement strings,
disjoint evidence-window policy, eight-state legal lifecycle and mandatory
human authorization for `APPROVED` and `CHAMPION`. Recursive exact matching
rejects missing, reordered and extra fields, evidence reuse, premature lifecycle
state and any preregistration/registry/runtime/state/order authority.

This remains read-only response admission. It creates neither a hypothesis nor
an experiment or registry row, does not authorize PAPER/LIVE, does not mutate
state and cannot transmit an external order.

## ADR-159 — The visible research workflow is an exact derived contract

Status: accepted, 2026-08-27.

Finding only a `PAPER=NOT_ELIGIBLE` row and a `LIVE=DISABLED` row does not
validate the workflow shown above them. A payload could mark factor validation
as passed, reorder prerequisites or claim research-admitted strategy
construction while retaining those two final blockers.

The browser now reconstructs the exact eight ordered stages from independently
validated factor, correlation, robustness and causal-backtest booleans. The
factor-validation explanation also depends on whether triggered conditional-
exit timing was evaluated, and strategy construction is research-admitted only
when all three prerequisites pass. Every reviewed detail string and the fixed
PAPER/LIVE blockers must match. Recursive exact matching rejects missing,
reordered, duplicate and extra stages, forged status, text drift and non-boolean
context.

This check changes no research conclusion and adds no workflow transition,
registry operation, PAPER/LIVE runtime, state mutation or external order path.

## ADR-160 — Strategy behavior identity is reconstructed and rehashed in the browser

Status: accepted, 2026-08-27.

A hash-shaped server field and an ID derived from its prefix do not establish
that the displayed strategy matches either the submitted request or the stated
hash scope. Strategy behavior and a matching invented digest could previously
drift together while downstream read-only admission records remained internally
consistent with those invented values.

The browser now reconstructs the exact entry factor, conditional or time exit,
causal price sources, position rule, benchmark, cost and T+1 behavior from the
normalized request. It binds observed conditional-exit and fallback counts to
the response while excluding only those two outcome counts from the immutable
behavior specification. The specification is serialized with the same recursive
key ordering and compact JSON semantics as the service, hashed with Web Crypto
SHA-256, and compared with the exact scope, digest, derived ID and read-only
identity object. Extra, missing or drifted behavior, factor metadata, outcome
counts, hash scope, digest or authority fields reject the complete response.

This is independent response verification, not a signature or registry write.
It does not change the backtest engine, create a strategy version, advance a
research gate, authorize PAPER/LIVE, mutate runtime state or transmit an order.

## ADR-161 — Intraday backtest clocks are exact branch-derived contracts

Status: accepted, 2026-08-27.

Two causal booleans cannot prove when a factor became knowable or which later
provider Bar supplied an execution price. A response could preserve
`same_bar_execution=false` and `t_plus_one_compliant=true` while moving entry to
the signal Bar, shifting the interval label, or mixing the fixed-open and
conditional-exit branches.

The browser now derives the complete causal clock from the normalized exit rule.
Both branches require the third ten-minute Bar to close at 10:00 before entry at
the next provider five-minute open identified by label 10:05 and interval start
10:00. The fixed branch requires the T+1 first-Bar open identified by label 09:35
and interval start 09:30. The conditional branch requires its third T+1
ten-minute Bar to close at 10:00, execution at the next 10:05-labelled Bar, and
fallback at the 15:00-labelled Bar whose interval starts at 14:55. Exact recursive
matching rejects branch mixing, changed clocks or labels, and missing or extra
fields before metrics render.

This verifies response timing only. It does not recalculate returns, alter the
backtest engine, register or advance a strategy, authorize PAPER/LIVE, mutate
runtime state or transmit an external order.

## ADR-162 — Continuous collection is isolated research observation

Status: accepted, 2026-10-25.

Run one supervised, bounded collector on the inspected cluster. Reuse existing
public-source adapters and canonical validation rather than adding gateways.
Publish immutable Parquet plus checksum-bound reports and retain the previous
success after a failed attempt. Distinguish process heartbeat, collection
success and exchange-calendar-aware source freshness. Calendar expiry, future
clocks, incomplete coverage or invalid checksums fail closed.

Use the persistent NFS mount only for research archives and JSON; no SQLite WAL
operational ledger is deployed there. The worker lock remains container-local,
so multi-container writers are unsupported. Bind observation HTTP to loopback.
Do not modify accepted historical experiments, count backfill as forward
PAPER evidence, or change live/external-order authority. A public Vercel project
cannot directly reach the private SSH address; web publication is a separate
read-only integration with explicit destination and network configuration.

## ADR-163 — Public research publication is a bounded data-only channel

Status: accepted, 2026-10-25.

Use a dedicated public observation repository for small JSON snapshots, with a
write deploy key scoped only to that repository. The generated private key stays
on the cluster with restricted permissions. Vercel fetches one fixed HTTPS
destination without credentials; it never executes code from the data repository.
Bulk Parquet, full experiment artifacts and active catalogs remain on the cluster.
This small-volume bootstrap can later move to object storage without moving
research computation into Vercel. GitHub/CDN availability and delay are explicit
transport limits, not realtime authority.

Snapshots bind source reports and latest bars. Request-time checks re-evaluate
market clocks, calendar coverage, worker state at publication and a 20-minute
publication expiry. Monitoring responses are never edge-cached. Missing, invalid
or stale observations never silently replace accepted backtest evidence.

Formal research runs reuse ExperimentRunner, SQLiteMetadataCatalog and canonical
backtest artifacts. A full run specification, dataset manifest, result and file
checksums are persisted before publication. Active SQLite is container-local;
verified immutable backups and artifacts reside on persistent storage. Legacy
archives preserve their original identities without claiming missing run specs
were independently reconstructed. Historical continuation runs remain post hoc,
non-PIT and promotion-ineligible. There is no anonymous experiment write API,
automatic strategy promotion, PAPER start or external order transmission.


## ADR-164 — Current research inputs are versioned independently of frozen evidence

Status: accepted, 2026-10-25.

Reuse the existing Sina/BaoStock readers, Bar quality gates, Parquet schema and
causal backtest/spec helpers. No gateway or new trading path is needed; vn.py
execution components do not address the stale dataset/publication gap. Current
charts and parameterized previews use a fixed read-only content-hash channel,
with request version binding, bounded downloads and publication expiry. Dated
experiments and reports retain their original evidence and are shown as archives.
Intraday observations and complete-session EOD backtests have separate clocks.

Expansion uses a current liquidity screen (explicit non-PIT bias), per-symbol
checkpoints on persistent storage, isolated bounded vendor processes and a 100 GiB
free-space reserve. A configured target is never a completed coverage claim.
Full research history stays on the cluster; only bounded web inputs enter the
public data repository. BaoStock qfq history is rebuilt consistently, never
spliced with uncertified Sina adjustment semantics. LIVE remains disabled.
## ADR-165 — Authenticated research submission uses a private outbound queue

Status: accepted, 2026-10-26.

The user selected direct submission after login on the existing Vercel website.
Use an independent research account and signed HttpOnly/Secure/SameSite cookie;
never reuse broker, SSH or trading-control credentials. Store only password
hashes and repository-scoped queue credentials in server-side environment values.
The new private QTrade-research-queue repository carries bounded signed JSON
requests and cancellation intents. Pin GitHub's SSH host key; non-force pushes
provide append conflict detection. The cluster pulls outbound and executes one
allowlisted model subprocess at a time, with a local SQLite claim catalog, lease,
timeout, process-identity recovery and explicit interrupted states. Public
observation publication continues to carry bounded data only. This bootstrap
has GitHub/CDN availability and roughly five-minute status latency; it is not
a realtime job service and must eventually be replaced if submission volume grows.

New Ridge/LightGBM experiments share Alpha158 train-fitted preprocessing,
supported-universe admission, TopkDropout and explicit cost/split contracts.
The original fixed baseline remains immutable under its different protocol.
Only equal inputs, source, splits, seed, portfolio and costs may be attributed
to model differences in the UI. Vendor status backfill is observed now; do not
invent historical available_at timestamps or certify full PIT. Freeze source
repair coverage into each new archive; unknown exact trading limits remain
explicit diagnostic constraints. LIVE_TRADING_ENABLED=false.

## ADR-166 — Source retry status is separate from accepted PIT coverage

Status: accepted, 2026-10-26.

A supervised process being alive does not establish a successful source pass.
Keep a signed attempt record independently of the last accepted coverage. Reuse
only checksum-verified master/status evidence, preserve its original observed
time, and disclose stale-master fallback after a failed refresh. No fallback
creates historical availability or trading-rule certification. Failed or
interrupted passes retain their existing scope and retry after bounded backoff;
only fully complete source passes advance the bounded sample after six hours.
A zero process exit with PARTIAL source evidence retries the same scope with
backoff and is never recorded as a successful coverage pass. The parent reaps
its collector on shutdown or timeout so the shared vendor session is released.

The first production login acceptance also exposed a stale uv.lock: the frozen
deployment lacked declared queue transport dependencies. Keep declared runtime
dependencies and the deployment lock aligned, with a regression check. Queue
failure logs contain the operation and exception class only, never credential
values, exception messages or tracebacks.

## ADR-167 — New Qlib runs bind raw source evidence and only add diagnostic blocks

Status: accepted, 2026-10-26.

Coverage counts alone cannot reproduce a run or justify execution-state claims.
Freeze the exact verified master/status inputs and bind their hash into the
request/run identity. Publish a pinned source ID with model defaults; preserve
that ID through browser retry, queueing and execution. Different source versions
cannot be attributed to model differences. Archive raw sources and request proof.

Reuse Qlib's directional limit fields and Exchange implementation. Apply only
additional diagnostic restrictions after raw price/volume/amount reconciliation:
block both directions on contradictory sources; exclude reconciled ST buys as
an explicit research policy. Never relax prior proxy limits, fill missing state
as normal trading or claim backfills were historically available. Preserve old
protocols and archives; new runs use a distinct source-bound comparison protocol.
Full historical PIT, exact limits, corporate actions, settlement and auctions
remain separate uncertified P0 work. No gateway or vn.py execution component
supplies this missing source lineage. LIVE_TRADING_ENABLED=false.

