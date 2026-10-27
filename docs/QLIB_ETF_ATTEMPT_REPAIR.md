# Explicit ETF attempt repair

The default ETF daily service still permits one durable attempt per completed
exchange day. This separate operator tool repairs only the proven failure
`ETF_SERVICE_WAITING_CURRENT_COLLECTION_RETRY`. It preserves the original
attempt, failure and partial raw evidence. It has no order, gateway, model,
PAPER recovery or live-trading action. `LIVE_TRADING_ENABLED=false` is mandatory.

## Source and gap audit

Reuse the original frozen observed master, exchange `Calendar`,
`run_etf_trend_service.source_gate`, existing bounded Sina `fetch`,
`UnifiedBatchStore`, `admit`, and `verify_etf_archive`. The missing capability is
an explicitly reviewed, bounded continuation with original raw reuse, separate
code provenance and durable per-request uncertainty. vn.py has no role at this
research-data boundary.

The original service first persists each received response and then checks
priority before another request. A signed failure with the exact priority code,
one completed query and exactly one verified prefix receipt proves that the next
HTTP request was not issued. Other failures, mismatched counts, incomplete raw,
non-prefix symbols, unknown clocks, existing aggregate receipts or diagnostics,
and uncertain old attempts cannot authorize this repair.

The documented original policy says: “A partial or uncertain attempt requires
explicit operator review; there is no automatic same-day repair or retry policy.”
This tool implements a separate explicit plan/apply operation. It does not alter
that default policy or add an automatic repair loop.

## Read-only plan and explicit apply

Run from the reviewed release containing this implementation. The source revision
must differ from the failed collector revision. In the default local Git mode,
apply checks actual Git HEAD, rejects modified tracked files and checks the
complete actual Python inventory and blob hashes against the tracked Git tree.
Required repair/shadow/CLI modules must be tracked; extra untracked or ignored
Python code is rejected. No production clock override
is supported. Plan itself does not create files/directories, acquire leases,
query the vendor, or change collection state; redirecting its JSON is an explicit
operator output operation.

```bash
export LIVE_TRADING_ENABLED=false
export QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true
export PYTHONPATH="$PWD/src:$PWD"
python -B -m scripts.repair_etf_daily_attempt \
  --base "$B" --local-root /root/qtrade-local/etf-trend-service \
  --day 2026-10-27 --source-revision "$REVIEWED_FULL_RELEASE_SHA" \
  --source-manifest /tmp/etf-repair-source-manifest.json \
  > /tmp/etf-repair-reviewed-plan.json
```

Review the complete plan: original signed request/attempt/failure, byte hashes of
the frozen master and source documents, original raw-receipt hashes, the old
publication pointer, frozen target symbols, reused symbols, remaining symbols and
total/additional query budgets. The actual October 27 interruption is one healthy
`sz159915` response out of the frozen 30, so the plan must report one reused and
29 remaining responses. These source facts still require actual cluster plan
verification; offline tests are not a production receipt.

Supply the plan's `evidence_sha256` explicitly after review:

```bash
python -B -m scripts.repair_etf_daily_attempt \
  --base "$B" --local-root /root/qtrade-local/etf-trend-service \
  --day 2026-10-27 --source-revision "$REVIEWED_FULL_RELEASE_SHA" \
  --source-manifest /tmp/etf-repair-source-manifest.json \
  --apply-plan /tmp/etf-repair-reviewed-plan.json \
  --approved-plan-sha256 "$REVIEWED_PLAN_SHA256" --deadline-seconds 900
```

For a Gitless immutable cluster release, `--source-manifest` is required in both
commands. Omit it only for local Git mode. The supplied bounded signed JSON has
the existing `AUTHORITY` fields plus `schema_version` equal to
`qtrade.etf-repair-source-manifest.v1`, `revision`, `files` and `evidence_sha256`.
`files` maps **every tracked Python file under `src/` and `scripts/`**, plus
`configs/observer_calendar_2026.json`, to its real Git tree blob SHA1 at that
committed revision. Generate this inventory from `git ls-tree -r` of the reviewed
commit, not from uncommitted working bytes; apply must use that exact committed
release. The operator checks the manifest's origin against the local Git commit
and verifies its transfer SHA256. These checksums are not a remote signature.

The release's resolved directory name must equal the full revision. The tool
checks its complete actual `src/scripts` Python inventory, rejects missing or
extra Python files, symlink files/ancestors and path escape, and recomputes each
Git blob hash as SHA1 of `blob <length>\0<actual bytes>`. Plan binds the complete
manifest and its exact file SHA256; apply independently repeats all checks and
archives byte-identical manifest evidence. The resolution verifier checks that
archived provenance without assuming its own later running revision is the
historical repair revision. Installed third-party dependencies are outside this
repository-source inventory and retain their existing environment acceptance.

Linux is required for real local `flock`, directory fsync and `SIGALRM`.
The existing collection database is the sibling
`/root/qtrade-local/unified-data.sqlite`, opened by the reused gate in read-only
mode. No queue initialization or ownership claim occurs. Keep the existing
services running; the tool obtains the original daily service's `cycle.lock`
nonblocking and refuses concurrent work. It does not obtain or replace PAPER's
lease.

## Durable evidence and failure semantics

Apply recomputes the entire plan under the original cycle lease before any
request. A new immutable `repair-claim.json` in the original archive reserves its
single repair across all revisions; another revision cannot reset the budget.
Original `attempt.json`, `failed.json`, source receipts and raw packs remain
byte-identical. A new archive uses the frozen old master/day/calendar with the
actual repair code revision. Its signed repair plan and completion record both
the original collector revision and the repair revision. The frozen source
documents are copied byte-identically into its `origin` directory for later
independent replay, including original file hashes.

Each additional request follows this sequence:

1. Check actual remaining deadline and all original source-priority, storage,
   cooldown, fresh heartbeat and collection-ledger gates.
2. Fsync an immutable `repair-requests/NN.json` reservation.
3. Call the existing bounded public Sina fetch exactly once.
4. Fsync the actual raw response and `repair-receipts/NN.json` before proceeding.
5. Validate the response and complete 21-session closes. HTTP/transport/incomplete
   response failure also sets the existing six-hour source cooldown.

Before any request, setup explicitly creates the new archive, raw objects,
origin, reservation, response-receipt and local repair directories, fsyncing each
new directory and its parent. Thus reservation fsync does not rely on an
unflushed ancestor directory entry. The original immutable claim remains an
additional conservative barrier against a second burst after interruption.

An interruption, priority change, malformed response, source error, timeout or
uncertain request stops the operation. A reservation without a sealed receipt is
uncertain and consumes its request allowance. No apply call resumes this job, no
automatic retry runs, and another revision cannot issue a replacement burst.
Operator review of preserved evidence is required for further work; do not delete
claims or reservations. A crash during setup can also leave a conservative
claimed-but-unissued attempt. This implementation deliberately provides no
claim-reset or uncertain-response recovery command.

Only all frozen symbols with unique verified responses permit aggregate receipt,
diagnostic, exact archive replay and `repair-completed.json`. Publication checks
the exact earlier pointer document and byte hash under the cycle lease; a
third-party update is never overwritten. A CAS failure preserves a completed but
unpublished repair for review and does not trigger another source query.

The original archive receives a small immutable `repair-resolution.json` only
after full independent resolution verification, successful pointer CAS update,
and durable local completion. This is the final source-admission marker. Both
SHADOW candidate discovery and direct source replay require the final resolution
for any repair archive. A diagnostic or complete receipt alone cannot become an
observation input after a failed completion/publication. The SHADOW scanner treats the old partial as resolved history only when
the claim, original documents/receipts/raw, new plan, all request reservations,
response receipts/raw, budget, completion and new diagnostic replay agree. A
missing/corrupt/unresolved/ambiguous partial remains
`ETF_SHADOW_ARCHIVED_SOURCE_INVALID` and keeps publication degraded. The old
partial is never a source candidate. No clock, safety check or original target
identity is changed. The existing SHADOW service must observe the exact next
session source itself; a repair completion alone is not transition acceptance.

## Verification and limits

```bash
PYTHONPATH="$PWD/src:$PWD" python -m pytest \
  tests/test_etf_attempt_repair.py tests/test_etf_shadow_service.py \
  tests/test_etf_trend_service.py tests/test_etf_research_contract.py \
  tests/test_etf_trend.py tests/test_etf_trend_publication.py
python -m ruff check src/qtrade/research/etf_attempt_repair.py \
  scripts/repair_etf_daily_attempt.py scripts/run_etf_shadow_service.py \
  tests/test_etf_attempt_repair.py
MYPYPATH="$PWD/src" python -m mypy --explicit-package-bases \
  --follow-imports=silent --platform linux \
  src/qtrade/research/etf_attempt_repair.py scripts/repair_etf_daily_attempt.py \
  scripts/run_etf_shadow_service.py
```

Offline fixtures use synthetic source bodies and controlled clocks; tests do not
claim actual market data or a real completed-session transition. Windows fixtures
replace Linux filesystem leases, directory fsync and alarms. Actual Linux
durability, release provenance, controlled apply and the next ordinary SHADOW
publication require separate deployment evidence. Preserve native-close,
adjustment, distribution, PIT and execution limitations. No holdings, fills,
portfolio returns, research model activation or STEP2 completion follow from
this tool.
