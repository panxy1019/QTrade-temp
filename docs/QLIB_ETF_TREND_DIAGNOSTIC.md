# ETF native-price trend diagnostic

This separate STEP1 family reuses the observed securities master, public Sina
capture envelope, `UnifiedBatchStore`, completed daily OHLCV parser, exchange
calendar and existing positive 20-session trend factor. It admits **close-only
research evidence**, not full market bars or ETF execution. It does not mutate
stock Providers, compare raw prices against adjusted stock return series, or
add ETF orders to PAPER/SHADOW.

Sina's native price basis is uncertified: calling it definitively raw or
adjusted would overstate the source. ETF distributions and splits are not
reconciled. The interface must show this limitation next to the diagnostic.
The output identifier includes `native_close_diagnostic` to distinguish this from
a future corporate-action-adjusted strategy; `price_basis` is authoritative.

Run from the committed release with the existing unified observed master:

```bash
export LIVE_TRADING_ENABLED=false
export PYTHONPATH="$PWD/src"
python -m scripts.run_etf_trend \
  --master "$B/unified-data/catalog/latest.json" \
  --output "$B/online-research/etf-trend" \
  --source-revision "$(git rev-parse HEAD)" --collect
```

`--collect` explicitly permits one bounded request per target ETF (maximum 50),
63 daily candles each, through the existing fixed public endpoint and timeout.
No automatic retries or background loop are added. Run outside active daily
collection to avoid competing for vendor access. A durable collection-attempt
prevents a killed or failed run from silently issuing another burst. In that
case inspect the attempt; this CLI deliberately has no force-retry option.

Raw bytes are durably checksummed before parsing. A signed receipt records the
master, calendar and source revision. Re-running the same completed capture
reuses its receipt; without `--collect` a missing receipt fails closed. A new
source or date creates a distinct archive. Failed responses remain auditable.
All 21 actual completed sessions must be present for each admitted ETF; missing,
invalid, duplicate or future observations cannot be forward-filled. An ETF with
zero or negative 20-day change remains a qualified input but is absent from the
positive-trend score list. No positive scores can legitimately mean cash.

`diagnostic.json` contains current input date, admitted-close count, requested
count, score list, close histories, exclusions and explicit limitations. It
always sets `full_bar_qualification=false`, `execution_admitted=false`,
`return_adjustment_certified=false`, empty fills and null forward return. Do not
reuse these qualified-close counts as full OHLCV/amount/VWAP coverage. Production
publication should retain this separate diagnostic name and source date.

Validation: five local tests cover valid close-only evidence, missing sessions,
wrong securities, future source clocks and unfinished current-day rejection.
Ruff and mypy pass. Real-source cluster acceptance remains separate and must
record actual counts; local fixtures do not establish vendor coverage.

