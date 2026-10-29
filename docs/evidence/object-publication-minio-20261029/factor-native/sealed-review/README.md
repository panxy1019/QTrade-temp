# STEP2 factor score -> native Qlib adapter: nonproduction acceptance

The three new files implement an execution adapter for an existing registered factor contract. No existing registry entry, evaluation report, approved source, queue, API, worker, model lifecycle or production setting was changed by this work. No real 891-stock backtest has been run. This milestone validates independent synthetic artifacts and two actual Qlib execution cases.

Source is **exact selected Git f59cc8923bfc247009d2c92f391231bc92333571 plus three explicit uncommitted LF overlays**, not pure Git f59. The Linux package covers all 461 selected files under src/scripts/configs/tests and verifies their bytes before and after execution. `candidate-source/` contains the new files. Exact hashes are in acceptance.json and the package manifests.

| New file | SHA256 |
| --- | --- |
| src/qtrade/research/factor_strategy_backtest.py | 8864f939e86ea2d031b93617a9621aba5f43ad54da5caeff801e0f16fbe492a8 |
| scripts/run_factor_strategy_backtest.py | b6902a2a13f3e13dae1998d7fe0d68d3bc054d0a5943aedf03ee2ccdc2d1a907 |
| tests/test_factor_strategy_backtest.py | e484eff0374a65b93acc4e0fe0e666218dacf7d660f2679fb0044615c0287d5f |

## Implementation and source/gap audit

Reuse the FactorRegistry/evaluator for the three existing single factors and builtin equal-rank composite, existing frozen factor-strategy contract and real StrategyRegistry reader, and existing Qlib comparison fee metrics. vn.py execution/gateway components do not establish research evidence identities and are outside this simulation boundary; none is accessed. No free expression, train/model fit, promotion, PAPER or live authority is added.

The runner resolves exact strategy/version/contract/old registration job; requires EXPERIMENT/NONE; reads a bounded frozen strategy DB into an owned copy before using the writable Registry constructor. It resolves the approved archive and exact READY data, replays only the selected frozen factor through the existing evaluator and compares its full sample and rejection ledger to the saved evidence. Labels remain diagnostic and do not filter feature-valid scores. Late/unknown clocks retain the original knowledge-mode rules and disclosures. Any missing execution day or zero-score day fails closed.

Each decision is scheduled for the next actual READY calendar session inside the frozen test. Scores retain their decision dates because [Qlib 0.9.7 TopkDropoutStrategy](https://github.com/microsoft/qlib/blob/v0.9.7/qlib/contrib/strategy/signal_strategy.py) consumes the previous session's signal. The adapter supplies the frozen Topk/drop/holding policy, next-open price, directional blocks, cumulative capacity, 100-share lot and buy/sell/minimum fees to the [native Exchange](https://github.com/microsoft/qlib/blob/v0.9.7/qlib/backtest/exchange.py). Positive closes require positive adjustment factors so lot rounding cannot silently turn off. An explicit zero-return cash Series is the reference, with no market-index claim.

All provider bytes are copied and verified into a new output root. Qlib and caches use that copy. Original data, strategy DB, factor DB, archive index, report gzip, request and source are checked again before accepting output. New output contains the unchanged old definition, frozen request, scores/clock/rejection disclosure, native portfolio report, positions, existing metrics, execution-source bytes, separately signed result and complete physical manifest. Independent reload checks physical files, immutable registration association and recomputes metrics. Checksums are integrity evidence, not external signatures. Failures create a rejected-output marker, never retry or modify registration.

## Observed validation

- Windows: **30 passed, 3 skipped**, 11.258s, `../windows-short-path.xml`. Two skipped native cases require Qlib absent on Windows; one requires the existing POSIX immutable writer.
- Linux: **33 passed, 0 skipped**, 20.01s, `../factor-backtest-native-20261028T164928Z-3defc87d/linux-actual/linux-results.xml`. Actual Python3.11/Qlib0.9.7 from the specified qlib-env. The two native cases use synthetic raw-price/quote files, actual TopkDropout/Exchange, first-session holdings and real fees; they assert previous-session signal use, directional buy block and 100-share lots.
- One runner end-to-end unit case deliberately replaces only the native backend and explicitly tags `BACKEND_UNIT_DOUBLE_NOT_NATIVE_EXECUTION`; it validates archive/Registry/output immutability and net-cost metric calculation, not native portfolio behavior. The two native tests validate their results in memory; raw native portfolio exports were not separately archived.
- Four independent raw-price score/rank oracles cover every supported factor. Future label changes leave scores unchanged; null labels retain feature scores; late clocks and missing grids have explicit outcomes. Five Registry/archive drift cases stop before the backend. LIVE=true fails closed.
- Ruff for all three files and strict mypy for the two sources pass; exact command/stdout are in static-checks.json.
- Linux gate before: NFS **281107496960 bytes** (>=100GiB); local **17392320512 bytes** (>=2GiB). Source before/after is identical; NFS free bytes unchanged. The subprocess has a 240s budget and the exact frozen fba TERM2/KILL2 process-group cleanup helper, explicit Popen with no context-manager wait.
- Four native warnings are preserved: two MLflow/Pydantic deprecations and two Qlib empty-slice warnings. All finite report/cost/position checks passed.

Failed earlier attempts are retained, not converted into passes: first Linux startup lacked pytest in qlib-env; second isolated pytest copy lacked its py.py shim. Third run copied the existing pure test packages and shim into its own test-vendor directory with full byte hashes; no environment was installed or changed. Windows collection/source-path, fixture-instrument and MAXPATH failures are preserved in their original XML. Only windows-short-path.xml and the final Linux package apply to the frozen three-file candidate.

## Reproduction

Windows from E:/QTrade-git, using a short basetemp to avoid MAXPATH:

```powershell
$env:PYTHONPATH='E:/QTrade-git/src;E:/QTrade-git'
$env:LIVE_TRADING_ENABLED='false'
python -B -m pytest -q -p no:cacheprovider --basetemp E:/QTrade-temp/NEW-SHORT-OWNED-DIR --junitxml=E:/QTrade-temp/NEW-RESULT.xml tests/test_factor_strategy_backtest.py
```

Exact Linux staging/owned execution is reproducible with `../run_linux_native_v3.py` and `../linux_native_driver_v3.py`, requiring the authorized connect capability and the specified existing environment. The client rejects changed overlay hashes, packages Git f59 selected roots and overlays, verifies every remote byte, then creates a unique operator root. It does not read or copy the connect helper's body/credentials. Runtime test command and cleanup-source hash are recorded in owned-child.json; dependencies have test-vendor-manifest.json. No test uses the production queue or actual factor archive.

## Shared-entrypoint integration recommendation

A new FACTOR_STRATEGY_BACKTEST_V1 job should carry only exact registered strategy/version/contract/registration-job/data-version refs, a new immutable job identity and explicit execution-source identity. Existing auth/HMAC queue, shared admission cap, owned-child/cancel/reconciliation and signed completion can be reused after review. The worker must re-resolve Registry/archive/READY physically and never trust client-supplied scores or model spec fields. Use engine FACTOR_BACKTEST and a separate backtest result, preserving FACTOR_REGISTRATION receipts; do not interpret it as a trained model or activation grant.

**Compatibility blocker before worker wiring:** the existing registration definition's exact 20-file runtime fingerprint includes scripts/run_qlib_worker.py. A worker edit would invalidate existing registered contracts under current verification. Define an explicit reviewed execution-release compatibility policy before editing shared entrypoints. Do not relax old validation, rewrite historical contracts or silently re-register them. This candidate leaves those 20 files unchanged. Caller-supplied execution_source_revision is accompanied by exact execution source SHA files; an eventual committed release/queue must bind that revision to those bytes. This test package explicitly labels its overlays.

## Limits and pending work

All performance here is retrospective synthetic research validation. Current-pool selection/survivorship, uncertified historical PIT/vendor adjustments, daily-open capacity/limit proxies, research fees, native Float64/tie order, cash reference and non-liquidated terminal holdings remain explicit. No unseen/OOS or investable performance claim. Actual registered native full-run, real READY data and 891-stock metrics await independent review and the parent's next-stage decision. API/worker/queue integration is not implemented in these three files. No activation, production enablement, PAPER recovery, live brokerage, S3/MinIO or cold publication operation occurred.
