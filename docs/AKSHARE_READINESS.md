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

The first acceptance universe is deliberately ETF-only. A-share and major-index
history/snapshot normalization is still required before those capabilities can be
claimed. Corporate actions and point-in-time constituents are not provided by this
adapter.

## Live probe result

Two bounded one-shot probes were attempted from this Windows host:

1. `fund_etf_spot_em()` against `push2delay.eastmoney.com`;
2. `fund_etf_hist_em(symbol="510300", adjust="")` against
   `push2his.eastmoney.com`.

Both failed before data receipt with an upstream Python TLS/connection error
(`UNEXPECTED_EOF_WHILE_READING`; direct bypass subsequently closed without a
response). No certificate verification was disabled, no rapid retry loop was
started, no data was fabricated, and the provider remained disconnected.

Run the one-shot diagnostic in an isolated environment:

```powershell
$env:QTRADE_AKSHARE_NONCOMMERCIAL_ACK = "true"
python -m pip install -e ".[akshare]"
python scripts/akshare_probe.py --universe-size 30
```

Readiness remains `WAITING_FOR_AKSHARE_ENDPOINT_ACCESS` until this command returns
`ready=true` and a subsequent schema/freshness/recording acceptance test passes.
