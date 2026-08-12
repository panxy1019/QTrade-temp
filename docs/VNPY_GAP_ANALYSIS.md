# vn.py source and reuse gap analysis

Verified: 2026-08-12. Versions are research pins, not installed dependencies.

## Reuse decision

Use vn.py 4.4.0 core/event engine, `MainEngine`, `BaseGateway`, and standard data
objects through an adapter. Selectively reuse `vnpy_portfoliostrategy` 1.3.0,
`vnpy_algotrading` 1.1.0, `vnpy.alpha`, and database adapters after compatibility
tests. Keep Q's stricter application risk/OMS/idempotency/accounting boundary.

Do not use `vnpy_paperaccount` 1.0.6 as the safety-critical paper ledger. Source
review found that it reroutes `MainEngine`, mutates gateway naming, tracks
positions without complete cash/fee accounting, and fills at level one without
the partial-fill/retry semantics required by this project.

Do not rely on PortfolioStrategy's backtester for production accounting. Adapt
strategies/data contracts but verify cost, order-state, and ledger behavior in
Q's shared pipeline.

## Compatibility matrix

| Component | Research pin | Intended use | Key constraint |
|---|---:|---|---|
| vnpy | 4.4.0 | event/core/gateway/data contracts | Python >=3.10; adapter test required |
| vnpy_portfoliostrategy | 1.3.0 | portfolio strategy reuse | vnpy >=4.4.0 |
| vnpy_paperaccount | 1.0.6 | source reference only | insufficient accounting/fills |
| vnpy_algotrading | 1.1.0 | execution algorithms | vnpy >=4.0.0 |
| vnpy_riskmanager | 2.0.0 | optional defense-in-depth | native/Cython; not sole risk gate |
| vnpy_sqlite | 1.1.3 | local metadata option | peewee >=3.17.9 |
| vnpy_postgresql | 1.1.2 | durable state candidate | needs PostgreSQL environment |
| vnpy_xtp | 2.2.32.2.3 | future broker adapter | available Windows wheel ABI limits |
| vnpy_tora | 2026.8.2 | future broker adapter | available wheel currently CPython 3.10 |
| vnpy_ost | 2.0.1.3 | not selected | stale compatibility evidence |

The current Python 3.14 environment is unsuitable for broker wheels observed in
the audit. Use a separate pinned Python 3.10/3.13 environment based on the
chosen gateway; do not downgrade or mutate the user's global Python implicitly.

## Broker boundary

XTP or TORA selection requires the user's broker choice, appropriate account
and certification environment, and explicit later authorization. No gateway is
connected by the current project; real order transmission remains unavailable.

## Primary sources

- <https://github.com/vnpy/vnpy/releases/tag/4.4.0>
- <https://github.com/vnpy/vnpy_portfoliostrategy>
- <https://github.com/vnpy/vnpy_paperaccount>
- <https://github.com/vnpy/vnpy_algotrading>
- <https://github.com/vnpy/vnpy_riskmanager>
- <https://github.com/vnpy/vnpy_xtp>
- <https://github.com/vnpy/vnpy_tora>

