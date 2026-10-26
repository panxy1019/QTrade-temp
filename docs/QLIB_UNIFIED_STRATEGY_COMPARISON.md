# Unified historical strategy comparison

This STEP1 artifact compares seven historical research series using immutable
Alpha158 Ridge and LightGBM archives. It does not train, activate a model, submit
orders, or claim forward returns. `LIVE_TRADING_ENABLED=false` is enforced.

## Source and reuse audit

Reuses the existing signed baseline archive verifier, Provider bytes, causal
`signal_baselines.signal_family`, and native Qlib 0.9.7 `TopkDropoutStrategy`,
`WeightStrategyBase`, `backtest_daily`, and `Exchange`. Source reviewed:
https://github.com/microsoft/qlib/blob/v0.9.7/qlib/contrib/strategy/signal_strategy.py

The six ranking strategies (momentum20, reversal5, lowvol20, equal rank
multifactor, Ridge and LightGBM) share exactly the archived TopkDropout settings.
The seventh, equal-weight-all, intentionally uses WeightStrategyBase, with the
same cash exposure, execution dates, costs and Exchange constraints. It is an
allocation control, not an identical portfolio-rule comparison. It has daily
rebalancing and no TopkDropout minimum-hold parameter. Daily rebalance means
previous-session holdings are settled in this diagnostic, but this is not a
certified settlement ledger. vn.py gateways cannot supply historical PIT or
source lineage and are not needed for this offline comparison. No engine is
rebuilt or gateway accessed.

## Admission and common panel

Both archives must pass every recorded checksum and an exact file inventory;
symlinks and extra files are rejected. Models must respectively identify Ridge
and LightGBM. Dataset, source snapshot, training code, full Provider inventory,
train/validation/test segments, label, account, benchmark, portfolio, exchange,
universe and source-constraint policy must match. Constraints must be present.
Incompatible fixed/rolling archives are rejected, not silently intersected.

Predictions are loaded from immutable OOS Parquet, never by unpickling a model
or refitting processors. Every test session must have both model score sets.
Each session uses exactly the previous 21 sessions including the current close;
no forward fill. Only symbols with complete positive prices and both model
scores enter any family. Multifactor ranks are computed on this common pool.
Missing whole sessions, nonfinite/duplicate scores, empty panels and differing
execution calendars fail closed. Native Qlib consumes previous-session signals
at the next open: the first backtest date may therefore hold cash. All families
have the same date boundary. Label availability never controls signal admission.

## Reproduction

Use the cluster Qlib 0.9.7 environment and committed source:

```bash
export LIVE_TRADING_ENABLED=false
export PYTHONPATH="$PWD/src"
python -m scripts.run_qlib_strategy_comparison \
  --ridge-run "$RIDGE_RUN" --lightgbm-run "$LIGHTGBM_RUN" \
  --output "$COMPARISON_ROOT" --source-revision "$(git rev-parse HEAD)"
python -m pytest tests/test_strategy_comparison.py -q
```

The input archives currently nominated for cluster integration are
`float64-alpha158-linear-20261026-0ab51f95e3178a27` and
`float64-alpha158-lightgbm-20261026-7d31f41cbdf65d62`.
Their actual protocol equality is checked by the runner, not inferred here.

## Output and integration

`<output>/<comparison_id>/manifest.json` binds every output and source file.
`comparison.json` contains seven metric/curve series, exact protocol and input
identities, session range and common-row count. Each family has native daily
report Parquet, holdings/cash JSON and daily IC diagnostics JSON. Shared scores
and labels are persisted as Parquet. Positions are simulated historical units,
never current PAPER holdings. IC and RankIC for a constant equal-weight signal
are null rather than zero. Net returns deduct Qlib cost; drawdown includes the
initial unit capital. Failed runs remain `.partial`; completed targets are
immutable and verified before idempotent reuse.

The worker/publication integration should expose only `verify_comparison(root)`
results. A new data version does not modify a completed historical experiment.
Public `state=COMPLETE_DIAGNOSTIC` is research completion, not capital admission.
Frontend must retain every experiment's limitations and distinguish the
allocation control. Tests exercise causal no-future-input behavior, missing
sessions, bad prices, costs, initial loss, constant IC, protocol mismatch and
archive tampering. Real native cluster execution is a separate acceptance gate;
local tests do not certify it.

## Known limits

The current pool has survivorship and historical screening bias. Historical PIT,
information availability, corporate-action reconciliation, exact daily limits,
auctions and impact remain uncertified. Source constraints are diagnostic
additional restrictions and conservative proxies, not proven historical trading
permission. ETF data is absent and explicitly unavailable. Post hoc OOS is not
preregistered forward performance. PAPER/SHADOW accounting and next-session
acceptance are separate STEP1 artifacts.
