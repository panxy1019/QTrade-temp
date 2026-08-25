# Market data provider report

Source snapshot: 2026-08-25. XT findings remain a static official API audit.
The native RQData SDK surface was verified locally with `rqdatac 3.6.3`, without
credentials or an authenticated market-data request.
AKShare 1.18.84 was installed in an isolated environment. Bounded Eastmoney-backed
ETF snapshot/history probes failed before data receipt; the independent Sina ETF
daily-history endpoint succeeded and produced a quality-passed canonical dataset.

## Recommendation

AKShare is the first credential-free bootstrap provider for noncommercial research
and low-frequency `REAL_DATA_PAPER`. It is explicitly not production-grade.
Historical research is `AKSHARE_HISTORICAL_READY`; snapshot-driven PAPER is
`WAITING_FOR_AKSHARE_SNAPSHOT_ENDPOINT_ACCESS`. RQData remains the
conditional authoritative-provider choice when the user supplies a valid
commercial entitlement. XT is the QMT-entitled fallback.

The repository now provides both `RqDataProvider` and `XtDataProvider` over the
same injected, data-only bridge protocol, plus a native optional RQData transport.
Run the credential-free diagnostics:

```powershell
python scripts/provider_readiness.py AKSHARE
python scripts/akshare_probe.py --universe-size 30
python scripts/provider_readiness.py RQDATA
python scripts/probe_rqdata_readonly.py
python scripts/provider_readiness.py XTDATA
```

Minimum user action for RQData: obtain licensed realtime A-share/ETF/index access,
then set `QTRADE_RQDATA_USERNAME` and `QTRADE_RQDATA_TOKEN` in the isolated bridge
process environment, install `.[rqdata]`, then run
`python scripts/probe_rqdata_readonly.py --connect --symbol 510300`. Minimum user
action for XT: install an entitled QMT/XT client,
set `QTRADE_XTDATA_PATH`, and set
`QTRADE_XTDATA_ENTITLEMENT_CONFIRMED=true` only after verifying the entitlement.
The diagnostic prints variable names and actions only, never values.

AKShare requires `QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true` after the operator reviews
its official data-use statement. It does not require credentials. See
`docs/AKSHARE_READINESS.md` for the exact implemented scope and failed endpoint
probe evidence.

| Capability | AKShare 1.18.84 | rqdatac 3.6.3 native bridge | vnpy_xt 1.4.6 |
|---|---|---|---|
| Adapter license/access | MIT software; official statement limits data use to academic research | MIT; RQData service is commercial/proprietary | MIT; XT/QMT access is proprietary/broker-entitled |
| Realtime | Delayed/public-web polling snapshots; no SLA | L1 Tick and 1-minute channels | Tick subscription |
| History | ETF daily and recent 1/5/15/30/60m in first slice | Implemented raw 1m and daily; vendor also offers Tick/other frequencies | Tick, 1m, daily in adapter; more in vendor API |
| ETF/index | ETF daily/minute/snapshot adapter; A-share and major-index raw daily historical adapters accepted | Supported, subject to entitlement | Supported, including ETF auxiliary data |
| Corporate actions/PIT | Not implemented; current-universe selection has survivorship bias | Vendor supports data; adapter does not expose complete PIT entities | Vendor has factors/sectors; adapter does not expose complete PIT entities |
| Adjustment caveat | RAW only; recent minute coverage is limited | Bridge forces `adjust_type=none` | Adapter requests `front_ratio` |
| Python/Windows | Pure Python 3.9-3.14; isolated runtime recommended | Installed CPython 3.12 Apple Silicon wheel; isolated runtime recommended | Pure Python adapter, local QMT/XT service; isolate operationally |
| Reconnect/heartbeat | Polling cache/health/circuit only; no stream reconnect | Snapshot round-trip heartbeat; SDK reconnect is not observable enough for production continuity | Vendor has reconnect primitives; standard adapter lacks full supervision |
| Public SLA/price | No SLA; credential-free; upstream interfaces unstable | Not established; commercial quotation required | Not established; broker/vendor entitlement required |

## Required QTrade contract

- Vendor imports and credentials stay outside Core.
- Capability handshake includes provider/SDK/schema versions and entitled features.
- `external_order_transmission=false` and `account_access=false` are mandatory.
- RAW history is the default. Adjusted history must carry adjustment method and
  factor version; unavailable RAW data fails the PIT research gate.
- Every tick carries exchange, receive and ingestion timestamps, reconnect epoch,
  provider sequence where available, QTrade ingest ordinal and payload hash.
- Reconnect enters a gap state, rebuilds the desired-subscription manifest,
  snapshots/backfills, and only exits after reconciliation.
- Real-time bars are built from canonical ticks and close only at an explicit
  watermark. A signal emitted at bar N close can execute only on a later observable
  quote/tick.

Current implementation provides provider-neutral protocols, injected RQData and XtData
bridge adapters, a native lazy-loaded RQData transport, cross-restart quality
cursors, hash-chained raw recording, atomic Parquet/manifests, finalized bars,
and deterministic ingestion-order replay. It does not claim an authenticated
real-provider connection.

## Primary sources

- [vnpy_rqdata](https://github.com/vnpy/vnpy_rqdata)
- [vnpy_rqdata PyPI](https://pypi.org/project/vnpy-rqdata/)
- [vnpy_xt](https://github.com/vnpy/vnpy_xt)
- [vnpy_xt PyPI](https://pypi.org/project/vnpy-xt/)
- [XT native data API](https://dict.thinktrader.net/nativeApi/xtdata.html)
- [RQData Python API](https://www.ricequant.com/doc/rqdata/python/index-rqdatac)
- [RQData realtime market data](https://www.ricequant.com/doc/rqdata/python/generic-api)
- [RQData solution overview](https://assets.ricequant.com/welcome/%E7%B1%B3%E7%AD%90RQData%E9%87%91%E8%9E%8D%E6%95%B0%E6%8D%AE%E8%A7%A3%E5%86%B3%E6%96%B9%E6%A1%88%E7%AE%80%E4%BB%8B.a528f1bd.pdf)
`RqDataProvider` remains a protocol adapter over an injected bridge. The optional
`NativeRqDataBridgeClient` now supplies the in-process SDK transport, but its
connection becomes CONNECTED only after a session-bound, data-only capability
handshake passes. Authenticated entitlement validation remains external work.
