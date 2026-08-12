# Architecture

## Runtime pipeline

```text
Market Data → Strategy/Alpha → Portfolio Construction → Target Positions
  → Risk → OMS → Execution → Gateway → Trades
  → Accounting → Reconciliation → Monitoring/Audit
```

Backtest, paper, shadow, and any future live adapter share domain contracts.
Only gateways vary. `PaperGateway` is the only executable gateway initially;
shadow mode also routes to simulation and never transmits an external order.

## Layers

- `domain`: immutable identifiers, instruments, orders, trades, accounts, modes.
- `risk`: pre-trade validation, operational state, limits, kill switch.
- `oms`: auditable order state machine and idempotency registry.
- `execution`: broker protocol and deterministic paper gateway.
- `accounting`: cash, positions, fees, realized/unrealized PnL.
- `data`: versioned datasets, point-in-time records, quality gates.
- `research`: factors, experiments, time-ordered evaluation.
- `portfolio`: signal-to-target conversion and constraints.
- `backtest`: event-driven orchestration using the execution contracts.
- `api`/`monitoring`: control plane, health, metrics, audit.

## Dependency rule

Domain and safety modules have no web/database/framework dependency. Adapters
depend inward on protocols. vn.py integration is an adapter, not a fork.

## Persistence plan

Parquet plus DuckDB is the local analytical path. PostgreSQL is planned for
metadata and runtime state when an available supported environment exists.
Append-only JSONL is acceptable for the bootstrap audit/event journal.

