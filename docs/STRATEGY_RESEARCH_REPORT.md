# Strategy research report

QTrade v0.2 now treats strategies as durable, versioned entities rather than
scripts. The registry records immutable specifications, parent/child lineage,
controlled lifecycle transitions, champion/challenger roles and append-only
transition history. Human authorization is required after SHADOW for APPROVED and
CHAMPION; no LIVE state exists.

Implemented baseline families are buy-and-hold, moving-average trend, ETF momentum
rotation, ETF trend plus volatility/risk scaling, and cross-sectional factor
ranking. Experiments bind git commit, data version, PIT universe, chronological
train/validation/test split, model, parameters, cost model and seed to immutable
hashed artifacts.

Promotion is multi-metric and fail-closed. It includes OOS return, Sharpe, Sortino,
drawdown, Calmar, turnover, transaction costs, exposure, stability, parameter
sensitivity, walk-forward consistency, data quality, embargo and PAPER/SHADOW
observations. Outcomes are PASS, REJECT or NEEDS_REVIEW; a single high Sharpe can
never promote a strategy.

The research loop is bounded and hypothesis-led. Every hypothesis records economic
intuition, regimes, failure modes, required data and evaluation criteria. Candidate
and iteration budgets prevent brute-force search; held-out test windows cannot be
reused for candidate selection; an independent leakage-review pass is mandatory.
Robustness scenarios are predeclared and bounded and include parameter neighbours,
cost stress and regimes with deterministic hashes and cliff detection.

Strategy health monitors rolling performance, volatility, drawdown, turnover,
cost, exposure, signal/factor drift, IC and execution deviation. HEALTHY, WATCH,
DEGRADED and HALTED are distinct from the global capital kill state. A DEGRADED
episode emits one deduplicated research task; it cannot replace a champion or
authorize any external execution.

Known boundary: the current baseline evaluator remains deliberately simple and
does not claim production-grade multi-asset matching or complex ML. Lasso,
LightGBM, MLP and temporal models remain downstream work after the baseline,
robustness and real-data provenance gates are exercised on licensed data.
