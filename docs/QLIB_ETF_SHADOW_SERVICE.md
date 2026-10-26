# Separate ETF allocation and SHADOW service

`scripts/run_etf_shadow_service.py` supervises the ETF research contract from
`src/qtrade/research/etf_research_contract.py`. It reads existing verified
`B/online-research/etf-trend/<diagnostic_id>` archives. It performs no source
request, opens no stock ledger or model/data database, and has no gateway,
PAPER initialization, recovery, or execution admission. Keep
`LIVE_TRADING_ENABLED=false`.

The existing twelve stock books and their service need no restart. This process
owns a separate persistent local directory and publishes only
`B/workflow/public/etf-forward.json`. The shared publisher/API/UI integration is a
separate change; generating this file alone does not prove public delivery or
rendered acceptance.

## Lifecycle and durable evidence

Run exactly one owner for a given public destination, using one persistent local
root. A Linux `flock` on `<local-root>/service.lock` rejects another process with
that same root. This is a local process lease, not a distributed ownership claim
over different hosts or local roots. Preserve the root across process/container
restarts and retain its private archives for audit.

For the default ETF contract, the service records an immutable intent under
`contracts/<contract_id>/sessions/<execution-day>/intent.json` before freezing a
target. It binds the diagnostic ID, contract, actual issue time, expected next
open and original implementation revision. A release change or latest-pointer
change cannot silently replace that session's binding. The target is written by
the contract helper to `targets/<target_id>/target.json`, followed by an immutable
`target-binding.json` receipt in the session directory.

The source must match the calendar's current completed research day, have
verified close inputs, and have actual source receipts no later than the real
clock. A new target requires a real freeze strictly before the next exchange
open. The production call lets the contract sample the actual clock after its
expensive raw replay. The helper also requires `targets/<target_id>/admission.json`:
this immutable receipt binds the target hash and an actual clock sampled after
the target write became durable, strictly before the open. A target file alone
is never exported. A late durable write or a crash before its admission receipt
leaves an unadmitted file that cannot be resurrected by a restart, even with a
new implementation revision. The CLI exposes no clock override. If a crash leaves
only an intent, a restart after the open cannot manufacture the missing target.
If the target and its admission were already sealed before the crash, their
original bytes and revision can be replayed after the open. An unavailable
diagnostic never becomes an empty
allocation; a verified ready diagnostic with no positive trend can legitimately
produce an empty target with no account value.

After the exact next session has completed, the service discovers its archived
diagnostic even if the latest pointer has advanced again. Every candidate used
undergoes receipt and raw replay. Multiple different valid archives for an
unpinned observation day block observation with
`ETF_SHADOW_OBSERVATION_SOURCE_AMBIGUOUS`; the service does not choose one by
directory order. `observation-source.json` pins the selected diagnostic before
the helper writes an observation. A crash can resume that same source without
silently switching to a later release. The observation and a `completed.json`
receipt remain immutable. A missing next-session source stays explicitly
pending, even when a later session is available.

Each observed record contains selected target weights, actual selected closes
and missing target inputs. Target weights are not holdings. SHADOW has no fills
or portfolio return; all order/fill/holding arrays remain empty and cash, NAV and
forward return remain null. ETF lot, fee, settlement, limit, split and distribution
rules remain unadmitted. Native adjustment and historical universe/PIT limitations
are preserved. This channel does not certify the full STEP1 or historical ETF
comparison.

## Bounds and failures

The service runs every 60 seconds with a 900-second hard process deadline. Its
data-volume gate requires 100 GiB free space plus 16 MiB headroom on the base
source/public filesystem. Its separate local operational-filesystem gate requires
1 GiB free headroom for bounded target, intent, observation and receipt metadata;
this directory does not duplicate the raw source archives. A 32 GiB operational
overlay with 28 GiB free therefore passes, while the data-volume 100 GiB reserve
remains mandatory. Either failed gate blocks new target, intent or observation
writes and reports its own fixed error code. A small validated heartbeat
publication is still allowed when these gates block archive writes.
No collector/provider priority or source backoff is changed, because the process
makes no network/source request.

The signed `qtrade.etf-shadow-service.v1` envelope is validated by
`validate_etf_shadow_publication` before atomic replacement. It includes the
canonical ETF contract, current service revision, actual generated/heartbeat
times, RUNNING/DEGRADED/STOPPED state, at most 30 recent target/observation pairs,
at most 32 fixed safe error codes and the full limitations. Its serialized size
is at most 1 MB. If necessary the oldest public entries are dropped with an
explicit truncation error; the private evidence is retained. Older completed
records leave the public window but their local completion and signed document
bindings remain checked. Pending older sessions continue to be processed.
Archive and intent directory scans are bounded at 4,096 entries and fail closed
when exceeded. There is no automatic pruning or scope reset.

Source verification failures are isolated from independent targets. A corrupt
target/observation is omitted with a replay error and is never overwritten by a
replacement. A cycle-level failure publishes an empty DEGRADED envelope with a
fixed error, rather than claiming unverified prior entries. Publishing itself
can still fail on unavailable storage; consumers must enforce heartbeat
freshness. SIGTERM/SIGINT request a graceful end of the current bounded cycle,
stop further cycles and publish STOPPED after read-only replay. A hard deadline
or abrupt termination may leave a stale heartbeat; the supervisor can restart
the SHADOW process and replay immutable evidence. No execution recovery or
real-order permission is implied by this restart.

The bundled calendar is limited to its verified horizon. A later-year deployment
requires a separately reviewed calendar update. The service provides private raw
replay and signed data-only publication; SHA-256 integrity receipts are not a
cryptographic operator signature or a historical point-in-time certification.

## Reproduction

Run the focused checks from the repository root. The short Windows temporary
directory avoids MAX_PATH failures in deeply nested raw-capture fixtures.

```powershell
$env:PYTHONPATH = 'E:/QTrade-git/src'
$env:MYPYPATH = 'E:/QTrade-git/src'
python -m pytest tests/test_etf_shadow_service.py tests/test_etf_research_contract.py tests/test_etf_shadow_publication.py -q --basetemp=E:/es1
python -m ruff check scripts/run_etf_shadow_service.py tests/test_etf_shadow_service.py
python -m mypy --explicit-package-bases --platform linux scripts/run_etf_shadow_service.py
```

Linux supervision command, using an existing verified ETF source archive and a
dedicated persistent root:

```sh
LIVE_TRADING_ENABLED=false PYTHONPATH="$PWD/src" \
python -m scripts.run_etf_shadow_service \
  --base /path/to/existing/qtrade \
  --local-root /root/qtrade-local/etf-shadow-service \
  --source-revision <reviewed-40-character-git-revision>
```

`--once` performs one bounded cycle and then writes a STOPPED heartbeat. For
continuous freshness use the supervised process without that flag. The focused
fixtures test actual raw replay, release and crash idempotence, exact next-day
observation, missed-open/future-clock rejection, ambiguous and corrupt source
isolation, reserve/stop gates, invalid bindings and bounded publication. Synthetic
fixture dates do not establish that a real production session has completed.
