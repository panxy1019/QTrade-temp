# Independent graded data-quality contract audit

Date: 2026-10-28. Repository inspected: `E:/QTrade-git`, HEAD at audit
`52b03b9b0b64f99665c79ab9850d78cf66e62cae`.

Read-only audit. Required CURRENT_STATE.md, plans/MASTER_PLAN.md, DECISIONS.md,
AGENTS.md and relevant source/tests were inspected. No repository edits, network,
browser, cluster, credentials, broker, service restart, queue reset or recovery
actions were performed by this auditor. Initial worktree had untracked node_modules/;
later data_quality.py appeared during concurrent parent work and is not this
auditor's change. Tests and their fixture outputs are in E:/QTrade-temp only.

## Main finding

The statement that a few missing minute bars or suspended stocks globally block
all daily research is too broad. Collection and Provider construction already
isolate many failures. Two coordination contracts can still cause unrelated work
to wait, and changing them requires a new named contract rather than relaxing all
quality checks.

1. `scripts/prepare_qlib_incremental.py:51-116` starts with dataset stocks but,
   when the public master exists, replaces required symbols with all `in_target`
   securities. It queries all day tasks for the universe/day and requires every
   returned task to be SUCCEEDED or FAILED. Even without a master, an extra
   nonterminal day task blocks because the `any(nonterminal)` check runs on all
   returned rows. A pending ETF outside the current stock dataset therefore blocks
   stock-dataset preparation. This is a real, tested contract, not a hypothesis:
   existing `tests/test_daily_collection_binding.py:171` deliberately expects it.
2. `scripts/run_qlib_data_service.py:47`, checks at 98 and 157, requires a fresh,
   nonfuture global collection service state of WAITING. A collector currently
   working on 5min tasks can temporarily defer day work even when all required day
   inputs are terminal. This is shared-resource/current-collection priority
   coordination, not a statement that all minute data must be complete. Failed
   minute tasks alone do not make `collection_idle` false once the wrapper is
   WAITING. Do not delete this guard without replacing its ownership/resource
   safety contract.

The observed historical checkpoint in CURRENT_STATE.md reports day1497/min1496
of1500. These are prior observations, not a live check by this audit. The3 failed
stock day inputs, if terminal FAILED, are already compatible with the old
qualified-subset mechanism. A minority failure count is not by itself evidence
that a new bypass is needed for that specific checkpoint.

## Existing isolation and global boundaries

| Boundary | Actual current behavior | Who is affected |
|---|---|---|
| Unified task key | universe_version/frequency/symbol/trading_day | Independent day/5min and symbol/day cursors/backoff |
| collect_batch | Each symbol gets SUCCEEDED or FAILED; failures are caught per symbol; raw bytes captured before parsing | A bad Kline or incomplete48-slot minute grid rejects that symbol/frequency/window; remaining symbols continue |
| Shared daily quote envelope | Invalid/duplicate/unknown quote lines clear the shared quote map | Can reject amount reconciliation for all day symbols in that bounded batch; this is shared-source envelope corruption, not minute cross-contamination |
| Batch finish | Batch COMPLETE means sealed receipt, not every symbol success; last_success_batch/completed_end survive later failure | A failed symbol retains retry/evidence independently; no row ready event if there are no admitted rows |
| daily_tasks SQL | Filters exact universe, frequency='day', trading_day | Minute, other universe and other day tasks do not enter the daily proof |
| daily_tasks scope | Master all-target scope and all queried day task terminality | Any pending/missing master target, including unrelated ETF, waits before freezing preparation |
| preparation_request | Loops dataset stocks only; only successful, verified day batches with amount/current close qualify; failures get explicit exclusions | Failed dataset stock has no current batch; healthy stocks can continue after source scope freezes |
| prepare_stock | Per-stock immutable raw/factor/base-prefix validation and attempt ledger | A factor/basis/prefix defect fails that partition; bounded new/retry scheduling prevents prefix starvation |
| builder | Missing preparation partition is allowed only after explicit FAILED attempt; pending/unattempted and zero ready partitions block | Healthy prepared stocks can be admitted; failures retain old history with no current price/signal |
| SourceSnapshot | Whole signed coverage/master/checksum list, all bound status records, clocks and exact inventory are validated | A corrupt manifest, wrong schema, hash/identity/timestamp/authority claim is an integrity failure of the whole snapshot; must remain global |
| restrict_rows | Restrictions keyed by symbol/date; never clears an existing buy/sell block or edits raw prices | Raw source conflict or unknown tradestatus blocks both directions for the row; ST/unknown ST blocks buying; missing status retains explicitly labelled research proxy, not certified normal trading |
| Provider admission | Full file inventory/hash, dataset/reload binding, benchmark/calendar, source identity, READY epoch atomically committed | Corrupt accepted archive/benchmark or changed epoch remains a global failure |
| Frozen-model inference | Current data ancestry/date/precision, old feature-score reference parity and exact supported-current score coverage | A missing current stock is excluded by current_symbols/expected_codes; no stale score can stand in; no-qualified-current and incomplete expected scores fail |
| PAPER | Unknown suspension/ST/precise limits/corporate action/prior-session volume fail closed; held missing/unsafe mark can halt the account unvalued | Execution permission is stricter than exploratory features or diagnostics; research readiness never supplies those facts |

Relevant source anchors:

- `scripts/unified_queue.py:53`, `claim:217`, `finish:255`, `snapshot:368`.
- `scripts/collect_unified_data.py:124` collect_batch; its minute expected-grid check
  raises MINUTE_WINDOW_GAP_REASON_UNKNOWN, then captures a per-symbol FAILED result.
- `scripts/prepare_qlib_incremental.py:51`, `preparation_request:125`, `run:323`.
- `scripts/build_qlib_daily_version.py:122`, `build:225`, pending checks243-247,
  full source capture268 and immutable binding270 onward.
- `src/qtrade/research/source_constraints.py:73`, all checksums114, restrictions171.
- `src/qtrade/research/data_versions.py:39` verify_version and admission transaction.
- `src/qtrade/research/forward_prediction.py:58`, expected_codes90, rank_scores103.
- `scripts/online_prediction_worker.py:102` current approved head/event context,
  final transaction129.
- `src/qtrade/research/forward_paper.py:85`, execution332, invalid held marks348/399.

## Audit of proposed A/B/C/D

**A: required990 dataset day scope, unrelatedETF/minute wait ignored.** Acceptable
as a new research-input policy, with conditions. Derive an exact immutable symbol
set from the actual selected dataset; do not hardcode990. Validate the whole
master's signature/schema/universe and assert every selected dataset member has
the required source membership/type. Filter the queried task rows to selected
symbols before terminality/exact-clock/batch validation. Selected nonterminal or
missing rows still wait; explicit selected FAILED rows remain terminal exclusions.
Unrelated task states may appear in diagnostics but must not enter qualified
input counts or claim full1500 coverage. Do not weaken unknown data into a success.

Changing only `required` is insufficient: the `any(nonterminal)` check must also
operate on the intended exact scope. An unrelated pending row otherwise still
blocks. Missing/duplicate selected task, duplicate dataset symbol, wrong day,
wrong universe, wrong security type, wrong completed close and invalid successful
batch ID must fail the selected scope. Preserve read-only transaction semantics.

Do not equate this with removing collection_idle. If the new scheduler permits
day preparation while unrelated minute collection runs, it needs explicit bounded
resource arbitration and retained immutable-input semantics. If that cannot be
proved in this increment, retain collection_idle and describe the remaining
temporary resource wait honestly; do not claim full runtime decoupling.

**B: global integrity, benchmark and per-stock preparation remain.** Required.
Keep manifest/schema/hash/authority/source revision/epoch/whole accepted archive
verification global. Preserve benchmark overlap/basis/current date and calendar
verification. Retain source conflict/unknown state restrictions, factor alignment,
frozen historical prefix and FAILED isolation. A new quality summary must not
become an alternate authority that can override these validators. Raw conflict
does not necessarily prohibit exploratory feature computation today; it already
prohibits the corresponding simulated trade direction(s). Declare this purpose
distinction, never relabel diagnostic proxy rows as certified execution evidence.

**C: no frozen scope/signal/PAPER changes.** Required. Never overwrite collection
snapshots, request bytes, old version inventories, original model approval,
prediction artifacts, target slots, admissions, sessions or fill journals.
Existing preparation requests keep their original scope and source revision.
New scope creates a new identity. LIVE=false, rolling OFF and risk/OMS/gateway
pipeline remain. Neither extra status observations nor a quality grade authorizes
PAPER start/recovery, target replacement or execution of a halted book.

**D: new approval hash, default no effect.** Required by the user's current-stage
approval condition. This is a distinct research input-scope approval, not an
inferred extension of an existing model activation or recovery approval.

Bind at least named policy/schema version; allowed purpose; exact dataset hash;
selected symbol-set hash; original universe/master identity; frequency; date/window;
expected parent version/event; source revision; rule for terminal FAILED exclusions;
review/test evidence identity and explicit human authorizer. Hash and verify it,
then bind approval+policy hashes into new request context, collection snapshot,
preparation, Provider/version bindings and audit result. This prevents replaying a
scope approval against another dataset, date, purpose, revision or A-to-B-to-A epoch.

Absent/mismatched/expired/unsupported approval must preserve the legacy contract
or block the new mode explicitly. An invalid explicit request must not silently
fall back to a different authorized scope. Never edit old receipts to include
new fields or let a default flag activate the new mode.

The automatic prediction worker follows the current admitted day head after an
ACTIVE model approval; it has no native separate approval check for a new input
scope. Therefore publishing a newly scoped Provider as head can transitively
schedule inference. The approval must explicitly cover that purpose, or the new
research-only result must remain outside serving-head publication until that
separate purpose is authorized. Do not call a new model activation approval
unnecessary without first tracing this effect. Existing model/activation hashes
must remain byte-identical.

## Independent local verification and remaining acceptance tests

`test_contract_adversarial.py` contains35 independent current-code cases. All35
pass. It uses actual daily_tasks, collection_idle, capture_source, restrict_rows,
ObservedSecurity.block_reason, expected_codes/rank_scores and append_benchmark.
No replacement implementation or mocked success path is presented as proof of the
new proposal. Two passing tests intentionally assert the current overbroad waits;
these characterize the old behavior and must coexist with explicit new-mode tests.

Related existing regression:113 passed with a short Windows basetemp. The first
long-path invocation had4 failures, preserved in regression-results.xml. Three
preparation tests returned0 qualified stocks and one predictor fixture raised
WinError3 on a long output directory. Running the exact same7 files with a short
basetemp passed113/113. This is environment/path evidence; no source repair was
made or implied. regression-shortpath-results.xml is the passing receipt.

Reproduction from E:/QTrade-git (PowerShell; no persistent environment changes):

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='E:\QTrade-git;E:\QTrade-git\src'
python -B -m pytest -q -p no:cacheprovider --basetemp E:/QTrade-temp/gqa-a E:/QTrade-temp/graded-quality-contract-audit-20261028/test_contract_adversarial.py
python -B -m pytest -q -p no:cacheprovider --basetemp E:/QTrade-temp/gqa-r2 tests/test_daily_collection_binding.py tests/test_build_qlib_daily_version.py tests/test_qlib_data_service.py tests/test_source_constraints.py tests/test_forward_paper.py tests/test_online_prediction_worker.py tests/test_data_versions.py
```

New-mode acceptance is still outstanding because production code was not changed
by this auditor. Required adversarial additions when implementation is available:

1. Same approved selected stock scope with unrelatedETF day PLANNED, missingETF
   day row, and minute RUNNING/FAILED gap: selected day inputs proceed; changing
   any selected day row to missing/PLANNED/RUNNING blocks before scope freeze.
2. No approval/changed symbol set/dataset/date/universe/master/code/purpose/parent
   epoch/unsupported schema: new mode rejects; old mode remains byte-identical.
3. Explicit selected FAILED input retains only old history, no current fields,
   no score. A nonterminal/unattempted input cannot be relabelled FAILED or normal.
4. Full archive/source tamper and benchmark conflict still globally reject; a
   semantically bad individual partition remains quarantined; unrelated partitions
   retain their data bytes and anomaly-free purpose eligibility.
5. Source state unknown or raw conflict blocks correct row/purpose only; no price
   fill, stale current score, normal-state default or prior-session-volume guess.
6. Freeze old request first, then change scope policy/revision/approval: old bytes
   and replay remain unchanged; new identities are separate; no automatic remap.
7. A-to-B-to-A epoch during preparation/inference, source changed after hash,
   crash during scope sealing, and concurrent resource-owner changes fail closed.
8. New research-only scope does not schedule daily prediction, activate a model,
   replace frozen signal/target, open a PAPER database or consume recovery claim.
   If a later explicit purpose approval enables inference, require its own bound
   receipt and the existing frozen-model/epoch/clock checks without tolerance changes.

No cluster throughput, real990 scope contents, runtime contention, public delivery,
browser rendering, current trading session or live capital behavior was tested.
