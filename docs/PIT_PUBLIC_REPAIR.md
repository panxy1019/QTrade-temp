# Public-source PIT repair and remaining evidence gaps

This delivery validates the public backfill queue and produces an immutable,
offline evidence-gap report. It does not certify complete historical PIT. The
owner has no licensed historical database and authorized continued verifiable
public-source work. `LIVE_TRADING_ENABLED=false`; no gateway is involved.

## Source and reuse audit

The implementation reuses BaoStock's existing serialized session, frozen
990-stock dataset scope, per-symbol attempts, bounded exponential backoff,
signed coverage, immutable source snapshots, Qlib directional masks and Exchange
reload checks. vn.py execution/gateway components do not supply historical master,
announcement-time, ST, delisted-price or exact-rule evidence; adding a gateway
would not close these gaps. No new vendor, trading path or order authority was
introduced.

The last cluster state supplied for this work was **552/990 symbols and
1,247,523 vendor status rows**. Its latest source pass failed and its global retry
was due **2026-10-26 14:51 UTC**. Those counts are a prior accepted checkpoint,
not a fresh local measurement or a PIT certification. This branch does not SSH,
restart services, bypass that deadline, download new market history or alter
frozen experiments, Providers or models. Cluster acceptance remains the parent
integration step.

Existing accepted official reconciliation retains 372 exchange rows, 342 A-share
identity rows and 336 unique A-share identities. It found zero overlap with the
990-stock research sample. Its 310 `DATES_AGREE_UNCERTIFIED` rows remain
uncertified; 32 review and 30 unresolved/non-A rows remain visible. These are
recorded prior results in `PIT_OFFICIAL_RECONCILIATION.md`, not a new network probe.
Current exchange lists cannot establish publication or historical availability.

## Implemented changes

### Checkpoint admission and recovery

Previously, collection counted a cached checkpoint as complete after its hash
and filename matched. The new content check also verifies source, symbol,
observation clock, canonical strictly ordered dates, exact vendor identity,
requested date bounds, state domains, finite nonnegative supplied numbers and
OHLC relationships. A source-reported trading row with a supplied zero OHLC price
is rejected. Empty or absent numeric/state fields stay unknown and are counted;
they are never filled with normal trading. Observation-day checks use Shanghai
time even when the receipt clock uses UTC.

One bad checkpoint no longer stops all remaining symbols. Its original bytes are
stored reversibly in a signed immutable receipt under:

`pit-repair/quarantine/<symbol>/<raw-byte-sha256>.json`

The receipt contains base64 of the original bytes, byte hash and source-only
authority. The current raw file remains untouched while it waits for its own
bounded retry. A previously successful ledger entry becomes an unresolved retry;
other unattempted and due symbols keep their separate budgets. A successful
retry atomically replaces the invalid current checkpoint only after the old
bytes have been preserved. Old versioned coverage reports and frozen run copies
are never rewritten. Newly rejected parsed SDK responses are retained separately
under `pit-repair/rejected-responses/<symbol>/` before recording failure. These
are the SDK-returned values, not a claim to preserved vendor transport packets.

The manual collection entrypoint now checks the existing global retry deadline
before writing a new attempt. Migrating a legacy schedule also preserves a later
deadline and its failure count. A normal supervised child is dispatched only
after the supervisor's own deadline check. Neither path accelerates the next
global retry. Checkpoint content validation is independent of collection count:
accepted raw observations with unknown fields do not establish usable execution
state or calendar completeness.

### Unknown-state diagnostic policy and archive compatibility

New research configs use policy `RECONCILED_BACKFILL_ADDITIONAL_BLOCKS_V2`,
comparison protocol `ALIGNED_SUPPORTED_UNIVERSE_SOURCE_BOUND_CONSTRAINTS_V3`,
and constraint audit schema `qtrade.source-constraints.v2`.

| State | New diagnostic result |
| --- | --- |
| Missing whole source row | Keep prior proxy masks, count missing state and write a ledger row |
| Present row with unknown trade state | Add both-direction blocks and count unknown trade state |
| Reconciled trading row with unknown ST state | Add a research buy exclusion; never label it non-ST |
| Reconciled known ST row | Keep the existing research buy exclusion |
| Raw source disagreement / source suspension with a price row | Keep both-direction blocks |

Every resulting mask is at least as restrictive as its original proxy. Unknown
state counters remain visible even when another conflict determines the block
reason. ST exclusion is research policy, not an assertion of an exchange rule.
No exact price limits, auction replay or settlement readiness is claimed.

The worker now derives publication protocol from the policy stored inside the
archive. Legacy V1-policy/V2-protocol archives remain readable and retain their
original protocol. `verify_archive` continues to validate their exact frozen
proofs. Browser comparisons retain source identity checks for both V2 and V3 and
reject comparisons across protocols. Archived V2 templates are not reusable as
V3 tasks: the server explains that the archived protocol is incompatible. It
does not replace the old configuration with current defaults. Current frozen
V2 Providers and restored models can continue to be read; this delivery does not
recompute their masks or mutate their binaries.

### Offline repair dossier

`scripts.audit_public_pit` reads the pinned coverage, master, raw status files,
dataset, attempt ledger and schedule. Optional inputs are the immutable official
reconciliation and observed-security master. It rechecks signatures/content
hashes and binds actual status file byte hashes; malformed/missing status inputs
appear as per-symbol integrity gaps instead of disappearing from the report.
It checks content and reports known-date bounds without inferring session
coverage, suspension from missing bars, or last-trade dates from delisting dates.

The result is stored immutably at:

`pit-repair/audits/<evidence-sha256>.json`

It includes per-symbol issues and unknown counts, observed-master quarantine
counts, official identity/duplicate review scope, whether the official report
used the same master snapshot, and a nonexecuting repair-task list. Missing
status tasks carry the later of their per-symbol and global retry deadlines.
`execution_allowed=false` for every task: they document required evidence and
do not submit downloads or alter services. Official date conflicts and delisted
price requirements are separate from the 990-stock status queue.

## Complete-PIT gaps that remain

| Requirement | What public evidence currently establishes | Still required |
| --- | --- | --- |
| Historical full-market universe | Observed current master, current biased sample, preserved delisting lists | Past versions and information availability, inclusive historical membership and identities |
| Historical status | Partial vendor backfill with current observation times | Missing symbols/fields, independent state reconciliation and historical knowledge times |
| Delisted prices | Official/vendor date disagreements are preserved | Actual delisted-symbol prices, corporate actions, coverage and date semantics |
| Exact trade constraints | Conservative daily proxies plus additional diagnostic restrictions | Source-backed directional limit prices and rule effective dates, IPO/board exceptions, ST changes, auctions and settlement |
| Corporate actions | Vendor adjustments and retained source identity | Independent action ledger, announcement/effective times and adjustment reconciliation |
| Calendar/coverage | Ordered supplied dates and source bounds | Exchange-session completeness, listing-aware coverage and independently explained absences |
| Historical PIT availability | `observed_at` and immutable receipt identities | Verifiable historical `available_at` for each relevant fact; it remains null |

Even collection of all 990 status files would leave these requirements open.
The report always keeps `complete_historical_pit_symbols=0`,
`historical_pit_certified=false` and `promotion=NOT_ELIGIBLE`. It never promotes
present-day web/list timestamps into historical availability. Some past
information versions may not be recoverable from currently accessible public
sources; there is no promise that missing evidence can be manufactured or
certified without an appropriate source.

## Reproduction and integration acceptance

From this checkout, with its own `src` first on the import path:

```powershell
$env:PYTHONPATH='E:/QTrade-pit-agent/src'
$env:MYPYPATH='E:/QTrade-pit-agent/src'
$env:LIVE_TRADING_ENABLED='false'
python -m pytest tests/test_pit_source.py tests/test_pit_collection_queue.py tests/test_pit_status_quality.py tests/test_public_pit_audit.py tests/test_source_constraints.py tests/test_qlib_archived_template.py tests/test_qlib_workflow.py tests/test_qlib_worker.py
python -m ruff check .
python -m mypy src
```

Focused local evidence: **118 passed, one Linux-only process-identity test
skipped** on Windows. A further **55 checks passed** for official/observed master,
publication, restored-model bundle and daily-version compatibility. Ruff and
all 123 Python source modules pass type checking. The focused browser contract
suite passes seven checks, including retained V2 source binding and cross-policy
comparison rejection; frontend lint and production build pass. Two broader
Windows frontend runs ended with timeouts in unchanged DataPages/intraday UI
tests (218/219 and 216/219 passed respectively), with no failed protocol
assertion. Full frontend acceptance therefore remains pending on the integration
host; no unrelated UI or test-timeout setting was changed. Directory fsync
durability is not established by mocked
Windows writes; real Linux acceptance is required before deployment.

On the cluster, from the verified release with its own `src`:

```bash
export LIVE_TRADING_ENABLED=false PYTHONPATH="$PWD/src" MYPYPATH="$PWD/src"
python -m pytest
python -m ruff check .
python -m mypy src

# Offline audit is allowed during vendor backoff. It does not query BaoStock.
python -m scripts.audit_public_pit --base "$BASE" \
  --observed-master "$BASE/unified-data/catalog/latest.json" \
  --official-reconciliation "$BASE/pit-independent-sources/reconciliation/b620f4a277d1ddacd7b779cd663ac7901ed38e019d97e2b1e941566202f8fc89.json"

# Existing global deadline is authoritative; --once returns without collection
# while it is not due. The parent integrates the supervised release separately.
python -m scripts.run_pit_repair --base "$BASE" --once

cd web
npm test
npm run lint
npm run build
```

Use actual accepted reconciliation identity if it has changed, and preserve both
reports. For cluster acceptance retain before/after coverage identities, retry
deadlines, immutable audits, original checkpoint/experiment/Provider/model hashes,
full-suite output and the new supervisor source identity. Verify one failed
symbol cannot starve new symbols and that the next global deadline is not moved
earlier. Do not describe a live process or a zero exit as complete PIT evidence.
