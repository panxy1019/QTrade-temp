# Daily Provider input binding across rotating collection snapshots

The collector can correctly publish a fresh `PASS_FINISHED` progress snapshot
whose `required_through` contains only `5min`. This happens before the current
session closes; that snapshot is not a catalog of every completed daily task.
Indexing `required_through['day']` stopped daily preparation with `KeyError`, and
choosing batches from a partial task list could silently change preparation scope
or reset per-stock retry progress.

`run_qlib_data_service.py` and `prepare_qlib_incremental.py` now accept optional
`--collection-database`. The supervised service forwards it to its bounded child,
along with the exact exchange-calendar completed day and the existing Provider's
`observation_universe_version`. No collector state, process ownership or active
collection table is changed. The collection connection uses SQLite `mode=ro` and
one read transaction on exactly that universe, frequency `day` and trading day.

Daily tasks must be terminal SUCCEEDED/FAILED. The signed current master, when
available, supplies the complete required target scope, including non-Provider
targets such as ETFs. Its universe must match the pinned source universe. Without
that master proof, every frozen dataset stock must have an exact daily task;
missing rows or a pending task cause WAITING. Desired/completed daily clocks must
match the selected session's 15:00 Shanghai close. Failed tasks remain explicit
and do not masquerade as usable current inputs.

The first terminal metadata snapshot is written immutably before any batch read
under `unified-data/qlib-preparation/input-scopes/SCOPE/collection.json`. SCOPE
binds the base dataset hash, source universe, completed day and source revision.
The snapshot records its real freeze time and whether the evidence came from the
collection ledger or the legacy signed progress file. Only verified actual
receipts, raw captures and Parquet are admitted into the preparation request;
frequency/day/universe, receipt completion clock, successful symbol outcome and
daily amount proof are checked. Corrupt or unavailable batches are excluded with
an explicit reason, rather than counted as qualified inputs.

The frozen request stores selected batch IDs, receipt hashes and metadata digest.
Subsequent passes reuse that request even if the collector rotates to minutes,
retries a failed symbol or changes its latest success pointer. Stock checkpoints,
source failures and backoffs therefore remain in the same preparation identity.
Files are still reverified when used. A later collector improvement does not
silently broaden an already frozen scope; a separately reviewed source scope is
needed to change that day's frozen inputs. No data version or model is activated
by preparing these inputs.

In particular, FAILED source rows and failed receipt content/contract validation
are frozen exclusions for that scope. A collector retry that later succeeds does not remove
the exclusion. Replacing a frozen input scope requires explicit review and a
new scope identity; this fix provides no automatic reset/delete/rebind action.
Transient storage `OSError` is different: the frozen metadata remains, but no
request or permanent exclusion is sealed. The existing service retry deadline
then permits another read of the same frozen batch IDs after storage recovery.

Without `--collection-database`, a missing daily entry or absent daily progress
file returns `WAITING_FOR_CURRENT_DAILY_INPUTS`. The supervised service waits
before creating its day request/attempt intent when the daily marker is absent.
An empty, missing or nonterminal exact ledger scope also waits before a new day
intent is created. Previously accepted frozen scopes retain their stable resume.
The legacy path still requires exact daily scope and terminal task proof when a
daily marker is present. The service retains collection priority, 100 GiB reserve,
the independent 900-second child deadline, preparation/build process locks,
5-minute preparation retry deadline, 30-minute build backoff and final head/epoch
checks. A training/strategy change is outside this fix.
Selected due/new stock work runs before completed checkpoint verification, so a
slow completed prefix cannot repeatedly exhaust the fresh-work budget. Completed
outputs are still verified before publication, and the existing deadlines remain.

For the existing supervised data service, add only the explicit local collection
database option to its verified launch command:

```sh
python -m scripts.run_qlib_data_service \
  --base "$B" --database "$DATA_VERSION_DB" --qlib-python "$QLIB_PYTHON" \
  --source-revision "$REV" \
  --collection-database /root/qtrade-local/unified-data.sqlite
```

For read-only gate acceptance, inspect the exact daily scope without calling
`run`, stock adaptation, Provider building or any source fetch:

```sh
python - "$B" "$PREVIOUS_VERSION_ROOT" "$UNIVERSE_ID" <<'PY'
from collections import Counter
from pathlib import Path
import json
import sys
from scripts.prepare_qlib_incremental import daily_tasks, load_signed
base, previous = Path(sys.argv[1]), Path(sys.argv[2])
rows = daily_tasks(base, load_signed(previous/'dataset.json'), '2026-10-26',
                   sys.argv[3], Path('/root/qtrade-local/unified-data.sqlite'))
print(json.dumps({'gate': 'WAITING' if rows is None else 'TERMINAL_DAILY_SCOPE',
                  'daily_tasks': len(rows) if rows is not None else None,
                  'states': dict(Counter(r['state'] for r in rows or []))}))
PY
```

This diagnostic reads the signed master and existing SQLite rows only. It makes
no new source requests and writes no input scope, preparation, collector or data
ledger. Successful terminal-task inspection alone does not verify batches or
claim a new Provider; actual batch verification remains in the bounded prepare
stage.

A standalone preparation can additionally pin `--day YYYY-MM-DD` and
`--universe-version HASH`; both must agree with completed-session and frozen base
source evidence. Current-day Provider publication and next real-session inference
still require their existing independent validators. This document claims no new
production version, forward return, full STEP1 acceptance or PIT certification.

```powershell
$env:PYTHONPATH='E:/QTrade-git/src'
$env:MYPYPATH='E:/QTrade-git/src'
python -m pytest tests/test_daily_collection_binding.py tests/test_prepare_qlib_incremental.py tests/test_qlib_data_service.py -q --basetemp=E:/dp-check
python -m ruff check scripts/prepare_qlib_incremental.py scripts/run_qlib_data_service.py tests/test_daily_collection_binding.py tests/test_prepare_qlib_incremental.py
python -m mypy --platform linux --explicit-package-bases scripts/prepare_qlib_incremental.py scripts/run_qlib_data_service.py
```

The focused integration checks use actual immutable source receipts/Parquet and
stock adaptation with a synthetic factor source. They verify minute-only rotating
progress, exact source/day selection, complete terminal scope, read-only ledger
bytes and no owner claims, metadata freeze before batch reads, unchanged bindings
after collector retries, raw corruption exclusion, WAITING before intent creation,
source/code identity, and unchanged existing retry/rollback/storage behavior.
