# Market data provider report

Source snapshot: 2026-08-12. This report is a static package/source audit. No
credential, token, QMT installation, vendor session, or licensed feed was used.

## Recommendation

QTrade remains `WAITING_FOR_DATA_PROVIDER_AUTH`. The conditional first choice is
RQData when the user supplies a valid commercial entitlement because its vn.py
adapter is data-only. XT is the fallback where the user already has QMT/XT data
rights, but it must run in a hard data-only subprocess and must never instantiate
its trading API.

The repository now provides both `RqDataProvider` and `XtDataProvider` over the
same injected, data-only bridge protocol. Run the credential-free diagnostic:

```powershell
python scripts/provider_readiness.py RQDATA
python scripts/provider_readiness.py XTDATA
```

Minimum user action for RQData: obtain licensed realtime A-share/ETF/index access,
then set `QTRADE_RQDATA_USERNAME` and `QTRADE_RQDATA_TOKEN` in the isolated bridge
process environment. Minimum user action for XT: install an entitled QMT/XT client,
set `QTRADE_XTDATA_PATH`, and set
`QTRADE_XTDATA_ENTITLEMENT_CONFIRMED=true` only after verifying the entitlement.
The diagnostic prints variable names and actions only, never values.

| Capability | vnpy_rqdata 3.4.7.8 | vnpy_xt 1.4.6 |
|---|---|---|
| Adapter license | MIT; RQData service is commercial/proprietary | MIT; XT/QMT access is proprietary/broker-entitled |
| Realtime | Tick channels | Tick subscription |
| History | Tick, 1m, 60m, daily | Tick, 1m, daily in adapter; more in vendor API |
| ETF/index | Supported, subject to entitlement | Supported, including ETF auxiliary data |
| Corporate actions/PIT | Vendor supports data; adapter does not expose complete PIT entities | Vendor has factors/sectors; adapter does not expose complete PIT entities |
| Adjustment caveat | Adapter requests `pre_volume` | Adapter requests `front_ratio` |
| Python/Windows | rqdatac 3.6.2 has CPython 3.14 Windows wheels; adapter metadata ends at 3.13 | Pure Python adapter, local QMT/XT service; isolate operationally |
| Reconnect/heartbeat | No complete observable contract in standard adapter | Vendor has reconnect primitives; standard adapter lacks full supervision |
| Public SLA/price | Not established; commercial quotation required | Not established; broker/vendor entitlement required |

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
bridge adapter, cross-restart quality cursors, hash-chained raw recording,
atomic Parquet/manifests, finalized bars, and deterministic ingestion-order replay.
It does not claim an authenticated real-provider connection.

## Primary sources

- [vnpy_rqdata](https://github.com/vnpy/vnpy_rqdata)
- [vnpy_rqdata PyPI](https://pypi.org/project/vnpy-rqdata/)
- [vnpy_xt](https://github.com/vnpy/vnpy_xt)
- [vnpy_xt PyPI](https://pypi.org/project/vnpy-xt/)
- [XT native data API](https://dict.thinktrader.net/nativeApi/xtdata.html)
- [RQData solution overview](https://assets.ricequant.com/welcome/%E7%B1%B3%E7%AD%90RQData%E9%87%91%E8%9E%8D%E6%95%B0%E6%8D%AE%E8%A7%A3%E5%86%B3%E6%96%B9%E6%A1%88%E7%AE%80%E4%BB%8B.a528f1bd.pdf)
The repository adapter is deliberately a protocol adapter over an injected bridge,
not an `rqdatac` process implementation. Its connection becomes CONNECTED only
after a bridge-returned, session-bound data-only capability handshake passes.
Authenticated vendor transport and entitlement validation remain external work.
