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
