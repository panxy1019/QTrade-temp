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
hashed artifacts.

Promotion is multi-metric and fail-closed. It includes OOS return, Sharpe, Sortino,
drawdown, Calmar, turnover, transaction costs, exposure, stability, parameter
sensitivity, walk-forward consistency, data quality, embargo and PAPER/SHADOW
observations. Outcomes are PASS, REJECT or NEEDS_REVIEW; a single high Sharpe can
never promote a strategy.

The current research-loop contract is bounded and hypothesis-led. Every hypothesis records economic
intuition, regimes, failure modes, required data and evaluation criteria. Candidate
and iteration budgets prevent brute-force search; held-out test windows cannot be
reused for candidate selection; an independent leakage-review pass is mandatory.
Robustness scenarios are predeclared and bounded and include parameter neighbours,
cost stress and regimes with deterministic hashes and cliff detection.

Strategy health accepts a typed metric snapshot and currently enforces sample,
Sharpe, drawdown, factor-drift, execution-deviation and staleness thresholds.
HEALTHY, WATCH, DEGRADED and HALTED are distinct from the global capital kill
state. A DEGRADED episode emits one process-local deduplicated research task; it
cannot replace a champion or authorize any external execution.

Known boundaries: the experiment runner is an immutable evaluator/artifact
orchestrator, not a full multi-asset analytics engine; the robustness evaluator
is deliberately small; loop/task state is not yet durable; strategy lifecycle
updates still rewrite the record's `updated_at`; lineage evidence is registered
with the child rather than attached by a later evidence transaction. Lasso,
LightGBM, MLP and temporal models remain downstream work after licensed-data
provenance and baseline validity gates are exercised.
