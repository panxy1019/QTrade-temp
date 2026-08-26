# Architecture decisions

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
