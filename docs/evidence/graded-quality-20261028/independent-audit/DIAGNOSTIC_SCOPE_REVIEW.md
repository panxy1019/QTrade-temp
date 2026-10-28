# Independent diagnostic and approved-scope review

This continues AUDIT.md. All review is read-only against E:/QTrade-git. Only
external fixtures/tests/reports under E:/QTrade-temp were written. No network,
cluster, browser, production queue/database, broker or recovery actions occurred.

## Diagnostic findings reported to root

Initial existing diagnostic/recent-status tests:27 passed,0 skipped. Independent
negative-contract probes then reproduced the following defects before root's
repairs; initial XML remains immutable as diagnostic-adversarial-initial.xml.

1. **P1: optional channel could stop the market publisher.** quality_publication
   accepted any JSON type and validate_quality immediately called value.items().
   JSON [] raised AttributeError outside the optional channel's caught exception
   boundary. This escaped publish() instead of returning the unavailable sentinel.
   A shape guard raising ValueError is needed before dictionary methods.
2. **P2: retained universes contaminated current diagnosis.** Builder SQL selected
   all universes; two retained failures with identical symbol/frequency/day yielded
   duplicate cases and global summary failure. An old failure/new success could
   show the obsolete failure. Bind a verified current universe, filter both
   counts/tasks by it, and bind it into the publication and each case.
3. **P2: recent pointer key was not tied to the accepted record symbol.** A scope
   containing A and an A.json file whose signed contents described B was counted
   as verified. The collector had a path-stem check but this new consumer omitted
   it. Require pointer key = filename symbol = record.symbol and scope membership.
4. **P2: self-hash validation did not enforce diagnostic semantics.** Re-signed
   future source time, source time later than summary time, future recent status,
   false case counts and invalid source revision passed the API/publication
   validator. Explicit contradictory per-case authority flags were also accepted.
   Validate these fields against the publication time and recomputed counts,
   retaining authority=false without treating quality classification as authority.
5. **P2: optional nonobject evidence could erase all other diagnoses.** [] in a
   supplemental status file, recent pointer or issuer index raised AttributeError
   outside the individual quarantine exception clauses. Each external JSON entry
   needs a shape check before .items(). A bad optional source must remain a source
   issue while the other task cases remain visible.
6. **P2: malformed UI-consumed container could crash the data page.** A re-signed
   source_issues=null was not checked by validate_quality, while the card calls
   value.source_issues.length. Validate source_issues and its entries rather than
   relying on the TypeScript interface for external JSON.

Root repaired findings during review. An intermediate repair accidentally tested
every per-case AUTHORITY field including `classification`; this collided with
the case's UNKNOWN/SUSPENDED quality classification, rejecting every nonempty
case list. The independent suite includes a valid positive control to detect
this kind of false-green negative test. This regression was reported immediately.

No direct trading privilege escalation was found in the diagnostic design:
production_admission_effect=NONE and execution_admitted=false are emitted and
checked, the route is GET-only, PAPER purpose remains unconditionally blocked,
the publisher is data-only, and no model/target/recovery writer consumes the
quality label. The above issues concern false diagnosis, provenance, error
isolation and availability; the optional publisher issue can affect unrelated
market delivery and therefore must be fixed before deployment.

## Approved scope implementation independently tested

External `test_scope_policy_adversarial.py` ran32 cases, all passed/0 skipped
against the source including request_policy's paired-field check. Receipt:
scope-adversarial-results.xml.

This is a separate suite, not the original35 legacy tests. It checks:

- Default absent policy retains the old whole-target wait.
- Explicit approved selected scope ignores unrelated pending ETF day work and
  failed minute work, while a synthetic exact990-stock scope returns990 stocks.
- Missing, wrong-day, wrong-universe and nonterminal required tasks do not become
  successes or exclusions; exact desired/completed close and batch identity remain.
- Proposal state, unsupported mode/schema, research-only uses, extra PAPER use,
  changed model/PAPER flags, integer coercion of false, wrong symbol/universe
  hashes, inferred authorization, zero authorization hash, blank author, future
  or naive approval time, and a not-yet-effective date all reject.
- Unhashed policy tampering and changed dataset members cannot reuse approval.
- Explicit required FAILED rows remain terminal with no successful batch pointer.
- New approved preparation freezes a distinct approval-bound identity; legacy
  collection/request bytes remain identical and FAILED rows never load a batch.
- A valid approval cannot bypass the unchanged global collection_idle guard.
- A request containing policy_sha256 but lacking its embedded policy rejects
  instead of silently falling back to the legacy path. Root added request_policy
  after this omission was identified; the final independent case passes.

The first scope suite invocation had31 passes and one fixture error because root
added an earlier base-dataset read while the test was running. The fixture was
completed with its signed old dataset and the32-case suite then passed. This was
not a policy-contract failure; both XML receipts are retained.

The approval trust boundary is a local operator-written file; self-hash plus an
authorization-message digest is an integrity/reference contract, not a digital
signature or independently verified human identity. No public approval writer
was introduced. Operational activation still requires the actual human message
and correct operator-written record; tests use explicitly labelled fake authors.

Uses explicitly include PREDICTION, so an approved new serving-head Provider can
enter the existing approved-model inference pipeline without implying model
activation or PAPER authorization. Default None does not authorize that behavior.
No approved-scope policy was written into a production base during this review.

Known remaining coordination limit: collection_idle is retained. Minute work can
temporarily defer day preparation while the shared collector is busy; a failed
minute task is not itself a data-quality veto. Existing immutable per-day service
requests reject a later changed policy rather than rebind them automatically.

## Reproduction

From E:/QTrade-git, PowerShell:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='E:\QTrade-git;E:\QTrade-git\src'
python -B -m pytest -q -p no:cacheprovider --basetemp E:/QTrade-temp/gqd-review E:/QTrade-temp/graded-quality-contract-audit-20261028/test_diagnostic_adversarial.py
python -B -m pytest -q -p no:cacheprovider --basetemp E:/QTrade-temp/gqp-review E:/QTrade-temp/graded-quality-contract-audit-20261028/test_scope_policy_adversarial.py
```

## Final repaired-source verification

Root reported the repairs complete while the source remained undeployed. This
auditor first ran only the valid diagnostic positive control:1 passed/0 failures/
0 errors/0 skips. Only after confirming that fixture was accepted did the full
independent negative suites run:

| Final receipt | Tests | Failures | Errors | Skips |
|---|---:|---:|---:|---:|
| diagnostic-positive-final.xml | 1 | 0 | 0 | 0 |
| diagnostic-adversarial-final.xml | 14 | 0 | 0 | 0 |
| scope-adversarial-final.xml | 32 | 0 | 0 | 0 |
| final-related-regression.xml | 155 | 0 | 0 | 0 |

The155-case regression covered the existing daily collection binding, Provider
builder, data service, source constraints, PAPER, prediction worker and data
version tests plus new data_quality, recent_status and data_quality_policy tests.
One existing Starlette/httpx test-client deprecation warning appeared; it did not
skip or fail a case.

The14 independent diagnostic cases now pass for the exact previously reported
negative inputs: future or post-summary source clocks, future recent status,
wrong counts/revision, explicit elevated case permission, malformed source_issues,
retained other-universe failure, wrong record symbol under a pointer key, and []
in the publication or each of the3 optional evidence sources. The positive
control prevents rejection of all publications from masquerading as successful
negative testing.

Source inspection also confirms the repaired case-quality classification does
not collide with AUTHORITY.classification, evidence IDs and proposed recheck
clocks receive checks, and legacy builder requests without policy fields do not
add a new early base-dataset read. The32 policy tests separately preserve exact
selected-input checks, approval-purpose/hash binding, old immutable bytes and the
global resource-coordination wait.

`final-reviewed-source-hashes.json` binds11 reviewed source files, including the
diagnostic/policy modules, builder/preparation/service, recent collector,
publication/API and frontend card. A post-test rehash found no source drift.
Original initial and intermediate failure receipts were retained rather than
overwritten or relabelled as final passes.

No remaining blocker from the reported findings was reproduced in this final
local review. This statement is bounded by the audited source and tests. Actual
human approval installation, cluster resource behavior, complete real990/1500
coverage, publication delivery, browser rendering and trading-session acceptance
were not performed or certified. No runtime/cluster/public/browser acceptance or
PAPER/live authorization is implied by these local test results.
