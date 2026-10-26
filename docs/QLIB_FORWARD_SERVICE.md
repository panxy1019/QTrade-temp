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

Remaining source limitations are explicit: stock-only adapter, survivor bias,
uncertified historical PIT/available_at and price adjustments, unknown ST,
suspension, precise limit prices and corporate actions. These unknowns block
simulated orders; no production fills or returns are claimed by this document.
