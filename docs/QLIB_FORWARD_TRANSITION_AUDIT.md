# Actual-session forward book acceptance audit

`scripts/audit_qlib_forward_transition.py` checks the book portion of STEP1 for
one genuinely completed exchange session. It complements
`scripts/audit_qlib_session_transition.py`, which verifies data-version,
prediction and signal generation. Neither audit alone accepts the whole STEP1,
browser delivery, historical PIT or external trading readiness.

The forward audit reads existing local ledgers with SQLite `mode=ro` and
`query_only=ON`. It does not construct `ForwardPaperBook`, claim an operational
lease, change ownership, submit orders, recover a book or prepare a recovery
request. It writes only a separate immutable audit JSON selected by the operator.
The CLI has no clock override; it uses the actual UTC wall clock. A future or
unfinished session remains BLOCKED.

## Evidence checked

Expected coverage is exactly twelve books: the five current stock baselines
(`momentum20`, `reversal5`, `lowvol20`, `multifactor_equal_rank`,
`equal_weight_universe`) plus the supplied approved model ID, each in PAPER and
SHADOW. Missing/duplicate/extra local or published coverage blocks acceptance.
The ETF strategy is excluded explicitly because the stock forward adapter does
not implement ETF rules; it cannot be replaced by a stock pretending to be an ETF.

For every book, the auditor checks:

- Hash-derived roster identity and the immutable local account configuration.
- The exact target/session IDs and actual execution at that day's 09:30 Shanghai
  open; generation, durable freeze and admission verification all precede open.
- A signed admission receipt with the correct signal/model/archive identities,
  data event and model approval epoch where applicable.
- A COMPLETE local day receipt, intact serialized input hash and continuity from
  the preceding observed trading session. PENDING or missing receipts fail.
- Frozen market evidence, collector manifests, raw quote/kline bytes and Parquet
  reproduced through the existing source adapter. Prior-volume evidence is
  replayed with the original strictly-before-open availability cutoff. Missing
  securities remain explicit; their constraints never become normal trading.
- Receipt identities and source clocks matching that independently replayed
  market input. Unknown/missing constraints must retain their blockers.
- PAPER orders/fills only for unblocked instruments, durable APPLIED fill proof,
  lot/prior-volume limits, and reconciled cash, holdings, NAV, turnover and return.
  A valid observed all-cash session with zero orders may have zero return; this
  never proves that unknown security constraints are certified.
- SHADOW has no local or durable fills, orders, holdings, cash or portfolio
  return. Hidden operational fill records also fail.
- Public session and frozen-target projections equal the local receipts,
  including bounded detail/truncation fields and archive identity. The supplied
  publication, heartbeat and book summaries must be fresh within twelve minutes.
- Current PAPER must be running/NORMAL in the supplied summary and have matching
  local control history. A paused/halted/stopped model book is an explicit failure
  even if an earlier day receipt exists. Resume after stop does not prove start.

The audit reuses the recorded component source/gap audit in
`docs/QLIB_FORWARD_PAPER.md`: the existing simulation stack and immutable source
verifiers supply this evidence. vn.py gateways neither provide these local
receipts nor repair uncertified market constraints, so no gateway is introduced.

## Reproduction

Run after the actual selected day's completed receipts are recorded and the
public forward snapshot has caught up. `MODEL_ID` is the independently selected
approved model identity, not a value inferred from whichever books happen to
exist. Pass the actual downloaded/public snapshot file to verify publication
delivery; passing the local publisher input checks only that supplied input.

```sh
export PYTHONPATH="$PWD/src"
export LIVE_TRADING_ENABLED=false
python -m scripts.audit_qlib_forward_transition \
  --base "$B" --local-root /root/qtrade-local/forward-research \
  --published-forward "$PUBLISHED_FORWARD_JSON" \
  --approved-model-id "$MODEL_ID" --day 2026-10-27 \
  --output "$B/workflow/acceptance/forward-20261027-audit.json"
```

The output must be outside the book root and immutable market lake. An existing
output is never overwritten. State is PASSED only if all global and book checks
pass. Otherwise BLOCKED includes safe failure codes for the global gates and
each affected strategy/mode; it does not silently omit failed books. File/input
configuration errors may terminate the CLI before an audit can be written.

Every output keeps `whole_step1_accepted=false`,
`historical_pit_certified=false`, `browser_verified=false` and all original
research-only authority flags. A supplied snapshot is not proof of a production
URL or visual browser acceptance. Content hashes prove integrity, not independent
timestamps, historical source availability or broker execution. This document
contains no claim of actual production forward receipts or user recovery approval.

```powershell
$env:PYTHONPATH='E:/QTrade-git/src'
$env:MYPYPATH='E:/QTrade-git/src'
python -m pytest tests/test_forward_transition_audit.py -q --basetemp=E:/forward-transition-tests
python -m ruff check scripts/audit_qlib_forward_transition.py tests/test_forward_transition_audit.py
C:/ProgramData/anaconda3/python.exe -m mypy --platform linux --follow-imports=silent scripts/audit_qlib_forward_transition.py
```

The twenty focused tests use synthetic ledgers and real raw/Parquet replay.
They verify twelve-book coverage, read-only filesystem preservation, pending/
missing/backdated/tampered local evidence, fabricated fills/returns, paused model
books, stale/changed publication, raw tampering, clock gating and immutable CLI
output. Synthetic test clocks and results are never production acceptance.
