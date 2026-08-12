# AKShare provider readiness

Assessment date: 2026-08-12. Package tested: `akshare==1.18.84`.

## Classification

AKShare is accepted only as a credential-free, low-frequency provider for
noncommercial research and `REAL_DATA_PAPER`. It is not an authoritative,
production-grade, streaming or execution feed. The MIT license covers the
software; it does not grant blanket rights to upstream data. AKShare's official
statement limits its interfaces/data to academic research and warns that
interfaces and data can change or disappear.

The adapter therefore requires
`QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true` before constructing its native client.
This is an operator acknowledgement, not a credential or entitlement.

Primary sources:

- [AKShare PyPI package](https://pypi.org/project/akshare/)
- [AKShare official data-use statement](https://akshare.akfamily.xyz/special.html)
- [AKShare ETF documentation](https://akshare.akfamily.xyz/data/fund/fund_public.html)
- [AKShare stock documentation](https://akshare.akfamily.xyz/data/stock/stock.html)
- [AKShare index documentation](https://akshare.akfamily.xyz/data/index/index.html)

## Implemented boundary

- SDK import is optional and isolated behind `NativeAkshareClient`.
- Exact dependency pin: `akshare==1.18.84` in the optional `akshare` extra.
- `connect()` becomes healthy only after a nonempty upstream ETF snapshot probe.
- ETF raw daily and 1/5/15/30/60-minute history normalization.
- A historical-only connection path through `fund_etf_hist_sina`; it cannot
  attest snapshot polling or `REAL_DATA_PAPER` readiness.
- ETF polling snapshot normalization, including source update time and Bid1/Ask1
  when present.
- One cached all-ETF snapshot per configured interval; no per-symbol polling fanout.
- One in-flight upstream request, minimum request spacing, bounded retries with
  backoff, staleness rejection, duplicate polling-observation suppression,
  heartbeat health and a circuit breaker.
- Current-snapshot liquidity-ranked 20-50 ETF candidate universe with explicit
  survivorship/current-universe warning. It is not a point-in-time research
  universe.
- Canonical observations flow through hash-chained raw recording, persistent DQ
  cursors, immutable canonical segments, finalized bars, causal strategy decisions
  and PAPER execution only on a later received observation.
- A checksum-protected per-event session journal recovers crashes before the runner
  boundary; ambiguous crashes after entering that boundary halt and require explicit
  reconciliation. New events cannot silently skip a durably archived predecessor.

The first acceptance universe remains ETF-only for portfolio research. Historical
daily normalization now also covers raw A-share bars through
`stock_zh_a_hist_tx` and major-index bars through `stock_zh_index_daily`; bounded
live probes for 600000 and CSI 300 passed. Their snapshot/minute paths are not yet
accepted. Corporate actions and point-in-time constituents are not provided by
this adapter.

## Endpoint acceptance result

Two bounded one-shot probes were attempted from this Windows host:

1. `fund_etf_spot_em()` against `push2delay.eastmoney.com`;
2. `fund_etf_hist_em(symbol="510300", adjust="")` against
   `push2his.eastmoney.com`.

Both Eastmoney-backed calls failed before data receipt with an upstream Python TLS/connection error
(`UNEXPECTED_EOF_WHILE_READING`; direct bypass subsequently closed without a
response). No certificate verification was disabled, no rapid retry loop was
started, no data was fabricated, and the provider remained disconnected.

The independent Sina-backed `fund_etf_hist_sina` daily-history endpoint did
return real data. A bounded 30-ETF ingest covering 2024-01-02 through 2026-08-12
produced 18,960 canonical daily bars and passed the quality gate. The resulting
Parquet checksum was
`70e3abf151b88382430ce82b333702243618a5eccc0b9deb71f30b19e694ac98`.
The frozen universe is `configs/akshare_etf_universe_v1.json`; it is explicitly
a current candidate universe with survivorship bias, not PIT membership.

Five initially selected series with recent greater-than-50% raw discontinuities
were excluded instead of being silently adjusted without sourced corporate-action
factors. This dataset is suitable for exploratory noncommercial research, but it
does not authorize snapshot-driven PAPER execution.

Run the one-shot diagnostic in an isolated environment:

```powershell
$env:QTRADE_AKSHARE_NONCOMMERCIAL_ACK = "true"
python -m pip install -e ".[akshare]"
python scripts/akshare_probe.py --universe-size 30
python scripts/akshare_history_ingest.py --start 2024-01-01 --end 2026-08-12
```

Status is `AKSHARE_HISTORICAL_READY` and
`WAITING_FOR_AKSHARE_SNAPSHOT_ENDPOINT_ACCESS`. Snapshot-driven PAPER readiness
remains false until the snapshot command returns `ready=true` and a subsequent
schema/freshness/recording acceptance test passes.
