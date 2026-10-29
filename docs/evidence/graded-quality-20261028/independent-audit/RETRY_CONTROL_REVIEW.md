# Independent review: approved exact-task suspension retry control

Date:2026-10-28. Repository:E:/QTrade-git. This is a new, separate review batch;
the prior diagnostic14/scope32 receipts and source hashes were not overwritten.
No repository edits, production writes, real approval installation, deployment,
cluster/browser/network/broker actions or recovery operations were performed.

## Reviewed contract and corrections

The production collection retry path remains unchanged when the separate local
data-quality/approved-retry-policy.json is absent. Approval of the daily-input
scope is not approval of this retry policy. An invalid optional retry approval or
quality source returns no deferrals and permits the original retry behavior.
Neither a SUSPENDED label nor this policy admits prices, model inputs, model
activation, PAPER recovery or live execution.

The initial proposed symbol-only queue interface did not carry enough identity
to bind an old diagnosis to the exact current failed task. It also used the
collector's earlier time for both evidence read and claim. This was reported to
root before final verification. Root replaced the symbol set with narrow task
tokens, removed that old interface, and added:

- A task snapshot digest over universe, symbol, frequency, trading day, task
  state, desired/completed endpoints, attempts, next retry deadline, active batch,
  last successful batch, error and consecutive failure streak.
- Token scope, target, task digest, source/quality observation clocks, policy and
  quality hashes, and expiry.
- Revalidation of the actual current row and actual datetime.now(UTC) inside the
  SQLite claim transaction, rather than trusting the earlier caller time.
- Token expiry capped by min(proposed recheck, vendor observation+24h,
  quality publication+1200s), with independent age checks in the transaction.
- Presence/format checks for policy and quality hashes in the token.
- Per-frequency/day retry_controls progress entries, so a later minute check
  does not erase this pass's daily reason.

The quality-freshness cap was a separate correction: retaining only a6h recheck
would have allowed a paused process to use a token after its summary became
older than1200s. The final code now restores the original retry in that case.

## Source and authorization boundary

The retry reader consumes a local trusted builder's freshly sealed summary,
not an arbitrary external API result. The builder checks issuer PDF bytes and
vendor status record hashes before classification. Summary and policy hashes
are integrity/provenance references; they are not cryptographic proof of a
human identity. The operator must create the approval file only after the actual
explicit human action. All test approval actors are explicitly fixture-only.

Given this limited scheduling action, fresh local sealed evidence, at-most1200s
token life and transaction-level task/clock checks, re-opening every issuer PDF
and vendor record inside each queue claim is not necessary for this increment.
No source quality claim is promoted to price or execution authority. A design
that later accepts these tokens from a public/untrusted process would require a
new authentication and provenance contract; the current token is an internal
in-process result, not a public authorization bearer.

The approval's effective_from_day is an operational activation date. The exact
data day/frequency comes from the matching quality case and current task token;
a policy activated today can legitimately govern retries of yesterday's failed
task. It cannot reuse that case for another task day/frequency/universe.

## Independent tests

External test file:test_retry_control_adversarial.py. The valid positive control
was run alone first and passed1/1. It uses a real local UnifiedCollectionQueue
task and proves the accepted token suppresses only the intended failed row,
leaving that row byte-for-byte equivalent as a record while another stock is
claimed. No lake/raw/model/PAPER path is created by the test.

The final independent suite contains52 cases, all passed/0 failures/0 errors/
0 skips. It covers:

1. Absent retry policy does not even need a usable quality channel; original
   retry remains available.
2. Exact symbol/day/frequency/universe and policy/quality hashes appear in the
   approved control; a different requested scope gets no token.
3. Changing desired endpoint, attempts, next deadline, last error, failure
   streak, last-success batch or active-batch identity after summary generation
   invalidates the old token and restores current retry.
4. PLANNED, INTERRUPTED and partially collected FAILED tasks remain claimable.
   This is tested both with a stale token and with a deliberately recomputed
   matching fingerprint, proving the state/partial-content guard independently
   of the fingerprint guard.
5. A stale caller clock cannot preserve an expired token: the transaction uses
   its actual clock. Expiry, future/naive/overlong time, future or stale source
   and quality clocks restore retry.
6. Wrong task key/hash, missing expiry, malformed token object, missing policy
   hash or damaged quality hash cannot suppress collection.
7. Nonobject/malformed/missing optional policy/quality files do not stop ingestion.
8. Proposal state, model/PAPER/live flag changes, future approval and zero human
   authorization hash do not grant retry control or other authority.
9. Stale quality, missing issuer/vendor evidence IDs, stale status, unknown
   state and incomplete evidence cannot defer.

Relevant repository regression plus the old independent14+32 compatibility
tests:94 passed,0 failures/errors/skips. The94 consist of48 current repository
cases across test_unified_queue, test_collect_unified_data,
test_collection_retry_policy and test_data_quality, plus the prior46 independent
diagnostic/scope cases. An existing Starlette/httpx test-client deprecation
warning appeared and did not skip a test.

| Receipt | Tests | Failures | Errors | Skips |
|---|---:|---:|---:|---:|
| retry-positive.xml | 1 | 0 | 0 | 0 |
| retry-adversarial-final.xml | 52 | 0 | 0 | 0 |
| retry-related-regression.xml | 94 | 0 | 0 | 0 |

The43-case first pass and49-case extended pass receipts remain separately
retained. They are earlier coverage counts, not replacement claims for the final52.

## Reproduction and source identity

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='E:\QTrade-git;E:\QTrade-git\src'
python -B -m pytest -q -p no:cacheprovider --basetemp E:/QTrade-temp/gqr-review E:/QTrade-temp/graded-quality-contract-audit-20261028/test_retry_control_adversarial.py
python -B -m pytest -q -p no:cacheprovider --basetemp E:/QTrade-temp/gqr-regr tests/test_unified_queue.py tests/test_collect_unified_data.py tests/test_collection_retry_policy.py tests/test_data_quality.py E:/QTrade-temp/graded-quality-contract-audit-20261028/test_diagnostic_adversarial.py E:/QTrade-temp/graded-quality-contract-audit-20261028/test_scope_policy_adversarial.py
```

retry-reviewed-source-hashes.json independently binds5 source files for this
batch:collection_retry_policy.py, data_quality.py, unified_queue.py,
collect_unified_data.py and build_data_quality_summary.py. This does not relabel
the previous source hash manifest. A post-test rehash found no drift.

No remaining blocker was reproduced in this final local review under the stated
trusted-local-builder contract. Actual approval remains uninstalled and source
undeployed. Real vendor/PDF completeness, cluster contention, automatic future
cadence and public/browser acceptance were not tested here. Skipping a retry
never marks the task successful, advances coverage, alters its original retry
deadline or supplies missing price bars; later invalid/expired control returns
it to the original due retry path.
