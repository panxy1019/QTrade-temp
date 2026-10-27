# Unified recent market bars

The current-market chart reads the observed target universe through `/unified/bars`.
Legacy dataset files remain archives. They do not fill missing unified bars or extend
the new endpoint's advertised history. This is data observation, with no execution,
PAPER admission, historical PIT, or adjustment certification.

## Export and publication

`scripts/publish_unified_market.py` uses the existing `UnifiedBatchStore` and Sina
normalizers. vn.py provides no existing public incremental-lake export component;
the change reuses the project's actual collection and admission implementation.

The exporter reads SQLite using `mode=ro`, `query_only=ON`, and an explicitly closed
short transaction. It never constructs a collection queue or claims its writer lease.
SQL selects the exact current universe and recent windows: at most 20 daily exchange
sessions and 3 five-minute sessions. It selects only COMPLETE batches and excludes
quarantined chunks. A batch can have failed peer symbols; only its exact SUCCEEDED
symbol outcome, chunk bounds, count, and manifest binding can supply bars.

Actual raw and normalized bytes are hashed on every pass. On first admission, original
successful, complete responses are replayed at their bounded receipt times. Daily
quotes and klines must reconcile, including amount; minute batches must cover the
declared after/target interval. Every normalized field and raw lineage must equal the
replay. Each export also applies the current public schema to every selected bar.
Different accepted values at the same symbol/frequency/timestamp quarantine that
whole series. Unknown minute amount/VWAP remains null. Missing bars remain gaps.

Every target gets both index entries, including NO_DATA and QUARANTINED. Immutable
JSON shards are content addressed and contain no export heartbeat. Daily bars share
one bounded shard; minute bars have one shard per session. The signed root index
binds the observed master, universe, calendar, implementation file hashes, all object
checksums/counts/bytes, window boundaries, and publication clock. Its pointer is
replaced only after all objects exist and the selected ledger evidence remains valid.
Transient IO or export-budget failure retains the dated previous index.

The existing publisher copies only referenced verified objects and the index through
its existing Git queue. No separate remote or publication authority is introduced.
The publication checkout also maintains hardlinks at
`unified-market/.delta/20261027/M301001-<identityhash16>.json` for each minute session
and `unified-market/.delta/day/D301001-<identityhash16>.json` for each daily window.
The suffix is the first 16 SHA-256 hex characters of `symbol|frequency|group`, where
group is the Shanghai session date in `YYYYMMDD` form or `day`. These stable names
help Git select the preceding shard as its delta base. Both paths stage the same
Git blob, so the aliases add tree entries without duplicate payload objects.

An unchanged inode skips relinking. An update hardlinks the verified immutable
publication object to a temporary sibling and atomically replaces only the alias;
it never writes through the old alias or changes old content-addressed bytes.
Symlink files/parents and resolved path escapes are rejected. Aliases are transport
hints only: the signed index, immutable hash paths, schema, API and authority checks
remain unchanged. Hardlink support on the publication filesystem is required;
failure preserves the previous index. Old aliases and historical objects are not
deleted by this change.

## Bounds and cache

An export allows 45 seconds by default (callable maximum 120), 60,000 selected chunks,
12,000 selected batches, 512 MB source reads, 192 MB public objects, 4 MB index, 1 MB
per shard, and 2,000 bars per shard. Source Parquet is limited to 2,400 rows and 16 MB
declared uncompressed row-group bytes per batch. Cache limits are 100,000 decoded
rows and 128 MB estimated decoded bytes (canonical JSON length plus 1,024 bytes per
row); expired batches are evicted and new entries exceeding a limit are not cached.
This avoids clearing the whole working set during sequential scans. This estimate is an operational bound,
not an RSS measurement. Cache only avoids repeated decompression and raw parsing;
actual immutable hashes, ledger status, and current row schema still get checked.

Exporter statistics record duration, source bytes, selected/replayed/cached batches,
cached rows/estimated bytes, public files/bytes, quarantined series, and index identity.
Real cluster timing/RSS evidence must be captured separately; fixture timing is not
production capacity evidence. A first cold export can exceed budget, in which case
there is no fresh publication claim. Existing historical objects are not deleted.

## API and UI contract

`GET /unified/bars?symbol=000063&frequency=day|5min&start=YYYY-MM-DD&end=YYYY-MM-DD`
accepts an optional current index hash `version` and `limit=1..2000` (default 500).
Absent range dates select the exported window. A version mismatch returns 409 so the
client can refresh its selection; an unknown target returns 404, invalid parameters
422, and unavailable/invalid publication or objects 503. No private lake is read by
the API, and importing this router does not import cluster scripts.

The response includes universe/index/entry identity, actual exported availability,
requested-range state, missing timestamps, explicit bounded window, publication and
check clocks, expected current end, native price basis, and decimal-string bars.
`actual_end` and source `freshness` refer to the full exported series, while `state`
and `gaps` describe the requested range. Querying an older date does not make an
otherwise current dataset stale. A publication older than 180 seconds is explicitly
STALE_PUBLICATION; old valid bars may be displayed with that warning. All responses
use `Cache-Control: no-store`. There is no legacy price fallback.

## Reproduction and configuration

Run from the deployed checkout with its environment and `LIVE_TRADING_ENABLED=false`:

```powershell
$env:PYTHONPATH='E:/QTrade-git/src;E:/QTrade-git'
$env:MYPYPATH='src'
python -m pytest tests/test_unified_market.py -q -o addopts='' --basetemp E:/qm-focused
python -m ruff check src/qtrade/research/unified_market.py src/qtrade/api/unified_market.py scripts/publish_unified_market.py tests/test_unified_market.py
python -m mypy --explicit-package-bases src/qtrade/research/unified_market.py src/qtrade/api/unified_market.py scripts/publish_unified_market.py
```

Production standalone export (local files only; no network or Git push):

```sh
PYTHONPATH=src:. /usr/bin/time -v python scripts/publish_unified_market.py \
  --base "$QTRADE_DATA_ROOT" --database /root/qtrade-local/unified-data.sqlite
```

Set `QTRADE_DATA_ROOT` to the existing data directory containing
`unified-data/public/unified.json` and `unified-data/lake`; it is not the separate
directory that holds the SQLite ledger. The existing publisher accepts optional
`--unified-market-database` with that exact
database path. `--interval-seconds` accepts 60 through 300 and defaults to 300 for
compatibility. An operator should use 60 or 120 only after measuring export/push
cost. No private service wrapper or environment is automatically modified.

Publication Git commands run in a newly owned POSIX process group with the
existing 90-second command limit. Cleanup terminates the group, waits up to two
seconds, then kills remaining members and bounds direct-child reaping. A timed-out
leader cannot leave an ordinary SSH/send-pack descendant uploading alongside the
next attempt. Windows is rejected before launch. This does not undo a ref update
already accepted by the remote; verify actual remote/public state after a timeout.
The real process-tree regression tests use local artificial programs, no network
or credentials: `pytest tests/test_publication_git_process_group.py` on Linux.

The first large public-object migration can exceed the ordinary limit on the
observed slow Git uplink. Keep the publisher paused during an explicitly bounded,
single-owner bootstrap; preserve original snapshot dates and verify the remote
before resuming. Collection and PAPER need not restart. A bootstrap success is
not evidence that ordinary subsequent cycles meet the freshness contract.

Before the first new frame using stable aliases, seed aliases from the already
published frame in a separate Git commit/push. Keep one publication owner, verify
the publication checkout is clean and its HEAD equals the remote branch first,
and verify that seed reaches the remote before resuming ordinary publication.
`seed_existing=True` reads only the existing publication index and its verified
referenced objects. It neither exports source data nor rewrites the pointer's bytes,
publication time or filesystem modification time, and rejects a non-null database.
This local-only reproduction prints the paths to stage with the existing Git queue:

```sh
PYTHONPATH=src:. python - "$QTRADE_DATA_ROOT" "$QTRADE_PUBLICATION_ROOT" <<'PY'
import json
import sys
from pathlib import Path
from scripts.publish_unified_market import publish_market_files
print(json.dumps(publish_market_files(Path(sys.argv[1]), Path(sys.argv[2]),
                                      seed_existing=True)))
PY
```

Seeding is transport preparation, not a freshness refresh. The local thin-pack
experiment motivating this change reduced a 1,500-symbol shared-receipt frame
from 3,679,943 to 714,097 bytes; actual remote pushes, subsequent ordinary-cycle
timing and public freshness still require separate production evidence.

Availability is limited to already admitted incremental lake data. If daily collection
has one day, this endpoint has one day; a 20-session window does not claim 20 observed
sessions. Public API delivery, all-target production coverage, latest-bar latency,
frontend behavior, and real exporter resource cost require the separate deployment
acceptance evidence. This implementation alone does not complete whole STEP1.
