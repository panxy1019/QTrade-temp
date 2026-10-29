# P0 checkpoint independent review — 2026-10-28

Scope: read-only review of the publisher, immutable checkpoints and validation
receipts. The reviewer added only an independent test module and this evidence.
No production process, live account, ACTIVE/PAPER state or original experiment
was changed. `LIVE_TRADING_ENABLED=false` throughout the isolated Linux run.

## Frozen source and final result

| File | SHA-256 |
| --- | --- |
| `scripts/publish_unified_market.py` | `64caab0023626ef0f531f563c3a392d4a2429a9a6c004c5789af17321bbd8d8a` |
| `scripts/unified_market_checkpoint.py` | `0f2c512f7abf7cbc99d34def6edfa7dfeaa73e564031a36a5b33653759a2f485` |
| `src/qtrade/research/market_validation_receipts.py` | `2152eed8405ace883c2bd8de388b72ec42875a3feaffa314e616c58c1147f79a` |
| `tests/test_unified_market_checkpoints.py` | `0488a5edbd64f882171036ea3db1292d6e1a5ee581aad2165282c86377bd535d` |
| `tests/test_unified_market_checkpoint_independent.py` | `4e61709f707986d94248773dfd32dd496245231d4d654d2d2249a4ec01e05e17` |

The final independent suite passed **33/33, zero skipped**, in 7.46 seconds;
the complete child took 8.203 seconds. Evidence is archived under
`docs/evidence/p0-publisher-factor-20261028/independent-review-final-20261028/`.
Its manifest binds each original run, driver, overlay, test result and source
inventory. Final remote run:
`/root/qtrade-local/operator-evidence/checkpoint-review-20261028T105052Z-0a61a260`.

The package is the exact Git baseline
`f036bca197272df4b1f885dfe8165a27b67c36cd` plus the five files listed above.
All 2,072 files matched their byte lengths and SHA-256 hashes before and after
the run. NFS free space was 286,229,790,720 bytes and local free space was
23,632,809,984 bytes, above the 100 GiB / 2 GiB reserves. Test state lived only
in the owned local operator directory. This was a functional run, not an NFS
performance benchmark.

Reproduce from the frozen package with the archived `run_checkpoint_review.py`
and `source-manifest.json`, or on an equivalent POSIX checkout:

```sh
LIVE_TRADING_ENABLED=false PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. \
  python -B -m pytest -p no:cacheprovider \
  --basetemp=/owned/local/path/pytest \
  tests/test_unified_market_checkpoint_independent.py
```

## Findings and resolution evidence

The original seven independent probes produced five real failures and two
passes on the original source. Later probes preserved additional failures;
none were converted to xfail or counted as passed.

| Boundary | Preserved failure | Final behavior |
| --- | --- | --- |
| Orphan receipt after failed directory fsync | Local SQLite imported a non-durable sidecar | Fsync the sidecar directory and ancestor chain before local index commit |
| Durable prepare / public replace | Head, exclusion or overlapping admission changed during prepare | Recheck head, selected ledger, consumed master fields and source hashes immediately before replace |
| Deadline | Prepare crossed the deadline but still moved head | Reject before public replace |
| Validation policy source | Changing `qtrade/data/__init__.py` left old receipt reusable | Include the import policy source in the replay fingerprint |
| Checkpoint clock | Future creation time was reusable | Require aware, nonfuture time equal to index publication time |
| Public head fsync failure | Ambiguous new pointer was copied as retained success | Raise `MarketPublicationUncertain`, record `UNCERTAIN`, stop transport |
| Recovery fsync failure | The next pass could fall through to retained-success transport | Raise the same uncertainty type/code and preserve the original I/O error as cause |
| Missing/corrupt durable receipt | Valid local SQLite payload remained authoritative | Require the actual bounded sidecar bytes and payload hash on every hit; miss on missing/corrupt data; propagate storage I/O errors |

The final 33 tests also cover physical hash invalidation with unchanged size and
restored mtime, manifest/metadata changes, future receipts, strict JSON inputs,
immutable result conflicts, sidecar recovery and commit ordering, target-field
projection, per-frequency window fingerprints, and complete selected chunk
sets. Target projection binds exactly the consumed `symbol`, `instrument_id`,
`security_type` and `name`; changes in those fields invalidate the affected
view. Mtime supplies no integrity evidence. Strict JSON tree validation remains
at put/decode boundaries after the encoding optimization.

Original red runs are retained in the evidence manifest, including the
five-failure run, late deadline/overlap probes, three public uncertainty probes,
four sidecar probes and repeated recovery failure. The 32/1 run at
`checkpoint-review-20261028T104300Z-d3aa35d0` is also preserved: its sole failure
was an old test-message expectation after the new uncertainty contract. The
final test checks the dedicated type, exact code and original `OSError` cause.

## Remaining limits and next gates

No further blocker was found in the reviewed scope. Recovery establishes the
visible head directory's durability before loading a prior checkpoint; receipt
and checkpoint names are fsynced before local-index commit/public replacement.
The subsequent pass still physically hashes source bytes and verifies metadata.
The local SQLite database is a rebuildable index, not unique durable receipt
evidence. Receipt-sidecar reads are separately reported as
`receipt_sidecar_bytes_read`; the source-byte counter is not total storage I/O.

The publication lease is a cooperative same-host `flock` tied to the configured
local database path. All writers must use that path, and a previous owner must
exit before replacement. It is not a distributed compare-and-swap or deployment
fence. Concurrent mutation by writers that bypass the lease remains outside this
contract. The rechecks narrow the commit window under that ownership assumption.

Synthetic POSIX fault injection does not prove NFS server power-loss behavior or
mount guarantees. Actual NFS cold/restart performance, full 5,986-reference and
original-458 oracles, deployment fencing and normal-round/recovery gates remain
separate acceptance work. Earlier source revisions' timing/oracles cannot be
attributed to this frozen source. This review grants no live, PAPER, model
activation or strategy promotion admission.
