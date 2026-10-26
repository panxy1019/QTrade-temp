# Rolling child failure retains the approved ACTIVE model

The missing STEP1 Phase D failure-isolation evidence now has a focused integration
test in `tests/test_rolling_failure_isolation.py`. No production state, network,
training policy or activation was changed. This is isolated acceptance evidence;
it is not a newly observed production training failure.

## Evidence mapping

| Requirement | Executed evidence |
| --- | --- |
| Start with an explicitly approved ACTIVE model | Real `ModelRegistry.register`, `record_validation`, and `apply(ACTIVATE)` transactions on an isolated SQLite model ledger; the actual `active_approval` reader resolves the APPLIED receipt |
| Exercise the worker's failed rolling child path | Real `Worker.once` → `execute` → `Popen` → child nonzero exit → durable `update(FAILED)`; job updates, workflow transactions and model registry transactions are not mocked |
| Native entrypoint failure | Actual `python -m scripts.run_qlib_rolling` subprocess rejects an invalid Provider inventory before training; its status becomes FAILED and its private runner log retains the reason |
| Failure at the training boundary | A real subprocess runs the native rolling main, `run_candidate`, mature-window plan and config construction; controlled Provider verification and fit-boundary injection then raise `TEST_ONLY_ROLLING_FIT_FAILURE` after a durable TRAINING_MODEL marker |
| A partial candidate cannot become current | The failing fit leaves a partial candidate summary with a different model ID; no model row, validation, approval or promotion is created for it |
| Preserve ACTIVE and its approval exactly | Exact stored rows from models, validations, active head, command receipts, model events, prediction tasks and prediction events compare equal before/after; `active_approval` and the original command receipt remain identical |
| Keep a durable FAILED task | Workflow row has FAILED, no result, and `QLIB_RUN_FAILED_SEE_PRIVATE_CLUSTER_LOG`; events end in FAILED and never COMPLETE; rolling publication exposes the same failure and `automatic_activation=false` |
| No silent retry after failure/restart | Reopening the real Worker and independent SQLite connections retains FAILED and the attempted day; scheduling that same day again returns no task |
| Preserve live safety and credential isolation | Child environment has `LIVE_TRADING_ENABLED=false`; every `QTRADE_RESEARCH_*` environment variable is stripped before launch |

The integration test substitutes only the empty outbound Git queue, publication,
prediction ticking, clock/resource fixtures, short polling, and the explicit
training injection. Windows fixture creation also substitutes Linux directory
fsync and `/proc` process-marker observation. It uses real child processes,
workflow/model/data SQLite files, approval transactions, rolling scheduling,
failure handling and restart behavior. The controlled fit case does not require
Qlib installation or perform a real optimization. The native preflight case may
fail at the existing 100 GiB disk reserve gate on smaller test machines.

## Reproduction

```bash
export LIVE_TRADING_ENABLED=false
export PYTHONPATH="$PWD/src:$PWD"
python -m pytest tests/test_rolling_failure_isolation.py -vv
python -m ruff check tests/test_rolling_failure_isolation.py
```

Both scenarios pass locally with actual subprocesses and no skips. Related
registry, rolling-control and worker regressions should be run with the broader
integration suite on Linux. A clocked registry snapshot is not used for exact
comparison because its observation timestamp changes; stored rows and the
original signed approval receipt are compared instead.

## Production-clone feasibility audit

A production-backed clone is feasible without stopping, opening a writer against,
or changing production. The worker already produces consistent SQLite backups:

- `<base>/workflow/catalog-backup.sqlite` for workflow tasks/events;
- `<base>/online-research/control/catalog-backup.sqlite` for model, approval and
  prediction state.

The data head has a separate SQLite catalog; obtain a consistent backup using a
read-only SQLite source connection and `Connection.backup` into a new scratch
destination. Read the active model, current APPLIED approval event, data version,
manifest digest and Provider calendar from these snapshots. Hash the snapshots
and selected immutable files before the clone run, then report those bindings
with the result. No production credentials or copied secret-bearing environment
are needed for an empty test queue.

Create all clone databases, archive paths, outputs and logs under a fresh scratch
base. SQLite `process_owner` rows must be cleared or re-seeded **only in those
scratch copies**, because they refer to the still-running production owner. Never
construct `Worker` or `ModelRegistry` with production database paths for a
read-only audit: their constructors claim process ownership and can mutate
recovery state. Isolate new clone jobs from old QUEUED/RUNNING production jobs so
constructor recovery cannot follow copied child PID markers or signal a real
process. Preserve the original source backups as immutable evidence.

Use the controlled fit exception on the clone, with the subprocess input/output
arguments checked against scratch paths. The existing Worker command has a fixed
tracking argument `/root/qtrade-local/workflow-mlruns`; the test's controlled fit
raises before any tracking backend is opened. A clone acceptance must preserve
that early controlled failure or redirect tracking inside its test-only subprocess
adapter. Supplying a valid Provider to an unrestricted native rolling command
could start real training and use that tracking path; it is not the proposed
failure-isolation reproduction.

This task implemented the reproducible isolated scenarios and inspected the clone
path; it did not fetch production backups or execute a production-backed clone.
That additional acceptance should name the exact backup hashes, ACTIVE model ID,
approval receipt hash, failed task ID, source revision and unchanged before/after
ledger rows. Neither synthetic fixtures nor a clone demonstrate later production
prediction availability, real-model training quality, PAPER returns or completion
of the actual next-session gate.

## Core-path finding

No core fix was needed. A nonzero rolling child exit records FAILED and returns
before archive verification or `OnlineModels.import_trained`. Successful candidate
import registers a TRAINED candidate; model activation remains a separate explicit
control transaction. These tests bind that separation to durable failure and
restart evidence rather than only testing the registry in isolation.
