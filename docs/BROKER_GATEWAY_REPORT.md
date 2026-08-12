# Broker gateway compatibility report

Source snapshot: 2026-08-12. Static package/source inspection only. No broker was
selected, no account or SDK session was opened, and no real order/cancel path exists.

## Decision

QTrade remains `WAITING_FOR_BROKER_SELECTION`. If the user's broker supports TORA,
it is the best-maintained candidate; isolate it in CPython 3.10. If XTP is supported,
isolate it in CPython 3.13. OST and EMT are legacy HOLD candidates requiring a full
vn.py 4, ABI, recovery and security certification.

| Gateway | Package snapshot | ABI | Maintenance | Read-only query gap |
|---|---|---|---|---|
| XTP | vnpy_xtp 2.2.32.2.3 | Windows CPython 3.13 wheel | vn.py 4 tested | Native paged order/trade queries exist but standard Gateway does not expose them |
| TORA | vnpy_tora 2026.8.2 | Windows CPython 3.10 wheel | Best current maintenance; vn.py 4 tested | Native order/trade queries exist but standard Gateway does not expose them |
| OST | vnpy_ost 2.0.1.3 (2022) | CPython 3.10 | Legacy/unverified for vn.py 4 | Custom audited query adapter required |
| EMT | vnpy_emt 2.10.0 (2022) | Windows CPython 3.10 | Legacy/unverified for vn.py 4 | Custom audited paged query adapter required |

All four are trading gateways with realtime quote callbacks, not historical
research-data providers. Public package sources do not establish a universal
sandbox, latency SLA, fee completeness, SDK redistribution permission or broker
entitlement.

## Read-only architecture

```text
QTrade Core (Python 3.14)
        |
authenticated local read-only IPC
        |
broker-specific bridge process/venv
        |
native query API only
```

The canonical protocol contains `BrokerCapabilities`, `BrokerStatus`,
`BrokerAccount`, `BrokerPosition`, `BrokerOrder`, and `BrokerTrade`. Its operation
allowlist is connect, disconnect, heartbeat, query accounts, positions, orders and
trades. There is deliberately no send, cancel, amend, or execution command in the
schema, client, server, imports or tests. Capabilities fail closed if transmission
or cancellation is advertised.

Recovery moves through disconnected, authenticated, recovering and
read-only-ready states. Complete paged snapshots must reconcile pushes received
during recovery. Native IDs, exchange IDs, client references and QTrade internal
IDs remain separate. Unknown fees remain null rather than being reported as zero.

## Primary sources

- [vnpy_xtp](https://github.com/vnpy/vnpy_xtp)
- [vnpy_tora](https://github.com/vnpy/vnpy_tora)
- [vnpy_ost](https://github.com/vnpy/vnpy_ost)
- [vnpy_emt](https://github.com/vnpy/vnpy_emt)
- [vn.py gateway compatibility](https://github.com/vnpy/vnpy)
