# Supervised forward research service

`scripts/run_qlib_forward_service.py` holds independent prospective SHADOW/PAPER
books across polling cycles. It uses existing archive verifiers and the durable
portfolio → risk → OMS → simulation execution stack. It never accesses a broker,
adds a gateway, requests network data or enables live trading.

This service implements orchestration; actual next-session acceptance still
requires genuinely observed collector receipts. Unit-test clocks and synthetic
fixtures establish behavior only. STEP2 remains blocked by STEP1 acceptance.

## Sources and admission

The service reads the signed `workflow/public/online.json`, requires a worker
heartbeat no older than 12 minutes, current exchange-calendar daily data, and the
exact current data event. It finds the newest COMPLETE matching signal job in
the workflow SQLite ledger and verifies its archive. It independently verifies
the current approved model prediction and its exact approval epoch. Target
source revisions remain those of the generating archives; the service separately
publishes its own implementation revision.

Signal-family discovery and approved-prediction discovery are independent. A
missing/corrupt archive on one side retains verified targets from the other side
and publishes DEGRADED with a fixed `FORWARD_SERVICE_SIGNAL_DISCOVERY_FAILED` or
`FORWARD_SERVICE_PREDICTION_DISCOVERY_FAILED` diagnostic. No matching completed
signal job is reported as `FORWARD_SERVICE_SIGNAL_UNAVAILABLE`; an active model
without its current prediction (or a prediction without current approval) reports
`FORWARD_SERVICE_PREDICTION_UNAVAILABLE`. Arbitrary archive exception messages are
never published or saved as diagnostics. Final common data-event and approval
rechecks still reject the entire discovery result if either changes during
verification. Partial discovery never weakens those gates or uses fallback data.

Archive verification occurs outside the short admission guard. Final data and
approval checks, durable target freeze and an immutable per-target admission
receipt run while holding `BEGIN IMMEDIATE` reservations in **data then model**
order, matching existing control workers. These two connections use `mode=rw`
solely to reserve existing databases; the service changes no data/model rows,
creates no schemas there and makes no process-owner claim. Workflow/collection
connections use `mode=ro`. A crash or rejected admission leaves the frozen target
without a valid admission receipt; observation cannot use it or silently retry
the incomplete admission.

Only actual writes before execution count as prospective freezing. The service
refreshes the real clock after acquiring admission locks; a stale polling clock
cannot backdate a freeze. Each book accepts one immutable target per execution
time. Signal weights remain separate from simulated holdings and fills.

## Observation, publication and recovery

With `--collection-database`, the service reads exact-day persisted collection
tasks and verified batch IDs, including earlier days after the mutable progress
snapshot advances. Current tasks must exist for every required instrument and
reach SUCCEEDED/FAILED before observation. Failed/missing market evidence never
becomes a fabricated price or normal-trading flag. The oldest uncompleted target
is processed first; neither a missing day nor PENDING receipt can be skipped.

The source adapter replays raw quote/kline bytes and normalized Parquet. Previous
session volume comes only from independently verified previous-session receipts
known before execution; absent evidence remains null and blocks PAPER liquidity
admission. Verified batch selections are frozen locally and replayed identically.
Same-day total volume never sizes a simulated opening order.

Before PAPER observation, the service again reserves data then model, requires
the frozen data event, rejects any subsequent rollback, checks current head/event
consistency, and permits a later version only if its hash-bound dataset ancestry
contains the frozen dataset. Approved-model PAPER additionally requires the same
current explicit approval. This deliberately blocks a model revoked after its
intended execution time as well. SHADOW records market observation without orders,
holdings, cash or forward portfolio returns.

Each account has local `forward.sqlite` and `operations.sqlite`; neither belongs
on NFS. The local service lock fences duplicate service processes. At least
100 GiB must remain on the persistent archive filesystem before freeze or observe.
Book failures and invalid summaries are isolated. Every public heartbeat and
summary passes `validate_forward_publication` before atomic publication to
`workflow/public/forward.json`; output is capped at 32 books / 64 errors / 2 MB.
Diagnostics retain safe error codes/classes, never arbitrary exception messages.

Default mode is SHADOW. `--modes SHADOW,PAPER --initialize-paper ACTOR` permits
one explicit initialization of pristine PAPER books, with a durable human intent
receipt before reconciliation. An existing book is never recovered by that flag.
Restarted PAPER remains halted and requires a separate explicit human recovery
and successful reconciliation. A SHADOW-only restart does not open persisted
PAPER books. The service gracefully stops runtimes, publishes STOPPED and closes
SQLite connections on SIGTERM/SIGINT. Supervisor restart cannot bypass HALT.

An operator can request recovery of one existing PAPER book while the supervised
process keeps running. This uses a local, one-shot request, never a web control
or repeated startup flag. The preparation command reads the existing signed
roster and a live service heartbeat no older than 90 seconds; no account config
duplication or book constructor is required. It writes a content-hash-bound
request with exact book ID, human actor, unique request ID, current service
instance ID, real issue time and expiry. Default validity is five minutes;
accepted validity is 30–600 seconds. Local filesystem permissions provide access
control; the content hash is integrity evidence, not proof of a person's identity.

Writing this request authorizes the live process to attempt recovery on its next
cycle. The command only prepares the request and never reconciles or opens a
runtime itself. It must be invoked only for an explicitly approved human action.
The live consumer accepts only the specified, currently halted PAPER book and
persists an immutable consumed intent **before** calling its existing human
reconciliation/recovery method. It checks the real expiry again after that
write. Success/failure has a separate immutable receipt. Failed or interrupted
attempts cannot retry automatically; a consumed intent without a result is
reported for human review. PENDING forward sessions still reject reconciliation
and remain intact. Expired requests, wrong books, SHADOW, extra fields, invalid
actors and requests from another process instance are rejected without recovery.
Each service restart rotates the instance ID, so even an unconsumed request from
the old process cannot restart PAPER. Completed/rejected requests cannot replay.

## Reproduction

Run on Linux from the verified checkout, with the actual configured source
revision and existing ledger paths. The process creates only its local books,
diagnostics and the bounded public snapshot.

```sh
export PYTHONPATH="$PWD/src"
export LIVE_TRADING_ENABLED=false
python -m scripts.run_qlib_forward_service \
  --base "$B" --local-root /root/qtrade-local/forward-research \
  --workflow-database "$WORKFLOW_DB" --model-database "$MODEL_DB" \
  --data-database "$DATA_DB" \
  --collection-database /root/qtrade-local/unified-data.sqlite \
  --source-revision "$REV" --modes SHADOW
```

For currently authorized initial research PAPER, select `--modes SHADOW,PAPER`
and supply the actual authorizing actor with `--initialize-paper`. Do not add
that flag as a recovery mechanism to a scheduler. `--once` performs one cycle
then publishes STOPPED; use it for SHADOW inspection, since starting/stopping
PAPER makes subsequent startup require human recovery. Production supervision,
public API/page acceptance and real trading-day evidence belong to integration.

After a human explicitly approves recovery of an existing halted PAPER book,
run the following once in a separate local operator process while the service
is running. `BOOK_ID` is the exact 64-character key in the local `roster.json`;
`ACTOR` identifies that human action. The unique request ID is generated when
omitted; an operator may provide `--recovery-request-id` with a fresh 32-character
lowercase hexadecimal ID. This command writes the actionable request; do not
place it in Supervisor, cron, startup scripts or an automatic retry loop.

```sh
python -m scripts.run_qlib_forward_service \
  --local-root /root/qtrade-local/forward-research \
  --prepare-recovery-book "$BOOK_ID" --recovery-actor "$ACTOR" \
  --recovery-ttl-seconds 300
```

Audit files are local `recovery/requests/ID.json`, `recovery/consumed/ID.json` and
`recovery/receipts/ID.json`. Inspect the receipt and public book state after the
next service cycle. A receipt of FAILED/REJECTED, or a consumed intent without a
receipt, does not grant permission for another request; any fresh recovery must
again be an explicit human action. The consumer inspects at most 1,024 request
files per cycle; preserve old audit files outside the active directory if this
bound is eventually reached.

```powershell
$env:PYTHONPATH='E:/QTrade-git/src'
$env:MYPYPATH='E:/QTrade-git/src'
python -m pytest tests/test_forward_service.py tests/test_forward_paper.py tests/test_forward_paper_adapter.py tests/test_forward_publication.py -q --basetemp=E:/forward-service-checks
python -m ruff check scripts/run_qlib_forward_service.py tests/test_forward_service.py
C:/ProgramData/anaconda3/python.exe -m mypy --platform linux --follow-imports=silent scripts/run_qlib_forward_service.py
```

Focused service tests include competing SQLite writers during freeze, changed
epochs/crash quarantine, exact-day read-only collection replay, restart/mode
fencing, damaged-book isolation, secret-safe diagnostics, rollback/descendant
lineage, current archive discovery, and real execution-core/public-schema
integration before and after synthetic observed sessions.
Recovery checks cover prepare-only behavior, exactly one selected PAPER book,
replay, expiry/future clocks, wrong books/modes/instances, invalid request fields,
failure/crash durability, restart fencing, and actual core reconciliation with
an uncertain PENDING receipt that cannot be cleared automatically.

Remaining source limitations are explicit: stock-only adapter, survivor bias,
uncertified historical PIT/available_at and price adjustments, unknown ST,
suspension, precise limit prices and corporate actions. These unknowns block
simulated orders; no production fills or returns are claimed by this document.
