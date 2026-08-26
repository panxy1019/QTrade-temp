# Strategy research report

QTrade v0.2 treats strategies as durable, versioned entities rather than scripts.
The registry records version specifications, parent/child lineage, constrained
lifecycle transitions, research roles and append-only transition history. Human
authorization is required after SHADOW. Champion assignment additionally requires
an APPROVED strategy and an immutable, persisted PASS promotion decision; no LIVE
state exists.

Implemented baseline signal/target primitives are buy-and-hold, moving-average trend, ETF momentum
rotation, ETF trend plus volatility/risk scaling, and cross-sectional factor
ranking. These are not yet licensed-data-validated production strategies.
The read-only `/strategy-templates` catalog maps those five structures to their
actual implementation references, data scope, entry/exit rules, unfrozen
defaults, required inputs and limitations. Four are active research baselines;
equal-weight buy-and-hold is comparison-only. Catalog visibility creates no
experiment or registry record and does not authorize PAPER, SHADOW or LIVE.
ETF momentum rotation, its trend/volatility-constrained variant, and
cross-sectional reversal/low-volatility ranking now load into the existing
bounded causal multi-asset preview. The workbench keeps template defaults
visibly unfrozen, models disabled risk filters as null, and can export all 13
`ExperimentRunSpec` fields plus a deterministic hash. The draft binds the
verified data version/checksum, PIT universe, implementation, chronological
split, parameters, costs and seed, but is not written to immutable storage.
Its historical test dates have already been visible and there is no independent
leakage-review PASS, so it is not fresh confirmation evidence and cannot create
an experiment, register a strategy or authorize PAPER/LIVE.
The workbench can execute that draft in one read-only request. The server
recomputes the spec, reopens the checksum-verified data, runs the same causal
preview and produces a deterministic binding hash over the spec hash, preview
hash, template and data identity. Editing any form field removes the prior
browser binding. This supplies reproducible draft-to-result lineage but remains
stateless and unpersisted; it is neither a registered experiment nor new
held-out evidence.
When the operator saves that result for browser-local comparison, the UI now
retains its template ID, spec hash and binding hash and distinguishes it from an
ordinary immediate preview. The save path verifies the current result and data
identity plus every no-authority flag before writing; older schema-v1 rows load
as explicitly unbound. This removable browser state is not immutable storage,
registration, promotion evidence or PAPER/LIVE authority.
The browser-local A/B table exposes total and annualized return, matched
benchmark return, active excess, Sharpe, information ratio and maximum drawdown
for every saved row. Missing fields from older records stay unavailable. A
table-adjacent warning preserves the current-universe, non-PIT survivorship
boundary, so displaying common metrics does not turn repeated tuning into
independent validation.
Loading a bound local row restores the saved template identity and exact
backtest parameters, clears all computed result/draft/binding state, and resets
the run-spec form for a new dataset-bounded draft. Its old spec and binding
hashes are displayed only as historical identifiers: the local summary does not
contain the original experiment ID, source commit, split or seed, so the full
13-field spec must be rebuilt and rehashed. Ordinary rows stay unbound, and an
incompatible local template/parameter pair does not restore a template.
The whole local comparison is now exportable as one deterministic JSON
checkpoint. Provider/version/checksum/PIT identity sits beside every parameter
set, common metric, preview hash and optional template/spec/binding identity;
no export timestamp is added. This permits portable audit and A/B review without
turning removable browser state into automatic synchronization, immutable
experiment evidence, registry admission or PAPER/LIVE authority.
Separately, the intraday-factor notebook now shows every stored research axis on
one A/B row: cumulative/annualized return, active excess, Sharpe, drawdown, Rank
IC, maximum absolute correlation, stitched out-of-sample return/excess,
Newey-West t and 95% interval. Those values are copied from the exact saved
checksum/result-hash summary; display does not create a new calculation,
independent observation, experiment record or promotion decision.
New intraday-factor rows also keep the exact read-only strategy ID and behavior
specification hash that bind entry factor, exit rule, sizing, benchmark, cost and
T+1 execution. The client rejects a hash/ID mismatch or any relaxed registration,
PAPER, promotion, state or order flag; old rows display no identity rather than
receiving a synthetic one. All retained identities remain
`AD_HOC_UNREGISTERED` and cannot create a StrategyVersion.
Each adjustable result now includes an equal-weight Buy-and-Hold run from the
same causal simulator. It selects symbols visible at the first decision close,
executes at the next open and shares the strategy's window, initial cash,
reserve, transaction cost and slippage. Strategy and benchmark sessions are
required to align before the API returns benchmark equity/drawdown, benchmark
return, cumulative excess and information ratio. The comparison is still based
on the current non-PIT universe and therefore remains a survivorship-biased
diagnostic rather than promotion evidence.
Experiments bind git commit, data version, PIT universe, chronological
train/validation/test split, model, parameters, cost model and seed to immutable
hashed artifacts. The runner automatically registers PASSED dataset lineage and
the experiment result in SQLite, requires confined equity/drawdown/trades/
positions/risk/log artifacts, and includes their SHA-256 values in a deterministic
result hash.

Promotion is multi-metric and fail-closed. It includes OOS return, Sharpe, Sortino,
drawdown, Calmar, turnover, transaction costs, exposure, stability, parameter
sensitivity, walk-forward consistency, data quality, embargo and PAPER/SHADOW
observations. Outcomes are PASS, REJECT or NEEDS_REVIEW; a single high Sharpe can
never promote a strategy.

The current research-loop contract is bounded and hypothesis-led. Every hypothesis records economic
intuition, regimes, failure modes, required data and evaluation criteria. Candidate
and iteration budgets prevent brute-force search; held-out test windows cannot be
reused or overlapped for candidate selection; an independent leakage-review pass is mandatory.
Robustness scenarios are predeclared and bounded and include parameter neighbours,
cost stress and regimes with deterministic hashes and explicit negative-Sharpe
scenario detection.

The first real AKShare ETF walk-forward evidence uses three complete, disjoint
folds. Candidate simulations stop at validation before selection; only the fixed
winner then reaches the held-out test. The result is negative in all three folds
and remains `NOT_ELIGIBLE`. The report includes the exact data checksum, source
commit, candidate specifications, validation scores, test metrics, observed trade
counts and deterministic hashes; the dashboard rejects a tampered envelope.
The backtest page now renders those three held-out failures directly beneath the
exploratory leader: `0/3` positive folds, aggregate return `-20.4412%`, aggregate
Sharpe `-1.1005`, and maximum drawdown `-28.6241%`. It explicitly gives this
predeclared evidence priority over the positive result selected after observing
the sensitivity comparison, reducing the chance that an operator mistakes
selection bias for strategy validation.

The rejected candidate also has a bounded 14-scenario diagnostic covering cost,
parameter, subperiod and deterministic current-universe perturbations. It finds
a worst Sharpe of `-1.1429123433`, Sharpe dispersion of `1.6703725093`, and at
least one negative-Sharpe scenario. These scenarios reuse already observed data
and retain current-universe
survivorship bias, so the result is diagnostic only and cannot be counted as a
fresh held-out test or promotion evidence. Its signed envelope is verified by
the API before the Chinese research page displays it.

A subsequent frozen robustness search evaluates 36 factor/parameter candidates
over eight 2024-2025 quarterly windows. The ranking prioritizes positive-window
frequency, median Sharpe and worst-window Sharpe before dispersion and turnover.
Only `reversal-10-top3-20` met all three window hard gates. It then passed
doubled-cost return, one-parameter-neighbour consistency, six deterministic 80%
universe subsets and a 1,000-replication stationary bootstrap: base return was
`38.8414%`, doubled-cost return `30.9870%`, `8/9` neighbours and `6/6` subsets
were positive, and bootstrap positive-return probability was `85.3%`. The
bootstrap 5% total-return quantile remained negative at `-13.7648%`, preserving
the downside uncertainty hidden by the point estimate.

The candidate was locked before any 2026 access. Its labelled polluted audit
then returned `-0.4437%` with `-17.8407%` maximum drawdown, while Buy-and-Hold
returned `-4.6176%`. These observations were previously visible to the project;
they are neither a clean confirmation nor an input to tuning. The API therefore
recomputes the complete selection/gate arithmetic but still returns
`NOT_ELIGIBLE`, and the web application explicitly prevents interpreting a
research-robust result as PAPER or promotion authority.

The ETF-selected candidate was next transferred without stock-side tuning to a
pre-frozen 75-stock universe spanning 15 current SW level-1 sectors. The quality-
passed HFQ dataset retains 149,903 bars and at least 961 observations per stock.
The formal 2019-2025 run was positive in all seven years, all 15 sector-only
universes and all 15 leave-one-sector-out universes. Full return was `315.7504%`,
annualized return `23.5341%`, Sharpe `0.809606`, maximum drawdown `-34.2044%`,
and doubled-cost return `256.1430%`; a 1,000-replication stationary bootstrap
estimated `96.9%` positive-return probability. The polluted 2026 audit returned
`1.9020%` versus `-0.9483%` for the current-universe equal-weight comparison.

Those figures are intentionally not treated as confirmation. The universe picks
today's high-weight CSI 300 members and projects them into years before they were
known, while several extreme sector returns visibly reflect current winners.
HFQ factors may be revised, price-limit execution is absent, and suspended
positions can retain their last close for up to 40 missing market sessions.
The API recomputes every transfer gate from six hash-bound artifacts but returns
`NOT_ELIGIBLE`; the web tab leads with both the passed research gate and these
promotion blockers.

For visualization, the API selects the best exploratory Sharpe only from the
verified comparison report and follows its evidence chain through the immutable
experiment record and deterministic result hash to the equity and drawdown file
hashes. It rechecks chronology, finite positive equity and each drawdown against
the running equity peak before exposing the 632-point curve. The dashboard
states that the chart is the full simulation period while the displayed return,
Sharpe and maximum drawdown belong to the declared 2026 test window; neither is
Champion or promotion evidence.

Strategy health accepts a typed metric snapshot and currently enforces sample,
Sharpe, drawdown, factor-drift, execution-deviation and staleness thresholds.
HEALTHY, WATCH, DEGRADED and HALTED are distinct from the global capital kill
state. A DEGRADED episode emits one process-local deduplicated research task; it
cannot replace a champion or authorize any external execution.

Known boundaries: the experiment runner is an immutable evaluator/artifact
orchestrator, not a full multi-asset analytics engine; filesystem artifacts and
the SQLite catalog are not one distributed transaction; the robustness evaluator
is deliberately small; loop/task state is not yet durable; strategy lifecycle
updates still rewrite the record's `updated_at`; lineage evidence is registered
with the child rather than attached by a later evidence transaction. Lasso,
LightGBM, MLP and temporal models remain downstream work after licensed-data
provenance and baseline validity gates are exercised.
