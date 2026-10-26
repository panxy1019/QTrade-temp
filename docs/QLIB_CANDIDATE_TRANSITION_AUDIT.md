# Later-input candidate transition acceptance

`scripts/audit_qlib_candidate_transition.py` independently checks the D milestone:
one specified trained candidate receives a completed automatic validation on a
genuinely later, verified data version, its proof is registered as STAGED, and
the independently specified ACTIVE model and original approval remain unchanged.
It does not execute a validation, fit a model, activate anything, recover a book,
or enable rolling training. Keep `LIVE_TRADING_ENABLED=false`.

## Existing implementation reused

The existing `audit_qlib_session_transition.py` / `assess_transition` checks C's
version, ACTIVE prediction and signal lineage. The stock forward-transition
auditor checks prospective books. Neither checks the complete D candidate
validation task, proof and registration chain. This tool supplements them; it
does not replace their acceptance scopes.

Reuse the existing `verify_archive`, `verify_bundle`, `verify_version` and
`verify_validation` archive checks, `verify_plan` rolling-plan check,
`inference_contract` lineage/clock contract, and the pure `active_approval`
reader. Historical inference contracts are reconstructed at their authenticated
recorded generation times; an audit after the intended open does not execute
inference or misclassify a historically valid proof as a new current signal.
Validation score/feature Parquet contents are checked for actual coverage,
ranking, count, feature order/float64 precision and session identity.

Existing `observed_session` raw-day replay checks actual collection receipts,
decompressed source bytes, same-vendor kline/quote reconciliation and normalized
Parquet rows for all current symbols in both the training and later version.
The `UnifiedBatchStore` constructor used by this reader only resolves its root;
no capture/commit/write method is called. Version and training archive hashes
also bind the stored historical prefix and source snapshots. This is not a new
full historical-source reconstruction, independent corporate-action audit or
PIT certification. No vn.py gateway/account component is needed at this boundary.

## Required evidence and clocks

Inputs identify the candidate, later version, expected ACTIVE model and expected
approval receipt hash explicitly. The latter two should come from the recorded
human-approved baseline, not be silently selected from whatever is current.
The actual 29589b44 candidate was a controlled operator training run followed by
registration, as recorded by the milestone owner. The tool accepts immutable
training request/summary, fitted bundle, independent restoration and TRAINED
registry event without inventing a scheduled training task. It conservatively
reports `training_origin=UNKNOWN`, `training_origin_certified=false` and
`training_job_verified=false`: origin is not inferred from a matching run name
in a training-job row. Such a row cannot upgrade the result to automatic-training
acceptance. The independently recorded operator provenance remains separate.

Acceptance requires:

1. Candidate model/index/definition, training request, dataset, source snapshot,
   captured code, fitted bundle and independent restoration agree. Restore
   parity, feature/reference identities and no-refit flags must be valid.
2. The training Provider version and genuinely later Provider version pass full
   file verification and are registered with matching READY receipts. The later
   dataset contains a verified signed dataset ancestry declaration binding the
   actual training dataset hash. This does not traverse every intermediate
   version directory. Same-version, same-day,
   calendar-placeholder or still-incomplete future-day substitutes cannot pass.
3. Exactly one matching automatic `QLIB_MODEL_VALIDATION` job and registered
   proof exist for the specified candidate/later version. Its signed identity,
   immutable request, QUEUED → RUNNING → COMPLETE events, actual output proof and
   model-validation index agree. Pending, interrupted, failed, ambiguous or
   missing evidence stays BLOCKED; the auditor never creates a task.
4. Registry TRAINED → VALIDATING → VALIDATED → STAGED events bind the same
   definition and imported validation. The candidate has zero activations and no
   activation time. Generation, version creation/admission, registration, job
   and staging clocks are aware, correctly ordered and no later than the real
   audit clock. The recorded contract must have been generated when that input
   was the latest complete session and before its intended next exchange open.
5. The current ACTIVE and its original APPLIED receipt equal the expected
   baseline. The complete ACTIVE model row is included in both snapshots; its
   signed definition is tied to its TRAINED event and model identity, and its
   activation time matches the approval. The receipt matches its stored command,
   and it remains the last
   global applied model-control event. An A → B → A transition or a new approval
   for the same ACTIVE cannot masquerade as unchanged approval.
6. Actual raw append-day evidence in both versions replays without changing the
   source store. Candidate, validation job/events, registration, ACTIVE/approval,
   version/admission and data-head facts are read again after archive work and
   must equal the original snapshots.

The specified later historical validation is the scope; its version need not be
the current serving data head when the audit is eventually run. The observed head
is reported and checked for changes during replay, but
`current_serving_admission_verified=false` always remains explicit. This audit
cannot authorize current serving admission.

The first candidate validation transition is the scope. Extra candidate state
transitions, duplicate matching jobs or conflicting records deliberately block
this narrow acceptance rather than silently selecting a preferred history.

`schedule_candidate_validation` in the existing worker is independent of the
rolling-policy OFF gate. It considers TRAINED candidates when a current actual
data end is later than their training end. The auditor never enables a policy,
restarts that worker or requests source data to make its own gate pass.

## Read-only behavior and output

SQLite connections use `mode=ro`, `PRAGMA query_only=ON` and a short read
transaction. Only bounded necessary rows are copied into memory, then all three
connections close before costly file/raw replay. A fresh set of short snapshots
at the end detects changed ACTIVE/approval/candidate/task/head facts without
holding a long WAL reader. No OnlineModels, ModelRegistry, DataVersionCatalog,
worker or model-recorder writer is constructed; no pickle is deserialized.

Each scan is bounded to 10,000 selected rows. The supplied archive/index paths
must remain within the base. Signed document authority values have exact types.
The report includes the selected-facts digest, original code identities and the
auditor source hash. Hashes provide content integrity, not operator signatures
or a distributed lock. Snapshots may be externally modified immediately after
the audit ends; this report never authorizes a subsequent state mutation.

The signed `qtrade.candidate-transition-audit.v1` result is either
`VERIFIED_LATER_INPUT_CANDIDATE_NOT_ACTIVATED` or `BLOCKED`. It reports completed
check groups, available evidence and fixed safe error codes. A BLOCKED result
does not claim that unchecked groups passed. The CLI writes one immutable output
outside the base and returns 0 for verified or 2 for blocked. Reusing an existing
output path fails instead of overwriting an earlier observation; choose a new
timestamped output path for each audit. The CLI has no `--now` option. Internal
controlled clocks exist only for synthetic tests and are visibly labeled
`clock_source=CONTROLLED_TEST_ONLY`. Normal CLI runs use `clock_source=SYSTEM_UTC`.

Every report keeps `whole_step1_accepted=false`,
`automatic_training_accepted=false`, and browser, production-publication,
PAPER-forward and historical-PIT acceptance false. A later-input validation is
not an observed forward portfolio return, real execution admission or permission
to replace ACTIVE.

## Reproduction

Focused local checks, with a short Windows temporary path:

```powershell
$env:PYTHONPATH = 'E:/QTrade-git/src'
$env:MYPYPATH = 'E:/QTrade-git/src'
python -m pytest tests/test_candidate_transition_audit.py -q --basetemp=E:/ca1
python -m ruff check scripts/audit_qlib_candidate_transition.py tests/test_candidate_transition_audit.py
python -m mypy --explicit-package-bases --platform linux scripts/audit_qlib_candidate_transition.py
```

Independent Linux acceptance against existing immutable evidence and live
read-only database snapshots:

```sh
LIVE_TRADING_ENABLED=false PYTHONPATH="$PWD/src" \
python -m scripts.audit_qlib_candidate_transition \
  --base /path/to/existing/qtrade \
  --model-database /root/qtrade-local/online-models.sqlite \
  --workflow-database /root/qtrade-local/workflow.sqlite \
  --data-database /root/qtrade-local/qlib-versions.sqlite \
  --model-id <specified-trained-candidate-64hex> \
  --later-version <actual-later-qualified-version-64hex> \
  --expected-active <recorded-human-approved-active-64hex> \
  --expected-approval <original-applied-approval-evidence-sha256> \
  --output /root/qtrade-local/audits/candidate-transition-<actual-check-time>.json
```

Use actual configured database paths, not guessed filenames. The three model,
workflow and data databases may have unrelated physical names. No missing
database is initialized. Do not substitute a future date/version or edit a
pending task to manufacture a positive result. Preserve and report BLOCKED
evidence until the real later-session worker cycle completes.

Tests use actual SQLite, real compressed raw responses and normalized/feature/
score Parquet files, plus synthetic non-executable model bytes. They exercise
archive integrity and transition acceptance without fitting or unpickling a
model. Their synthetic dates and fake model contents are never production
milestone evidence. Production Linux directory fsync and genuine next-session
acceptance require separate actual-cluster reproduction.
