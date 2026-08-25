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
