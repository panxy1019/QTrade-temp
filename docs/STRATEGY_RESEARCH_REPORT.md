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

The rejected candidate also has a bounded 14-scenario diagnostic covering cost,
parameter, subperiod and deterministic current-universe perturbations. It finds
a worst Sharpe of `-1.1429123433`, Sharpe dispersion of `1.6703725093`, and at
least one negative-Sharpe scenario. These scenarios reuse already observed data
and retain current-universe
survivorship bias, so the result is diagnostic only and cannot be counted as a
fresh held-out test or promotion evidence. Its signed envelope is verified by
the API before the Chinese research page displays it.

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
