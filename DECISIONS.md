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
