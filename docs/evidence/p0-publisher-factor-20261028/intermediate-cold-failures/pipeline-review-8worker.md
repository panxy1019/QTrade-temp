# Export pipeline independent review, 2026-10-28

Checked at 2026-10-28T04:56:46.426236+00:00. Readonly code review of the frozen eight-worker candidate. No production source/tests, service configuration, cluster inputs/outputs, books, accounts, orders, credentials, or live-trading settings were changed by this reviewer. Reviewer-created differential/fault-test/report artifacts reside in E:/QTrade-temp. No cluster I/O benchmark was run by this reviewer.

## Disposition

**No code blocker or weakened validation/durability contract was found in the exact four-file candidate below. Performance and deployment remain UNACCEPTED pending the current frozen-input cold-plus-warm test and exact current-output comparison.** A source review and passing local tests do not replace those gates.

The actual current freeze is **104,218 chunks / 4,200 batches**, captured `2026-10-28T04:23:39.263755+00:00`, ledger SHA256 `8f1dc0b9b7807b9339c4bbdc4d98c8c0a6bdf9bad9bfa476152cd7acfe7c03bb`, catalog raw SHA256 `9024a7e22cb9cddf25031afc81e71d99b3b7c23bfb8bcfd6964ea4b91cbf354b`. Its prior successful index has 5,986 shards and explicitly is **not** an oracle for the current frozen snapshot. The old 4,488-shard reference and prior warm timings must not be reused as acceptance for this candidate/current input. Publication freshness, successful Git transfer within its existing 90-second budget, actual production process/source identity and public API delivery still need their own evidence after all deployment gates pass.

## Exact candidate binding

Compared against original committed source `458552de1fea1faf77d02dcd9ec69251a0c364b7`. Local files matched these hashes at differential start/end and again when this report was written. Any subsequent source/test edit requires a new binding and material re-review.

| File | SHA256 of exact working-tree bytes |
| --- | --- |
| `scripts/publish_unified_market.py` | `173773bb14960bc6f0841b47564fd625eec6690c8a170be475991205cc68fb32` |
| `src/qtrade/research/unified_market.py` | `2c38be59279d206f7de331f97117265b44296c6e6a4997198d7ddbac7e52778c` |
| `src/qtrade/market_data/sina_incremental.py` | `c0f783604750ac2648c8dcb2496e2e02994ddb3cf469d6a69e762ebfb9bff63b` |
| `tests/test_unified_market.py` | `a804955d87a121eec1ae4193dc251546f0d9d46b9376577f7b03ca3432173aae` |

## Pipeline, failure and durability audit

1. `build_objects` in scripts/publish_unified_market.py:390 builds the original signed shard, calls the complete `validate_shard`, encodes once, hashes the exact encoded bytes, records the same reference fields, checks the cumulative unique-object output limit, and only then yields `(digest, raw)`. Invalid shard/encoding/output-limit errors can leave earlier unreferenced immutable objects but cannot reach the pointer write.
2. `_persist_objects` at line82 has `MAX_OBJECT_IO=8`: both the executor worker count and initial/subsequent submission count use this bound. The pending set admits a replacement only for completed tasks. The main thread generates/validates shards; worker threads handle only physical file hashing/writing. There is no shared per-row Decimal arithmetic in workers or concurrent mutation of entries/cache/lineage.
3. New object creation still writes a fresh temporary file, flushes, **fsyncs every file**, closes, links without overwriting the digest name, removes the temporary name, and cleans temporary files on error. The standalone `_write_object` default retains its original immediate directory-fsync behavior. Deferral is used by the bounded pipeline only.
4. Existing files are still rejected when symlinked or when actual byte hashing disagrees with the digest. Successful warm all-existing passes do **not** skip durability: after all futures succeed, the objects directory, unified-market directory, market-public destination, and its unified-data parent are synced in that order. These cover the new directory entries created along this export path. Earlier failed attempts that left all files present still execute the same four directory syncs on retry.
5. Every future result is inspected. On worker error, generator error or deadline expiry, pending work is cancelled where possible and executor exit joins running tasks before the error returns. No worker remains writing after a failed export has returned. The deadline is cooperative around source/generator/I/O checks; a blocked filesystem call is joined rather than forcibly cancelled. That is not a hard interruption guarantee at exactly45seconds and must not be represented as one. A late completion still cannot pass the later deadline gate and replace the index.
6. Only after object persistence/directory durability does the exporter re-read selected ledger membership and master universe identity, build and fully validate the index, and check the deadline immediately before the existing atomic index writer. Source/ledger/index/deadline or object-directory-sync failures therefore preserve the old pointer and previous success state; new orphan objects remain unreferenced. Retained conflict/quarantine behavior, excluded-chunk handling, immutable source reads and manifest rechecks remain enforced.
7. The existing atomic index writer's commit boundary matters: scripts/atomic_json.py fsyncs the index temporary file, **replaces the pointer, then fsyncs its parent directory**. A failure after replacement is an existing uncertain-commit boundary and cannot honestly be described as guaranteed old-pointer preservation. The same applies to an error writing the success-statistics file after index commit. This candidate does not introduce or resolve that existing behavior. The old-pointer findings in this report apply to precommit source/validation/pipeline/deadline/ledger/index-validation/object-directory-sync failures.

## Validation and memo audit

- `_ConstructedKlineRows` is private and scoped to a single shard's fixed symbol/security type/frequency/observation/calendar. It caches only the context result per requested date. Every bar still executes the same exact field/numeric-string/finite-value/context/time/VWAP checks and the original decoded parser/independent BarQualityGate call. The shard still separately checks duplicate and ordered timestamps; no bulk cross-bar jump policy is added or removed.
- The shard's new outer Decimal context has the same precision28 and ROUND_HALF_EVEN settings as the old per-bar wrapper, clears flags before each bar, and catches the same inner KeyError/TypeError/AttributeError/DecimalException cases as `validate_bar`. The public `validate_bar` API and its wrapper remain. Outer shard wrapping remains distinct from bar wrapping. Independent old-source comparisons verify success values, exception class/text and caller Decimal flags with default, foreign precision/rounding, Inexact-trapping, and pre-set Inexact flags.
- `_CapturedKlineReader` is private and fresh per export. It memoizes identity, open-day checks and cutoff results, each capped at4096 entries; a full map computes normally without admitting another entry. A different Calendar object falls back to full context checks. Custom Calendar subclasses retain their own full `expected` calls. No captured body, parsed row, quality admission or source replay result is cached by this reader.
- The concrete Calendar cutoff is constant within each Shanghai minute: daily selection changes at16:30:00; minute selection subtracts exactly60seconds, branches at whole-minute session boundaries and floors to a five-minute endpoint. The aware timestamp is normalized to Shanghai, its seconds/microseconds are removed, and the key includes kind. Thus differently formatted offsets representing the same instant preserve identity, while adjacent cutoff minutes remain separate. Naive observation clocks and invalid requested days/frequency/identity are still rejected before raw decoding.
- Every public raw `completed_klines` call and private captured-reader `parse` call reaches `_decode_klines` after its context checks. That function still checks the **2,000,000-byte raw limit and `json.loads(raw)` on every response**, including memo hits. The original post-decode parser body is AST-identical: list/count1..1970, row shape/day, required fields, completed session grid, requested-day selection, OHLCV, daily uniqueness and optional full-session validation remain. `number`, `_amount`, `IncrementalBar`, `completed_quotes`, and `reconcile_day` are independently AST-identical to baseline.
- Actual raw/normalized byte hashes, raw pack/decompression/count/body-hash lineage, Parquet schema/count/row hash, response-source/HTTP/completion/received-clock checks, per-symbol after/window equality, daily raw quote reconciliation and full normalized-row/source-hash comparison remain. Source-hash order is preserved by grouping the captures in their original sequence. The new per-batch expected-window map reuses only the same computed set for equal aware `after` instants.
- Sorted bar-field order, amount_basis position, signing and exact encoded shard digest/reference fields remain unchanged. Source rows and normalized cache still get complete validation on the existing paths, including rows discarded as outside the publication window.

## Budgets and authority

45-second export, 512,000,000 source-byte reads, 128,000,000 estimated decoded batch-cache bytes, 192,000,000 output bytes and 90-second Git limits remain unchanged in code. The pipeline has at most eight in-flight object tasks and retains the prior bounded output collection. The new calendar/identity metadata maps are separately capped at4096 each; the existing128MB statistic estimates the normalized batch cache, not all process objects or a total RSS cap. This report does not relabel that estimator as a whole-process memory guarantee. LIVE_TRADING_ENABLED remains required false, with no execution/gateway/recovery authority added.

## Independent verification

- **204 passed / 4 Windows skips** in the focused repository suite,107 unrelated tests deselected, no failures/errors. Skip reasons: Windows account cannot create symlinks. Selected coverage includes bounded8-task I/O, file fsync, cleanup, raw parsing, shard/bar equivalence, grouped replay, cache contexts, independent full-bar validation and corrupted retained/out-of-window rows. Windows directory-fsync stubs do not establish real Linux filesystem durability; require the separately bound Linux run before acceptance.
- **10 passed, no skips/failures/errors** in reviewer-owned external fault cases: every one of the four directory/ancestor-fsync failure stages preserves pointer bytes/mtime and success-state bytes; each all-existing retry verifies rather than rewrites objects and syncs all four directories. Generator, shard validation, encoding and output-limit faults also preserve old state and join workers. A deliberately late worker is joined before return and cannot commit. Warm all-existing object hashes and all directory syncs precede pointer/state publication.
- **10,664 differential comparisons, zero differences** against the original committed implementation: 2000 standalone-bar, 688 public raw-parser, 720 private reader, 3240 calendar/offset boundary, and 4016 shard comparisons, across four Decimal contexts. Multi-row cases exercise same-date context reuse, different/closed days, duplicate/order behavior, large cross-bar jumps and rounding/flags. Private-reader tests include malformed JSON, oversize bodies, invalid contexts and memo-hit paths. All three map capacities were exercised to4096+2 inputs and remained exactly4096 entries.
- Code review/finite tests establish no observed regression for the bound bytes; they do not establish production throughput, crash-injection durability on the live filesystem, exhaustive equivalence or the still-missing current frozen-output performance/oracle proof. No remote benchmark results for this final candidate were consumed by this report.

## Reproduction

From E:/QTrade-git in PowerShell, with fresh review-only temporary directories:

```powershell
$env:LIVE_TRADING_ENABLED='false'
$env:PYTHONPATH='src;.'
python -B E:/QTrade-temp/export-pipeline-review-differential-20261028.py
python -B -m pytest -q -o addopts= -p no:cacheprovider E:/QTrade-temp/test_export_pipeline_independent_review_20261028.py --junitxml=E:/QTrade-temp/export-pipeline-review-independent-tests-20261028.xml
python -B -m pytest -q -o addopts= -p no:cacheprovider tests/test_unified_market.py -k 'object or gate or directory_sync or export_context or grouped_replay or every_published or invalid_retained or outside_window or raw_kline or constructed' --junitxml=E:/QTrade-temp/export-pipeline-review-focused-tests-20261028.xml
```

Actual runs used freshly generated, previously nonexistent short `E:/QTrade-temp/prv-<random>` basetemp directories to avoid unrelated Windows long-path limits. The baseline revision is explicitly pinned inside the differential script; current four-file hashes are recorded and rechecked. Reproduction creates test/report artifacts only and does not invoke cluster services or production publication.

## Evidence binding

| Artifact | SHA256 |
| --- | --- |
| `E:/QTrade-temp/export-pipeline-review-focused-tests-20261028.xml` | `f74348646a93c3d753ab08349413dd1ebd160718f0cf076233628ddf10b16bf0` |
| `E:/QTrade-temp/export-pipeline-review-independent-tests-20261028.xml` | `5308f07d7c6f396ba0e2f4d470f72260d668c28fbdafc62f02c7d0b927bb4916` |
| `E:/QTrade-temp/export-pipeline-review-differential-20261028.json` | `0f4be24e7ec9edb041dd45f93a3e2a2f1bb4922438e9bb67624cbfebe2f64bea` |
| `E:/QTrade-temp/export-pipeline-review-differential-20261028.py` | `722e7315a4821e1834e467d615d5b1e5c6458b0a2f0a63f7750c17ba228b00b6` |
| `E:/QTrade-temp/test_export_pipeline_independent_review_20261028.py` | `2611bcc61e880552cc8204c3a780744d4551f0e3983d0de5b10ec179ce4ad58d` |
| `E:/QTrade-git/docs/evidence/p0-publisher-factor-20261028/current-freeze/freeze.json` | `8ba620229aad1736867149a853732ff5e0e2bb6f7813e910417b05847dd45f29` |

Final disposition for this exact candidate: **no source code blocker found; deployment/performance pending the current4200-batch frozen cold/warm and exact-current-output gates.** Retain previous failures as separate evidence. The earlier export-candidate-contract-review-20261028.md remains unchanged and has its original, older candidate scope.
