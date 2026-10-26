# ETF research contract and prospective SHADOW targets

This is an isolated STEP1 building block. It does not change the running stock
forward service, stock comparison, shared publication validators, APIs or UI.
No real next-session ETF observation is established by its synthetic tests.

## Original requirement and remaining integration

The preserved original `plans/UNIFIED_DATA_ONLINE_GUIDE.md` section 7 requires
ETF trend rotation under the unified research contract, separation of signals
and portfolio construction, explicit experiment change axes, and immutable daily
PAPER/SHADOW research records. Phase E additionally requires daily research
portfolios and forward observation. The existing native-close score diagnostic
alone does not satisfy the allocation and prospective-record part of that scope.

The existing seven-stock historical comparison is a separate valid cohort. Its
common stock model panel, adjusted-price assumptions, stock universe and execution
contract cannot be reused to pretend an ETF is a stock or an eighth comparable
model. Current stock forward inputs and validators likewise do not admit ETFs.

This module supplies ETF allocation semantics and a separate durable SHADOW
archive channel. Parent integration still needs a separate supervised loop,
source-pointer/universe guards, bounded strict public publication, API/UI wiring
and actual future-session acceptance. Keeping it separate avoids reopening stock
PAPER books and forcing unrelated recovery. No new strategy registry or model
training framework is needed for this STEP1 contract.

## Reuse and behavior contract

`etf_research_contract.py` reuses `verify_etf_archive`, its exact raw capture replay,
`MomentumAllocation`, the verified calendar/`next_execution`, and immutable durable
JSON writes. It does not introduce another signal algorithm or execution engine.
The older multi-asset backtester is not used as proof of present ETF trading rules:
its fixed lot and simplified fee/fill assumptions are insufficient for that claim.

`qtrade.etf-research-contract.v1` fixes:

- ETF identities from the current observed ETF master and day frequency;
- positive 20-session native-close momentum, `close[t]/close[t-20]-1 > 0`;
- TopK positive-score equal weights, daily target generation, at least 5% cash
  reserve and weights floored to 1e-12; defaults are Top3 / 95% risk allocation;
- next verified exchange open as the intended research clock;
- `SHADOW_NO_EXECUTION`, with unknown ETF fee/settlement policies and no admitted
  trading rules;
- a separate ETF native-close comparison cohort, with historical metrics blocked
  and null until adequate verified same-cohort history is admitted.

This explicit single-horizon daily contract is not a rewrite of the legacy ETF30
multi-horizon/monthly experiments. Different behavior receives a new identity.
`changed_etf_contract_axes` reconstructs and validates both ETF contracts, then
derives the changed behavior groups; changing TopK changes the `portfolio` axis.
Parent unified-catalog integration must additionally label universe, price basis,
clock, execution and evaluation differences when displaying ETF versus stock
cohorts. It must not label those comparisons as model-only changes.

## Immutable targets and clocks

`freeze_etf_target(archive, output, revision, policy=..., calendar=..., now=...)`
verifies the existing ETF source archive, recomputes the allocation and writes:

```text
<output>/<target_id>/target.json
<output>/<target_id>/admission.json
```

Its `qtrade.etf-allocation-target.v1` binds contract, source diagnostic/receipt,
observed-master and calendar hashes, source/implementation revisions, source date,
expected execution and weight hash. It separately records source observation,
actual allocation generation and actual freezing times. The source must already
be observed and freezing must occur strictly before the next open. The production
caller omits `now`; that argument exists for controlled tests and must never be
used to backdate production. Expensive raw verification precedes the clock check.

The target is admissible only with a separate immutable signed
`qtrade.etf-target-admission.v1` receipt. It binds the target identity and evidence
hash to `durable_at`, sampled after the target's file and directory fsync complete.
This clock must be at or after `frozen_at` and strictly before the intended open.
If writing the target crosses open, the call fails and leaves the target without
admission. A crash before a receipt likewise leaves an unadmitted target. Neither
freeze replay nor read-only verification creates a missing receipt, even if the
retry is still before open. Late files are never rewritten automatically.
Receipt persistence may finish after open: it attests the already-completed target
write, rather than claiming that the receipt itself was durable before open.

An existing identical frozen target can be replayed later without changing its
original freeze time. A missing target cannot be imported retrospectively. An
entirely unavailable source is rejected, not converted into a cash allocation.
Qualified inputs with no positive scores legitimately produce an empty planned
weight set. Neither case manufactures actual cash, holdings or returns.

`verify_etf_target(target_path, source_archive, calendar=..., now=...)` recomputes
every source binding, behavior field and weight. A rehashed document with a
changed clock, PAPER mode, execution admission or invented return is rejected.
SHA256 bindings establish integrity and identity, not trading authorization.

`validate_etf_target(value, calendar=..., now=...)` is the data-only validator for
bounded public publication. It enforces the exact versioned contract, source
binding identities, finite decimal strings/equal weights, ETF identities, coverage
bounds, clocks and empty/null execution fields. It does not replace private raw
replay or the private durable-admission receipt. A publisher must obtain targets
through freeze/read-only verification before using the data-only validator.
A service must restore a prior target using its stored implementation
revision after releases, rather than calling freeze with a new revision to create
a second target for the same day and behavior contract.

## Actual next-session observation

`observe_etf_shadow(target_path, source_archive, observed_archive, ...)` first
replays the target and both source archives. It requires the observed archive to
be the exact intended next exchange session, with actual source receipt clocks
already reached. Same-day or later substitute sessions are rejected. It writes:

```text
<output>/<target_id>/observations/<observation_id>.json
```

The signed `qtrade.etf-shadow-observation.v1` contains target weights, actually
observed next-session native closes, missing target inputs, source and processing
times and their exact diagnostic/receipt bindings. Missing closes are not filled
with yesterday's prices. Replaying the same event returns the immutable original.
Both targets and observations have empty orders/fills/holdings and null cash,
NAV and forward portfolio return. `paper_admission` remains `BLOCKED` for unknown
ETF lot/fees/settlement/limit rules, unreconciled distributions/splits and incomplete
execution inputs. No stock tax, stock settlement or stock gateway is substituted.

`validate_etf_shadow_observation(value, target, ...)` validates a public observation
against its exact frozen target, including bounds, clocks, finite native closes,
missing-input consistency and no execution/portfolio-return claims.
`verify_etf_shadow_observation(path, target_path, source_archive, observed_archive,
...)` additionally reconstructs the event from private raw receipt archives and
never creates a missing record. A rehashed invented native close may be structurally
plausible but cannot pass that independent raw replay.

A separate supervisor can discover the existing daily ETF diagnostic pointer,
freeze before open, retain target-to-source archive references privately, and
later call this observation function. It should hold its own local writer lease,
respect collection/space/backoff priority, retain missing days and failed sources,
and publish only bounded validated data. These functions themselves make no source
requests, manage no runtime and never recover a PAPER account.

## Historical comparison boundary

A full ETF Alpha158 training Provider is not required merely to implement this
ETF allocation/SHADOW contract. A historical ETF strategy result does require
adequate verified inputs and explicit evaluation semantics. Legacy ETF30 archives
may be reusable after a source/gap audit; their source equivalence is not assumed.

Existing ETF requests ask for 63 candles while the admitted diagnostic retains
21 closes. The actual archived body may supply earlier real sessions without
another request, but requesting 63 does not prove 63 returned. A future bounded
historical adapter should audit the actual returned grid, calendar horizon,
identity and native-price continuity, then use 20 sessions of warmup followed by
only the genuinely available evaluation interval. It should compare ETF trend
and ETF equal-weight on the same admitted ETF cohort, not require intersection
with Ridge/LightGBM stock predictions.

A lagged native-close allocation index may be useful as a separate retrospective
price diagnostic. It must identify its close-to-close price-exposure convention,
exclude unsupported dividend/adjustment claims, keep executable/net-cost metrics
unknown, and never present its curve as simulated fills or observed forward
portfolio return. The present contract intentionally retains historical metrics
as blocked/null. A costed execution backtest requires independently admitted
price/rule inputs or a clearly separated, explicitly assumed diagnostic policy.

## Reproduction and current evidence

```powershell
$env:PYTHONPATH='E:/QTrade-git/src'
$env:MYPYPATH='E:/QTrade-git/src'
python -m pytest tests/test_etf_research_contract.py -q
python -m ruff check src/qtrade/research/etf_research_contract.py tests/test_etf_research_contract.py
python -m mypy src/qtrade/research/etf_research_contract.py
```

The 56 dedicated checks pass locally, with Ruff and strict mypy. Tests use real
content-addressed raw packs and immutable source receipts with
controlled clocks. They cover prospective freezing, deterministic replay, positive
selection/cash behavior, unknown input rejection, exact future-session observation,
missing marks, forged claims, change axes, disabled LIVE, writes crossing open,
crashes before admission and exact target-bound durable receipts. Windows fixture writes
adapt directory fsync only; real Linux durable writes and actual production clocks
remain integration evidence, not conclusions inferred from unit tests.
