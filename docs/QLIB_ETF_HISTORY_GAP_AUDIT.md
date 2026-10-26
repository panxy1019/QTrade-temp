# ETF historical comparison gap audit

Audit date: 2026-10-27 (Asia/Shanghai). This records local read-only evidence and
the remaining original STEP1 Phase E work. It does not establish a newly executed
strategy backtest, an actual next-session observation, or permission to trade.
`LIVE_TRADING_ENABLED=false` remains mandatory.

## Original scope and present boundary

`plans/UNIFIED_DATA_ONLINE_GUIDE.md` section 7 requires ETF trend rotation under
the unified research contract, explicit experiment change axes, separation of
signals from portfolio construction, and immutable daily PAPER/SHADOW records.
Phase E also requires meaningful strategy comparison and forward observation.
The new ETF contract supplies positive 20-session momentum, daily Top3 equal
positive weights with 95% maximum allocation, and a separate prospective SHADOW
channel. Source-native close diagnostics and frozen targets alone do not establish
historical strategy performance or complete future-session acceptance.

A full ETF Alpha158 training Provider is not necessary to implement this original
allocation/SHADOW requirement. ETF histories must nevertheless be independently
admitted before a faithful historical comparison can be published. The seven
stock-model common panel is a different cohort; ETF cannot be added as an eighth
model-only comparison. Universe, price basis, signal, portfolio, clock, execution
and evaluation differences must be explicit change axes.

## Verified local legacy source

The following existing files were read locally, without vendor requests:

| Item | Verified value |
| --- | --- |
| Manifest | `E:/QTrade-temp/deployment/evidence/market-data/manifest.json` |
| Data bytes | `E:/QTrade-temp/deployment/evidence/market-data/bars.parquet` |
| Dataset | `akshare-etf30-factor-research-20220906-20260821-v1` |
| Manifest schema | `qtrade.bar.v1` |
| Manifest evidence SHA256 | `e819a5a2d8294b7b987b9ed63940cfe71e08097c5ae0c47ee04036f4dd15870c` |
| Actual Parquet SHA256 | `39a25d71d2c77798c8d73b8b7b45634d229a22122c18a174d3f7be9ac50d59fd` |
| Actual rows / distinct symbols | 28,770 / 30 |
| Observed coverage | 2022-09-06 through 2026-08-21 |
| Declared source | `AKShare 1.18.84 fund_etf_hist_sina raw daily` |
| Declared ingestion | `2026-08-24T16:11:01.313228+00:00` |

`verify_signed(manifest)` passed and the actual Parquet hash matches its manifest.
Its columns are `symbol`, `exchange`, `timestamp`, `trading_day`, `open`, `high`,
`low`, `close`, `volume`, and `data_version`. Prices and volume are strings;
`trading_day` is a Parquet date32. There is no amount column, corporate-action
evidence, settlement policy or trading-rule certificate in this table. The first
stored row is SSE 510050 on 2022-09-06 (close 2.764); the last is SZSE 159915 on
2026-08-21 (close 3.56). These row examples establish file content, not per-symbol
complete coverage.

The manifest's older `PASSED` quality declaration is not a new execution-input
certificate. It explicitly sets `real_data_paper_readiness=false` and
`snapshot_readiness=false`. The ETF30 universe was selected using a current
candidate list known on 2026-08-12; `point_in_time=false` and survivorship bias
are recorded. Hash verification proves integrity, not historical PIT availability,
adjustment policy or equivalence to the new October Sina capture path.

Legacy experiment records and artifacts exist under
`E:/QTrade-temp/deployment/evidence/akshare-v4/`. In particular,
`akshare-etf30-momentum-trend-20260812-v4.json` binds the dataset above and commit
`840750d854dabea5f600860d7916b1c027c2ca33`. It uses horizons 20/60/120 with a
120-session trend filter; the legacy runner uses a 20-session rebalance interval,
Top3, 5% reserve and linear transaction cost 0.0015 (v4 slippage zero). Its label
is `daily_portfolio_close_to_close_return_with_next_open_rebalance`, with train
2024, validation 2025 and test 2026-01-01 through 2026-08-12. This differs from
the new single-horizon positive20 daily contract. Its old curve and metrics cannot
be relabeled as the current contract or as new forward results.

## Current immutable archive and raw-history opportunity

Parent integration can inspect the existing cluster archive read-only. Let `B`
be its configured research base directory; do not infer it from public paths:

```text
<B>/online-research/etf-trend/latest.json
<B>/online-research/etf-trend/<diagnostic_id>/diagnostic.json
<B>/online-research/etf-trend/<diagnostic_id>/receipt.json
<B>/online-research/etf-trend/<diagnostic_id>/capture-store/objects/<raw_sha>.jsonl.gz
```

Recorded local evidence `docs/evidence/qlib-daily-ledger-55b5b00.json` identifies
diagnostic `b3cd61f630c17146a4c2d3b0fa3060a3e1b98ff8faf3735387a3f24b196296d4`.
`docs/evidence/qlib-step1-initial-native-20261027.json` records as-of 2026-10-26,
30/30 qualified 21-close inputs from 2026-09-18 through 2026-10-26, master SHA
`aea0fe389a3cb016f020b68fe327e9e9c910e53a90ff61fff2dc81a96adc6fa5`, source
revision `d83e1ae556205114790945359a9c7eb2a658cd6b`, calendar SHA
`b31af901ef888cf76f0137a06829e2bf3b9f27c430b85e9af80e48f9a2b1165c`, and receipt
SHA `0f4bc4676110c3a9ae2f4a1a3c1ebee3165a7d40b5d0f047fab3247cf4507c83`.
These are recorded evidence identities; this audit did not fetch cluster bytes or
independently establish the unused earlier rows in those raw bodies.

The collector requests `datalen=63`; the admitted diagnostic retains 21 closes.
The existing body may therefore contain useful earlier rows without another
request. A request for 63 does not prove 63 returned, that all symbols share a
complete grid, or that earlier rows satisfy the verified calendar.

Use the existing source APIs rather than treating public scores as history:

```python
diagnostic = verify_etf_archive(archive, calendar)
receipt = json.loads((archive / 'receipt.json').read_bytes())
captures = UnifiedBatchStore(archive / 'capture-store').read_captures(receipt['raw'])
```

`verify_etf_archive` and `read_captures` replay compressed content identity, raw
body hashes and source lineage. Preserve each actual request, symbol, received
clock and body hash. Do not certify rows simply because their JSON is present.

## Actual cluster raw inventory, October 27

A subsequent read-only cluster replay passed `verify_etf_archive` and
`UnifiedBatchStore.read_captures` for all 30 captures. Every body contains 63
rows with 63 distinct date strings, from 2026-07-22 through 2026-10-26. All
bodies expose day/open/high/low/close/volume, without amount. Per-symbol body
hashes and actual row bounds are preserved in
`evidence/qlib-etf-raw-inventory-20261027.json`, signed at the actual audit clock.
This establishes that earlier data exist; it does not yet admit OHLCV values,
prove the calendar grid or certify legacy overlap. July rows remain outside the
current verified calendar. The inventory is explicitly `RAW_INVENTORY_ONLY_NOT_OHLCV_ADMISSION`
and `backtest_ready=false`. The prior local-only conclusions above are preserved
as provenance; this later evidence resolves only the unknown raw row bounds.

## Calendar and return-clock limits

The actual checked-in `configs/observer_calendar_2026.json` is valid only from
2026-08-01 through 2026-12-31. Counting `Calendar.is_open` gives **55 sessions**
from 2026-08-03 through 2026-10-26. It excludes its explicit September/October
closures. Earlier raw rows require an independently sourced calendar extension;
weekdays outside the current horizon are not implicitly admitted sessions.

Even if a body contains 63 rows, this calendar permits at most 55 rows through
that as-of date, before missing-grid exclusions. For a 20-session momentum
lookback, a complete N-close grid permits at most N-21 matured next-close price
exposures: 34 for N=55. If 63 common closes were independently admitted with a
broader calendar, the upper bound would be 42. Both are upper bounds, not observed
evaluation counts. The currently admitted 21-close diagnostic has no matured
subsequent historical exposure label.

Applying a close-t signal to `close[t+1]/close[t]-1` includes the overnight move
before the intended next-open execution. This can be labeled an explicitly
mathematical native-close allocation price diagnostic; it cannot be presented as
next-open executed performance. A conservative close-only alternative starts at
the first close after that open and evaluates close-(t+1) to close-(t+2), costing
one extra lag: at most N-22 labels (33 for N=55). A separate next-open price
scenario could instead audit actual opens, but still cannot certify fills or fees
without the missing execution evidence. Native prices do not establish adjusted
total return or reinvested distributions.

## Faithful reuse and missing execution evidence

`MomentumAllocation((20,), top_k=3, rebalance_every=1, reserve=Decimal('0.05'))`
already implements the new positive20 allocation. For a same-cohort daily
equal-weight reference, use `signal_baselines.allocation` with
`method='EQUAL_WEIGHT_ALL'` over **all members of the fixed admitted ETF cohort**,
with the same 95% allocation and label grid. Do not restrict that reference to the
trend strategy's positive-score subset. `EqualWeightBuyHold` allocates only on
session index zero; it is a different, buy-and-hold comparator. A daily equalweight
reference changes both signal selection and portfolio construction relative to
the Top3 positive strategy; do not describe the comparison as model-only.

`DailyMultiAssetBacktester` does defer pending targets to next open. However, its
fixed lot100 and simplified linear cost/slippage assumptions do not admit actual
ETF lot sizes, exact fees, settlement, suspensions, price limits, prior-session
volume capacity, distributions/splits or other corporate actions. It may reuse
held stale marks under its configured limit. Reusing it unchanged would produce
assumed simulation results, not certified current ETF executions. Actual fee and
net execution-return fields must remain blocked/null until adequate rules and
inputs are independently supplied. Any optional linear-friction sensitivity must
be separately named as an assumption, with a declared change axis; 0.0015 from a
legacy experiment is not an ETF broker fee certificate.

`ExperimentRunner` requires a PASSED `DataManifest`, chronological train/validation/
test splits, nonempty finite metrics, and equity/drawdown/trades/positions/risk/log
artifacts. The present short close-only audit does not justify invented fitting
splits, filled trades/positions or zero net metrics to satisfy that interface.
Reuse content hashes and artifact primitives for an explicitly nonfitted price
diagnostic. Adapt the runner only when its inputs and output semantics can be
fulfilled honestly. Do not call a close-only diagnostic a completed strategy
backtest merely because it can be wrapped in `ExperimentRunner`.

## Concrete remaining audit and acceptance steps

1. Verify the existing signed pointer, immutable diagnostic/receipt and all raw
   object hashes locally on the cluster. Inventory actual returned row counts,
   per-symbol date bounds, duplicates, incomplete/future bars, invalid OHLCV,
   amount availability and rows outside the admitted calendar. Preserve exclusions.
2. Normalize each candidate day through `completed_klines` using its true symbol,
   `security_type='ETF'`, day frequency, requested day, actual receipt observation
   clock and verified calendar. Do not invent missing volume, adjustment factors
   or trading rules. Extend the calendar only with independently verified evidence.
3. Fix one same-ETF cohort before comparing outcomes; require a common contiguous
   admitted session grid, record unavailable/excluded symbols, and avoid forward
   filling. Reserve 20 lookback sessions, then count only matured labels under one
   explicit exposure clock. Short results need a short-history limitation and
   cannot establish robustness, annualized expectations or deployment readiness.
4. Audit legacy/new source overlap before any combination: intersect identities
   and verified dates, compare exact OHLCV and ratios, document discontinuities and
   source/version differences. Existing legacy data end on August21 whereas the
   admitted new diagnostic starts September18; unused raw earlier rows may or may
   not provide overlap. No overlap means no equivalence finding. Even exact overlap
   alone cannot clear distribution/split or historical-availability uncertainty.
5. Bind both positive20 and daily equalweight price diagnostics to identical raw,
   calendar, cohort, weight precision, allocation budget and evaluation clocks.
   Publish only explicitly native-price diagnostic quantities; retain null blocked
   execution/net-cost/portfolio-return metrics and the existing SHADOW separation.
6. Add fixture tests for changed raw hashes, missing/duplicate/future sessions,
   calendar bounds, fixed-cohort exclusions, 20-session warmup, final unmatured
   labels, zero positive trend, baseline membership, lag/no-lookahead behavior,
   source overlap mismatch, and forbidden fills/fees/returns. Independently replay
   each output from immutable inputs before publication.

Historical comparison remains open until these steps establish an adequate
verified same-cohort report. PAPER remains blocked by missing execution evidence.
Actual prospective forward acceptance remains gated on the real next session,
durable before-open admission and subsequent verified receipts. Synthetic clock
tests prove code behavior; they do not prove that a future market session occurred.

## Reproduction

The local byte/schema checks used `C:/ProgramData/anaconda3/python.exe` with
`PYTHONPATH=E:/QTrade-git/src`, `pyarrow.parquet.read_table`,
`qtrade.research.qlib_publication.verify_signed`, and `hashlib.sha256` over actual
file bytes. A minimal read-only reproduction is:

```powershell
$env:PYTHONPATH='E:/QTrade-git/src'
@'
import hashlib, json
from pathlib import Path
import pyarrow.parquet as pq
from qtrade.research.qlib_publication import verify_signed
p = Path('E:/QTrade-temp/deployment/evidence/market-data')
m = json.loads((p/'manifest.json').read_bytes())
verify_signed(m)
assert hashlib.sha256((p/'bars.parquet').read_bytes()).hexdigest() == m['manifest']['checksum']
t = pq.read_table(p/'bars.parquet')
print(t.schema, t.num_rows, len(set(t.column('symbol').to_pylist())))
print(min(t.column('trading_day').to_pylist()), max(t.column('trading_day').to_pylist()))
'@ | C:/ProgramData/anaconda3/python.exe -
```

No source request, production mutation or new backtest was performed for this audit.

## Read-only history audit command

Run the committed audit tool against an immutable archive and the verified
calendar. Optional legacy arguments must be supplied together. Redirect stdout
to a new evidence file outside the source archive; existing evidence must not be
overwritten. The tool performs no network requests or source writes.

```powershell
$env:PYTHONPATH='E:/QTrade-git/src;E:/QTrade-git'
python -m scripts.audit_etf_history --archive <archive-directory> --calendar configs/observer_calendar_2026.json --revision <audit-code-commit> --legacy-manifest <legacy-manifest.json> --legacy-bars <legacy-bars.parquet>
```

`verify_etf_history_audit` independently recomputes a saved audit against the
same source paths and calendar. Hash verification alone is insufficient. The
report fixes the declared cohort, retains per-symbol invalid/missing sessions,
rejects dates outside the calendar, and exposes the contiguous common grid ending
at the archive as-of. Legacy overlap is exact OHLCV comparison only; even full
agreement does not certify adjustments or authorize joining histories.
