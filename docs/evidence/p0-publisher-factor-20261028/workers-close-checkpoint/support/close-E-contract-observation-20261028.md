# Read-only October 28 execution-day contract observation

Observed 2026-10-28 14:50-14:57 Shanghai. This is an engineering diagnosis and a
possible future contract-review input. It changes no code, gate, policy, model,
target, admission, service, queue or production state.

## Actual immutable evidence

- `cluster-evidence/close-readiness-20261028T065020353279Z.json`: ACTIVE
  `29589b4486a09159eb3a07153c54fba609fbc80b52c168dd665cd46660be8487`, head
  `09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407`, 14 books,
  zero October 28 sessions, two retained retirement proofs, unchanged current
  context, forward owner 383859, instance `74972257947a44dea8ca686421b5ad76`,
  source `9846a7f30774d6ae4b4d41f02b493c5246be87d9`, LIVE false.
- `cluster-evidence/stock-public-20261028T065057Z-516c24b9/`: raw public stock
  forward wrapper, response headers/clocks/body hashes, equal before/after
  actual main `ae33a7c8c27ea721eb3be1e4430b562a44b67f91` deployment, and the
  extracted signed inner publication. Publication at 14:49:02 Shanghai is
  fresh but DEGRADED with `FORWARD_SERVICE_PREDICTION_UNAVAILABLE`.
- `cluster-evidence/close-prediction-diagnosis-20261028T065558355784Z.json`:
  online predictor is `BLOCKED_EXECUTION_WINDOW_PASSED` and
  `current_prediction_id` is null; durable relevant prediction tasks remain
  COMPLETE with no error. Actual rolling policy is OFF.
- The same diagnosis checks all 19 files of original frozen prediction
  `1e5ea1db77d0d9ef7c6ae5651c7d0b8ed9ef5cc4c87efca0d740fcac35fcd6b2` for each
  current-model book. Manifest hash and approval equal their signed admissions.
  The frozen target is `c8c591195b1ea75b0fa48b58161dab9d3018d48d4d9317e9b21b8aefb3e3bac0`
  for October 28 09:30. Its original source is
  `c382453b5eba6c6441646ab70c76131f486cb509`, not the later workflow source.
- Existing auditor run at 14:57:52 is in that raw public directory as
  `E-private-20261028T065752117254Z.json` and `E-binding-20261028T065752117254Z.json`.
  Actual result is BLOCKED: real session not complete, publication degraded,
  all 12 October 28 observations absent. Both retirement proofs pass. Auditor
  bytes match Git source 9846a7f. No production mutation or remote evidence write.

## Minimal causal mismatch

`OnlinePredictor.context` refuses a prediction once its expected execution
window has passed. `publication` sets the current pointer only for a COMPLETE
prediction whose current bindings match and whose deadline is still future.
This is appropriate for admitting a new prospective target.

`ForwardService.current_targets` turns approval present/current prediction null
into `FORWARD_SERVICE_PREDICTION_UNAVAILABLE`. The service aggregates that into
DEGRADED. Independently, the same cycle iterates all retained books and invokes
`observe_book` for nonretired books; discovery failure does not skip that loop.
The first admitted target and source remain immutable and are the correct
execution-day input, even when the newest prospective pointer has expired.

The existing E v2 auditor requires the supplied public service state RUNNING
and errors empty in addition to every book's raw replay, accounting, target,
admission, public projection and retirement proof. Therefore all 12 per-book
observations may later pass while the aggregate E remains BLOCKED on discovery
degradation. Preserve this result. Do not alter the publication or auditor.

## Actual clocks and conditional natural resolution

The auditor's complete-session check begins at the real 15:00 Shanghai close.
`Calendar.expected(now, 'daily')` rolls to today's date only at 16:30 Shanghai.
`observe_book` returns before that date gate, so existing automatic October 28
sessions cannot appear before 16:30 even if raw close data is earlier available.

After a real October 28 READY head is admitted and the clock is at least 16:30,
the current approved model may automatically generate an October 29 prediction;
model promotion is not required for that route. Once complete and current,
discovery can clear naturally. This is conditional. The existing worker calls
`schedule_model_promotion` before `predictor.tick`, so no ordering guarantee says
that a new prediction will finish before an eligible model promotion. Never
wait for new C/D merely to begin recording execution-day E evidence.

## Possible future contract review, not an implemented acceptance rule

An independently reviewed future interface could express fixed execution-day
observation readiness separately from prospective next-target discovery health.
It would preserve the current overall DEGRADED state and full diagnostic history,
bind observation proof to the execution-day model/target/admission/source, keep
all risk/runtime/reconciliation and retirement gates, and disclose unavailable
next-target discovery. It must define exact public schema and audit semantics
before any adoption, test expired discovery against valid frozen observations,
and test actual failures, missing/changed admissions, model changes, stale public
delivery and incomplete accounting. No existing receipt may be relabeled.

Current production and the current E v2 contract remain unchanged and strict.

## Actual first post-close capture

Fresh HTTP/deployment/inner capture at 15:00:41 and existing E audit at 15:00:57
are in `cluster-evidence/stock-public-20261028T070041Z-ce3c4965/`, including
`E-private-20261028T070057440907Z.json` and
`E-binding-20261028T070057440907Z.json`. The actual real-close gate clears;
the audit remains BLOCKED on the DEGRADED public publication and all 12 missing
October 28 sessions. Both retained retirement proofs still pass.

At 15:01:35, `cluster-evidence/close-collection-clock-20261028T070135133414Z.json`
records the actual expected daily date as October 27 and no October 28 daily
collection tasks. October 28 five-minute tasks are 1496 SUCCEEDED / 4 FAILED;
this is not completed daily or whole-market acceptance. The running forward
auditor/service/calendar bytes match their exact Git 9846a7f objects; the running
workflow predictor/worker bytes match their exact Git 096387b objects. These
five targeted checks avoid a broad NFS scan.

## Reproduction

New actual raw stock capture plus genuine existing read-only E audit, actual
UTC, immutable desktop evidence, no remote files or production writes:

```powershell
python -B E:/QTrade-temp/capture-close-E-ro-20261028.py
```

Bounded preflight only:

```powershell
python -B E:/QTrade-temp/close-readiness-ro-20261028.py
```

Retain BLOCKED until actual gates pass. Never replay handover/recovery, change
the clock, replace the first target, or count retired closure as today's data.
