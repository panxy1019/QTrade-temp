# Final scope-specific rollout review

Date: 2026-10-28. Independent read-only review. No operator, repository source,
cluster service, policy, price store, book or recovery action was modified or
executed by this reviewer. This file is the only review output written.

Reviewed operator: E:/QTrade-temp/graded-quality-rollout-20261028.py.

- Stage revision SHA256: a90e0aa7c7cb940c46499b3c836e4fa47765a1f39ed3b6d4bfa1cf3d8ad5e9c3.
- Data-action revision SHA256: e16497d482e79ddbb0f843345e4ca05b0cf157d4fe2d2c38be2307976c0f0ded.
- Latest reviewed/status revision SHA256: 3dd14200cc7a8a6db9aa6cdcbf8528795cfb8ea3f1be3f1b21ac0cca117acdf7.
  This includes the status vendor lease, persisted before snapshots, final data
  request-absence check and bounded Supervisor Starting-state poll.
- Client SHA256: 4e56b0c376c251e9e06431256d6f35e5c70e6dc152950eeea8e8e366a8af37dc.
- Bound source: 458552de1fea1faf77d02dcd9ec69251a0c364b7.
- Source manifest SHA256: 4b6b6ae1f8b2db717c1ffb075e7e2e073eb084a0a2b05f3bdc9c8729439d15be.

Both operator/client ASTs parse. The existing Linux XML has464 tests, zero errors
and failures, and exactly the documented Windows-specific skip:463pass/1skip.
This reviewer inspected that evidence; no second full test run was performed.
The user's two existing approvals are accepted as supplied by the parent; this
review introduces no new approval request.

## Scope decisions

| Action | Decision | Scope |
| --- | --- | --- |
| stage | GO, now separately evidenced | Diagnostic plus two immutable policy writes only |
| data | Source switch observed; read-only reconciliation only | Original post-start assertion failure remains; do not replay the old-PID action |
| status | Source switch observed; old owner was killed | Lease protected vendor work, but actual Supervisor stop was non-graceful; do not restart again to repair evidence |
| maintenance | HOLD | Natural exit and residual background refresh safeguards remain incomplete |
| publisher | BLOCKED | Real45-second cold/warm export failures; no publication approval |

These decisions do not accept current prediction, public delivery, a real close,
browser rendering, PAPER accounting or wholeSTEP1. No forward383859 restart,
ACTIVE295 change, frozen-book edit, rolling enablement or live trading is in scope.

## Stage: cleared independent of service replacement

The fixed input inventory is limited to issuer-starts.json, three issuer PDFs and
three supplemental status files. File content, size, bounded resolved source path
and absence of source symlinks are checked before copying. Diagnostic output and
the two explicit approval writes stay under data-quality; there is no old-PIT,
price, frozen-request, model, forward, recovery or service-control write in stage.

The operator checks exact436-file source inventory, source hashes, no unbound
source files, reviewed module origins, source revision and Linux result shape.
The policy explicitly includes DAILY_RESEARCH, FACTOR_EVALUATION and PREDICTION.
Retry controls retain exact-day/source/failure-row/expiry checks in the reviewed
consumer; staging does not itself claim that any retry was deferred.

The locally synchronized stage-035119916031.json records actual03:51:19UTC,
scope_stock_count990, quality cases12SUSPENDED/3DATA_GAP/4UNKNOWN,480 content-verified
recent status symbols out of990, owners_unchanged=true, consumers not yet upgraded
and whole_step1_accepted=false. This is independently inspectable local evidence,
not a fresh direct cluster observation by this reviewer.
Its self-hash0d199166acce7ea3fed59abcc685aadc5afb0335c4d523d1e932bd04a51f9dec
was independently recomputed successfully.

The stage implementation derives the symbol hash from the then-current admitted
dataset. Its script does not independently pin a predeclared990-symbol digest or
compare model/data context before and after stage. The concrete stage receipt
confirms990, and current head/universe/scope must remain part of the external live
preflight. This generic-script limitation does not invalidate the reviewed
concrete stage or authorize staging a different future scope without review.

## Data: cleared narrow handoff

Latest lines235-237 and325-326 reject an October28 frozen request both before
preparation and immediately before stop. The data parent must have no child at
that boundary, and actual Supervisor PID and start_ticks must match the old
parent. A preexisting frozen request is a stop condition; do not delete, rewrite,
relabel or finish it under a replacement source.

The existing data parent catches SIGTERM with a stop Event and explicitly reaps
its cycle process group. The no-child requirement therefore avoids an active
preparation/build handoff. Dynamic owner discovery now removes matching direct
children through ancestry rather than treating every same-module process as an
owner; a normal parent plus its --cycle child has one parent owner. Multiple root
owners still fail closed. The script does not literally filter the --cycle flag.

The replacement candidate and private original are fsynced/chmod0600 before stop,
outside Supervisor conf.d. Only the stopped named group is removed/added after
the preserved nonstandard reread receipt. Verify actual new PID/start_ticks,
Supervisor PID, command revision, cwd, PYTHONPATH and LIVE=false. Other owners,
ACTIVE/approval/head, frozen targets/admissions/sessions/fills, legacy status
bytes, rollingOFF and forward instance are compared after replacement.

A completed source switch is not evidence of a subsequent automatic data cycle.
Capture a new automatic cycle and, after real close, the immutable request and
new version with its policy hash. Preserve any failure and continue from concrete
state rather than replaying the old fixed-PID action.

Actual data-action context: the original attempt failed after start because the
Supervisor status still said Starting without a PID. The mutation had already
occurred. The locally synchronized read-only-data-035302537441.json subsequently
shows Running493083, exact458552d argv/cwd/PYTHONPATH, LIVE=false and a fresh
automatic WAITING heartbeat with last_returncode0 at03:52:56UTC. Reread exit1,
remove0/add0 and the original attempt exit1 remain retained. These observations
support the new running source and one completed cycle, not an invented fully
successful original operator attempt.

The old data pre-upgrade receipt did not persist before_owners, before_context or
before_frozen_controls. The assertion failed before the in-memory comparisons
ran. A fresh after-state cannot recreate the missing detailed before snapshot;
do not claim a persisted exact before/after frozen-book equality for this action.
Keep this audit limitation explicit. Latest3dd14200 now durably includes all
three before snapshots for subsequent actions and polls Starting for up to15s.
No further data stop/restart is recommended to repair an evidence limitation.
The later signed observation-040020307616.json contains the actual11:52:34
Supervisor event fragment exit status0 for qtrade-qlib-data. This supplies normal
old-data-owner exit evidence separately from the retained failed operator attempt;
it does not restore the missing detailed before-book snapshot.

## Status: concrete safe lease plan

The earlier a90e0aa/e16497d upgrade('status') reached stop without a status-pass idle guard. The
reviewed deployed observer configuration has stopwaitsecs45. In
scripts/collect_recent_status.py, collect_pass processes20 new plus up to5 retry
symbols and does not inspect the main stop Event between SDK queries. Consequently
SIGTERM alone does not guarantee exit within45seconds; that earlier operator could
force an active pass to be killed. Absence of OS child processes proves nothing
about this in-process SDK work.

Reuse scripts/vendor_session.py: baostock_session(blocking=False) opens
/root/qtrade-local/baostock-session.lock and takes LOCK_EX|LOCK_NB. collect_pass
holds that exact lease from before anonymous vendor login through vendor logout.
Latest3dd14200 now wraps the complete _upgrade('status') in this exact existing
nonblocking lease and requires next_pass_delay>60 before entering it. The signed
coverage check therefore requires a fresh pointer with more than60seconds until
the normal next pass. No new lock path or service code is needed. The arrangement
is equivalent to:

```python
try:
    with baostock_session(blocking=False):
        # Revalidate same PID/start_ticks/Supervisor identity, source, LIVE=false.
        # Require next_pass_delay(B, now)>60 after validating signed coverage.
        # Preserve coverage/attempt identities; verify no incomplete active pass.
        _upgrade('status')
except BlockingIOError:
    # Seal WAIT_FOR_EXISTING_VENDOR_SESSION; do not stop or retry early.
    pass
```

The actual implementation lets lease-busy/freshness failure exit before any
service mutation; a special WAIT receipt is a suggested presentation, not a
condition for safely refusing the stop. The lease wrapper excludes vendor queries
during handoff and remains held through all _upgrade postconditions. It does not
validate the Supervisor's effective stopping signal or guarantee graceful process
exit. The earlier safe-stop GO was too broad because deployed process-control
behavior had not been verified before stop; that recommendation is withdrawn as
a clean handoff recommendation.

Keep the lease open continuously across the last guard, stop and proof that the
old owner is gone; keeping it until the new owner proof also prevents a new
watcher from initiating a query during handoff. Acquire nonblocking and yield if
busy, preserving the existing vendor owner's work. Do not acquire the observer's
owner lock while it is alive, clear any lease file or reset query attempts.

The lease excludes active/new SDK queries. It does not stop local post-query
coverage publication, so retain the fresh-pointer/pass-state check and observe
clean exit. A clean handoff claim additionally needs Supervisor's actual exit
reason/status or equivalent sanitized process-exit evidence that distinguishes
normal completion from SIGKILL. PID disappearance alone is insufficient. If such
exit evidence is unavailable, report the precise limited observation instead of
asserting graceful completion. Release the vendor lease promptly after handoff.

The locally synchronized completed-status-035603049591.json records old466733
to new493858 at03:56:03UTC, source458552d, other owner/context/frozen checks passed,
LIVE=false and functional_observation_still_required=true. Its detailed before
snapshots are persisted. Its self-hashba8453f41574e2db5f1bd3a5b7fbe5bf73765e86b61df4f6456045ef3cf22c9a
was independently recomputed successfully. This supports the recorded source
switch and scoped preservation assertions. The later actual exit evidence below
establishes that the old status owner did not exit gracefully.

### Actual status failure and read-only correction,04:01UTC

Signed observation-040020307616.json, observed04:00:20.529563UTC, independently
passes self-hashb430100bb47703e5f1d5d61305f5037af6530a293547e6cfbf2aaf9549a54009.
The actual11:55:56 Supervisor events for qtrade-recent-status contain:

- signal=killed, source line SHA2563ff10e4882c122374315e444e659dd619d95c33bec41fea54bb5a27c23d4eed3.
- signal: killed, source line SHA256ea175b391a84779a36c9531c1085148108754c73ce16ea722f13bb7af51f1d35.

The old status owner was killed. The evidence's mentions_sigkill=false field is
only a failed literal-text classification: neither fragment spells SIGKILL.
It must not be interpreted as no forced termination or normal exit. Preserve
the original signed observation and add the interpretation; do not rewrite it.

An independent authorized SFTP read at04:01:47.797832UTC inspected only the
following three public process-control fields from the private old backup and
current status config. Full config text and environment were not printed,
logged or saved by this reviewer:

| Status config | SHA256 | stopsignal | stopwaitsecs | stopasgroup |
| --- | --- | --- | --- | --- |
| Old private backup | fe62f0e34811bb5658fb0859f37327822807c5ee2c80be6f7da2268936cf9146 | absent |45| absent |
| Current config |32c983e94243ba306d143e44d27777bad1d2abc81d4286c36a5a4d6007a1fcd5| absent |45| absent |

There is no explicit stopsignal=KILL in either file. Do not invent that cause or
assume another Supervisor implementation's default. Actual installed Go
Supervisor effective/default stop-signal semantics require a separate audit.
The operator read the config but never checked its stop signal, process-group
behavior or effective runtime interpretation before invoking stop.

The same latest observation records current source458552d recent coverage520/990
at03:59:05.646723UTC and regenerated quality at03:59:05.661214UTC. The replacement
observer runs and the recorded owner/frozen-control checks passed under the
vendor lease; those facts do not turn the killed old owner into a graceful
handoff. Retain the separate functional progress and non-graceful-stop results.
No current status restart or configuration mutation is recommended or performed.

After the new observer runs naturally, verify new coverage/quality using original
per-observation source identities. Cached480 records must not be relabelled as
newly queried by458552d. Full990 and a later window remain separate acceptance.

## Maintenance: keep on hold

The final WAITING/fresh heartbeat/no-child/no-RUNNING-batch recheck substantially
narrows the previous race. However, latest lines335-338 only prove old PID disappearance
and zero RUNNING collection batches after stop. They do not prove graceful exit
or absence of a residual refresh_current_datasets child.

The existing unified_loop has a ThreadPoolExecutor future. A submitted refresh
can begin after the last no-child observation; the context manager waits for it
when leaving the loop. refresh() may run its subprocess for900seconds and does
not receive the stop Event. It is therefore unsafe to treat an ordinary short
Supervisor stop timeout as guaranteed natural completion. There is no shared
refresh lease in the inspected source that the operator can simply reuse.

Do not deploy maintenance under the current operator. The actual status kill
additionally requires review of the installed Supervisor's effective stop signal,
timeout and process-group semantics before any maintenance stop. A concrete reviewed
handoff must prevent another refresh submission while draining the existing
future, preserve the active collection pass, allow existing work to exit
naturally, and capture exact owner exit reason plus zero residual descendants
and zero RUNNING batches before the replacement starts. Establish that mechanism
against actual Supervisor behavior first. Repeated idle snapshots alone do not
close the race; do not kill a legacy refresh to obtain a green receipt or change
the45-second export/resource budget to solve this shutdown problem.

## Publisher: remains blocked

The inspected no-profile report29348536... is signed for458552d and records
cold45.184261s and warm45.217236s, both FAILED/UNIFIED_MARKET_TIME_BUDGET with
profiling_enabled=false and production output unchanged. The earlier instrumented
failures are retained separately. Neither is invalidated by a later optimization.

The publisher branch now demands exactly cold+warm successes, equality of every
entry reference and every baseline shard, unchanged45-second production/control
limits and no production mutation. The real report fails before any stop.
The parent also reports another candidate's real45-second failures; this reviewer
has not independently read that newer artifact and does not certify it.

Do not use the current publisher branch to deploy a changed exporter under the
old458552d identity. A future recommendation requires the actual final source,
its reviewed manifest/tests, real unprofiled cold+warm45-second successes and
exact unchanged entry/shard lineage, followed by actual public acceptance. Add
an explicit profiling_enabled=false requirement when reviewing that final gate.
No service/publication/STEP1 acceptance is granted here.

## Remaining operator limitations

- Stage and fixed-PID actions are intentionally not general resumable operations.
  Preserve partial artifacts and consumed steps after interruption; never delete
  evidence or rerun a stop action blindly.
- Generic source rechecks include independent-test names but do not rehash those
  three external test bodies in verify_source. The original transfer verification
  did hash them; a later test-evidence revalidation should include them again.
- Exact config values and environment remain private. Keep full Supervisor error
  receipts private, since unexpected parser errors may include config fragments.
  This review did not read or print a credential or private configuration.
- Forward383859, ACTIVE295, all frozen books, rollingOFF and LIVE=false remain
  invariants, not new recovery/model authority.
