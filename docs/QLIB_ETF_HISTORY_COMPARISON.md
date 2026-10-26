# Fixed-cohort ETF historical simulation comparison

`scripts/run_etf_history_comparison.py` compares two fixed-rule strategies on an
independently replayed ETF history audit. It produces a separate immutable
historical simulation archive. It does not change the running ETF SHADOW
contract, its historical-metrics fields, stock books, source archives, collector,
or any execution-admission policy. `LIVE_TRADING_ENABLED=false` remains required.

## Source and reuse audit

The existing vn.py source/gap decision remains
`docs/VNPY_GAP_ANALYSIS.md` / ADR-004: gateway objects are adapters and neither
PortfolioStrategy nor paperaccount is the production accounting authority. This
task needs historical research simulation only and reuses the already tested Q
research components below; it adds no gateway, PAPER ledger or new fill engine.

| Component inspected | Reuse / gap decision |
| --- | --- |
| `MomentumAllocation` in `src/qtrade/research/multi_asset.py` | Reuse unchanged with horizon 20, positive-score filter, Top3, daily rebalance and 5% reserve. Equal-score ties use symbol order. |
| `DailyMultiAssetBacktester` in the same file | Reuse unchanged: closes inform the next session's open; sell before buy; deterministic symbol order; configurable lot, proportional fee, adverse slippage and cash affordability. Strict full-grid input means no stale valuation is used. |
| `ExperimentRunner` in `src/qtrade/research/runner.py` | Inspected, not instantiated: it requires a training/validation/test split and writable metadata catalog. These two predefined non-fit rules have a short warmup/evaluation window, not fitted train/validation experiments. Inventing fitting splits would misdescribe the evidence. |
| `evaluate_returns` in `src/qtrade/research/metrics.py` | Reuse for the common evaluation window only, with equal-weight returns as the benchmark. No unsupported trade-PnL ratio is reported. |
| Existing artifact writer | Inspected: it exposes terminal positions but not daily cash/holdings and has a drawdown peak that starts with observed equity. The isolated runner therefore rebuilds a full daily ledger from the reused engine's actual trade journal, including initial-cash peak and first-execution costs. |
| `verify_etf_history_audit` in `scripts/audit_etf_history.py` | Mandatory read-only raw reconstruction of the supplied audit; a signed JSON document alone is insufficient. No legacy rows are added to the simulation. |

No existing engine or live SHADOW implementation is edited. The added journal
replay is an accounting check, not an alternative order-sizing or fill engine.
The report binds the actual source hashes of the runner, audit, multi-asset
engine, metrics and unchanged ETF allocation contract as well as the stated Git
revision.

## Common strategy and simulation policy

Both strategies retain every symbol declared by the source diagnostic, including
any symbols excluded from its latest close diagnostic. Missing values cannot
shrink that cohort or receive fabricated prices. The audit selects the latest
full-cohort contiguous exchange-session run ending at its actual as-of date. All
expected sessions, common sessions, contiguous runs, selected sessions and latest
diagnostic exclusions remain in the report so any trimming is visible.

Both strategies stay in cash through the first 21 common closes. Their first
decision uses closes 0 through 20 and executes at session 21's open. Every later
close produces the next session's target. The final signal is retained as
`NO_NEXT_OPEN_IN_INPUT` with no invented future execution. At least 22 complete
common sessions are required; the common evaluation window excludes the 21 idle
warmup sessions and includes all subsequent net returns and costs.

`positive20_top3_daily` ranks `close[t] / close[t-20] - 1`, retains positive
scores and invests equally in at most three ETFs. When none is positive, it
remains simulated cash. `fixed_pool_equal_weight_daily` invests equally in the
entire fixed cohort each day, with no score screen or TopK selection. Both use
the same 95% investment budget / 5% reserve. Weights are truncated to 12 decimals
with ROUND_DOWN, matching the unchanged ETF allocation contract. The report
binds that canonical allocation contract and its hash; historical execution
assumptions remain separately declared.

Assumed defaults, all exposed by CLI and written into the simulation identity:

| Assumption | Default |
| --- | --- |
| Initial simulated cash | CNY 1,000,000 |
| Whole-lot size | 100 shares, configurable; this is not ETF rule admission |
| Fee | 0.001 of filled notional on each side; no minimum fee or stamp-tax model |
| Adverse slippage | Buy at open × 1.0005; sell at open × 0.9995 |
| Cash interest / borrowing | Zero interest; long only, no borrowing |
| Capacity / limits | Unlimited assumed capacity; no participation, spread, price-limit, suspension or settlement enforcement |
| Terminal treatment | Mark remaining holdings to the last close; no forced liquidation |
| Arithmetic | Fresh explicit Decimal context: precision 28, ROUND_HALF_EVEN, Emin/Emax ±999999, clamp 0; InvalidOperation/DivisionByZero/Overflow trap |

Caller Decimal precision, rounding, exponent settings and traps cannot change a
replay. The lot parameter is passed directly to the existing engine and every
reported trade is checked against it; custom-lot regression coverage verifies
that declared and actual sizing agree.

These are explicit research assumptions, not verified ETF execution rules.
Native source prices have no certified adjustment or distributions/splits
reconciliation, and the current cohort is not a historical point-in-time
membership replay. Historical bars were captured retrospectively, so their
historical availability is unproven. Annualized metrics over a short common
window are descriptive. No result provides PAPER admission, a forward return,
source-equivalence certification or full STEP1 acceptance.

## Accounting and immutable output

For each strategy the archive contains signed `targets.json`, `trades.json` and
`ledger.json` artifacts. Targets record decision date, full weight vector and
next execution session. Trades record the previous-close signal date, actual
simulated quantity, raw open, slippage-adjusted price, notional, assumed fee and
cash delta. Daily ledgers record opening cash/positions, opening NAV, closing
cash, all closing holdings and valuations, NAV, net return, drawdown, exposure,
fees, slippage and turnover. Every ledger row additionally carries the sorted,
complete fixed-cohort `source_bars` grid for that session: symbol and original
audited open/high/low/close/volume decimal strings (at most 50 symbols). Both
strategies expose identical market marks, including symbols with no trade or
position and names sold completely during that session. This permits data-only
public verification of opening NAV, each P&L component and the positive20 target
weights; the public marks still do not replace private receipt/raw-source replay.

The runner independently replays cash and shares from the engine trade journal,
checks buy/sell price and fee assumptions, rejects negative holdings/cash, and
reconciles every NAV/return/exposure/turnover/cost curve plus terminal positions.
It also checks the daily P&L identity:

`NAV change = prior-holdings close change + signed trade open-to-close PnL - slippage - fees`.

The bounded Decimal comparison tolerance is initial cash × 1e-18. Turnover has
two explicit denominators: filled notional / initial cash (the reused engine's
definition), and filled notional / previous NAV. Drawdown starts from initial
cash so first-execution fees are included. Summary metrics use the common
evaluation sessions only. Terminal cash, NAV, assumed fees and slippage are
reported separately.

The output directory is `<output>/<simulation_id>/`, containing the six
artifacts and `comparison.json`. The simulation ID binds the exact replayed
audit identity/file hash, source bindings and actual source reception clock,
cohort, warmup/evaluation sessions, implementation hashes/revision and all
assumptions. The comparison manifest binds each artifact's signed evidence hash
and actual file hash. Generation time is metadata outside the simulation
identity and artifact values; exact reruns reuse the original manifest time.
That time must be aware, no earlier than the latest verified source receipt and
no later than the real clock.

All artifacts are written immutably. A partial run can complete missing files
only when existing files match recomputed content. A corrupt existing file is
never overwritten. Output must be separate from the source archive, and resolved
artifact paths cannot escape their run directory. The final manifest is written
after all six artifacts exist; source files are not changed. No writable catalog,
production publication, web API update or SHADOW-metrics update is performed.

## Reproduction

Run focused checks from the repository root:

```powershell
$env:PYTHONPATH = 'E:/QTrade-git/src'
$env:MYPYPATH = 'E:/QTrade-git/src'
python -m pytest tests/test_etf_history_comparison.py tests/test_audit_etf_history.py tests/test_multi_asset_research.py -q --basetemp=E:/eh1
python -m ruff check scripts/run_etf_history_comparison.py tests/test_etf_history_comparison.py
python -m mypy --explicit-package-bases --platform linux scripts/run_etf_history_comparison.py
```

Create an independently replayable source audit first, without new source
requests. For an existing audit that includes legacy overlap, supply its exact
`--legacy-manifest` and `--legacy-bars` to both commands; these verify audit
identity but contribute no simulation rows.

```sh
PYTHONPATH="$PWD/src" python -m scripts.audit_etf_history \
  --archive /path/to/verified/diagnostic-id \
  --calendar configs/observer_calendar_2026.json \
  --revision <reviewed-audit-revision> > /separate/path/source-audit.json

LIVE_TRADING_ENABLED=false PYTHONPATH="$PWD/src" \
python -m scripts.run_etf_history_comparison \
  --archive /path/to/verified/diagnostic-id \
  --audit /separate/path/source-audit.json \
  --calendar configs/observer_calendar_2026.json \
  --revision <reviewed-runner-revision> \
  --output /separate/path/etf-history-simulations \
  --initial-cash 1000000 --lot-size 100 \
  --transaction-cost-rate 0.001 --slippage-rate 0.0005
```

Production archive persistence uses Linux directory fsync. The focused Windows
tests accommodate the platform's directory-fsync limitation after confirming
the actual file bytes were written; they do not establish Linux durability or
real-market historical performance. Actual source replay and Linux integration
evidence must be recorded separately by the milestone owner. Synthetic fixtures
prove causal timing, common warmup, full-cohort treatment, accounting identities,
configuration propagation and deterministic replay, not market performance.
Adding the public mark grid changes the runner's implementation hash and therefore
creates a new immutable simulation; older experiment archives remain unchanged.
