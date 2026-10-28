# One-ahead source prefetch and physical-read accounting review

**No remaining source code blocker found in the exact five-file candidate below.** The original failed-read budget defect is fixed and the unchanged independent regression fixtures pass. This review is not deployment approval: the final source still requires current4,200-batch production-equivalent filesystem cold and warm45-second passes, final Git/source binding, and a complete independently reconstructed original458 oracle for the final output. No cluster/NFS input reads or production actions were performed by this follow-up review.

## Exact candidate

| File | SHA256 |
| --- | --- |
| `scripts/publish_unified_market.py` | `81ac2ed440de3a9afcca719f187bf5fe1cffc843292248704ca1427a375848f7` |
| `scripts/unified_store.py` | `123ac2fbf6d31d5bb4f26bd5901b510f39cf5e964bc50baaddba3489fda944b2` |
| `src/qtrade/research/unified_market.py` | `64982440d5c876a58c282fb7c8ab4923d03376864c489e8224565a9aff760bb6` |
| `src/qtrade/market_data/sina_incremental.py` | `c0f783604750ac2648c8dcb2496e2e02994ddb3cf469d6a69e762ebfb9bff63b` |
| `tests/test_unified_market.py` | `8a35e355c711372d238871747d66b0867cfafd25d044e16716c922ae75ce1c0c` |

The owner confirmed the runtime-source freeze; all four runtime hashes were independently checked before and after the tests. The60-test run used test hash `dffff45977ad23192ff10172300ea4d6b2d690585ac4a28d2d959495bfb17bba`. The owner subsequently changed only two old assertions to the final test hash above: the private material signature explicitly permits its keyword-only default-None observer and proves that observer return values cannot replace bytes; an already exhausted source budget now asserts no new physical read and no count increase. Both changes were inspected and those two tests independently rerun successfully. No runtime-source change occurred between these runs. The validator/parser hashes are unchanged from the separately bound encoded-reuse review. The parent explicitly permitted the narrow fifth-file store observer needed to fix physical-read accounting; no Arrow decoding or quality arithmetic optimization was added.

## Read and failure contract

The store-owned `_read_bytes` observer accepts only sizes and stages, never supplied bytes. With no observer it directly follows the original `Path.read_bytes()` path. Ordinary `read_blob` retains the same reference/path/stat/size bounds, actual bytes, hash check and errors; `_BatchMaterial` retains manifest authority/signature, raw/normalized reads, predecode Parquet bound, raw decompression/count/body-hash lineage, schema/count/content and manifest-recheck validation. Default generic store callers do not receive an exporter reservation or a changed cache policy.

With the observer, each actual stat/verified blob size is reserved before its physical read. Actual returned byte length is charged immediately after the read, before JSON parsing or blob hashing can fail. The thread-specific reservation is released exactly once on successful return or read exception. A larger actual read is charged by its real length before a budget exception; a short read is charged by its real length and still faces the original checksum/parsing checks. Missing files fail before reservation; OSError releases reservation and propagates. Actual physical bytes returned by an aborted/unused prefetch remain counted. A read that itself raises without returning bytes has no known returned-byte length; it aborts the export through the original OSError path and cannot be turned into a successful quarantine-only budget bypass.

One shared lock protects actual totals and outstanding reservations across the single source worker and the main-thread manifest recheck. The budget test uses actual+reserved bytes. There is at most one ahead source-material task; the main thread alone performs normalized decoding, replay, cache mutation, per-bar/shard validation and counter/seal handling. The next batch's physical manifest/raw/normalized reads and hashes may overlap those operations. Every future is consumed for its own selected batch, with existing data-error quarantine versus OSError/budget-abort classification. No batch or original validation is skipped.

The source generator cancels the sole pending prefetch in finally; its executor joins any read already running. The immutable-object consumer explicitly closes the generator on validation/object/deadline errors, so a suspended generator cannot leave its source executor alive. Object tasks remain bounded at16 and are also cancelled/joined before return. These are cooperative deadline checks around filesystem work; no claim of interrupting an in-progress kernel read/fsync at exactly45seconds is made.

Pair multiplicity accounting still occurs after complete-batch processing, including marking every selected pair on batch failure. All target entries, complete source references, final ledger/master/index validation and deadline checks remain before pointer publication. Files are individually fsynced and warm/cold object directories/new ancestors are synced before commit. Precommit failures preserve old pointer and success-state bytes. The previously documented post-index-replace directory-fsync uncertain-commit boundary is unchanged.

## Independent local verification

**60 tests pass, zero skips/failures/errors:**

- Three original reviewer-owned malformed-manifest/raw-checksum/normalized-checksum one-byte-budget fixtures now raise the source-budget error and retain the old pointer, without editing those tests. Their original three-failure result remains preserved separately in `export-prefetch-failed-reads-review-20261028.xml` and `export-prefetch-failed-read-finding-20261028.md`.
- Twenty-two reviewer-owned object/directory/generator/validator/encoder/deadline faults and seeded full-index/every-shard-byte comparisons against original458 sequential export pass with the prefetch present. These include late failures/conflicts/repeated chunks and correctly patch the actual private validator/encoder.
- Twenty repository observer/source/prefetch tests pass. They cover one-ahead overlap with all replay on the main thread, physical error classification, exact budget boundary, malformed/checksum/decode failures including unused futures, grow/short/OSError/missing/before-budget reservation exits, concurrent next-read versus manifest-recheck reservation contention, joined actual accounting, pointer retention and no remaining worker threads.
- Fifteen generic store tests pass, covering exact raw preservation, immutable/idempotent batches, corruption/forged content, schema/count/semantic checks, path bounds and ordinary-load versus exporter predecode-bound behavior.

Reproduction from `E:/QTrade-git`, with `LIVE_TRADING_ENABLED=false`, `PYTHONPATH=src;.`:

```text
python -B -m pytest E:/QTrade-temp/test_export_prefetch_failed_reads_review_20261028.py E:/QTrade-temp/test_export_encoded_reuse_independent_review_20261028.py E:/QTrade-temp/test_export_pair_pipeline_review_20261028.py -q -p no:cacheprovider -o addopts=
python -B -m pytest tests/test_unified_market.py -q -p no:cacheprovider -o addopts= -k "prefetch or failed_source_reads or observed_read_reservation or object_failure_explicitly or source_bytes or physical or parquet_bound or raw_normalized or source_manifest"
python -B -m pytest tests/test_unified_store.py -q -p no:cacheprovider -o addopts=
```

The45-second export,512,000,000-byte source,128,000,000-byte estimated decoded cache, metadata/output limits and ordinary Git90-second budget are not increased. These test results do not establish the final production-equivalent storage timing, full current-source oracle, a deployable commit or actual publisher acceptance. The older full current oracle remains scoped to its explicitly bound earlier candidate/output until a fresh final-source run is completed.

Evidence under `E:/QTrade-temp`:

| Evidence | SHA256 |
| --- | --- |
| `export-prefetch-independent-fixed-tests-20261028.xml` | `e4808aa1e24fa667f78be591789989732f731aff15c401a040c64642695489a0` |
| `export-prefetch-source-observer-tests-20261028.xml` | `d50e205a9ec8074cef25dd5fee9cd7803f436de42242d04e8b4dd8124532be21` |
| `export-prefetch-generic-store-tests-20261028.xml` | `1f9707c5dd3866f2a1994c7ce007846edc4fdce2f9799717acb50cb3e29252ca` |

The two final assertion tests are separately retained in `export-prefetch-final-assertions-tests-20261028.xml`; earlier failure artifacts were not overwritten.

## Final-source full original458 oracle on NFS: PASS

The parent subsequently scheduled a fresh full oracle against this exact five-file source/test set and the original4,200-batch freeze. The source-manifest binding is candidate manifest SHA256 `fd8d11be151b3adac9455f73fd72f6cff9bc4ddc3c1be07842bb280f7d279b7a`, remote candidate `/root/qtrade-local/operator-evidence/market-current-nfs-prefetch-candidate-20261028T054748Z`. Its explicit readonly output root is `/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade/operator-checks/market-current-nfs-prefetch-candidate-20261028T054748Z`; root/index device66 was required and rechecked before/after. The independent oracle never called/imported an exporter or candidate validator, never used a decoded batch cache, and never wrote an export/pointer. It imported original458 parser, material/store, quality and market contracts from the separately SHA-bound baseline manifest (`4b6b6ae1f8b2db717c1ffb075e7e2e073eb084a0a2b05f3bdc9c8729439d15be`), whose436 files had previously been verified against exact original458 Git blobs.

The frozen ledger is `8f1dc0b9b7807b9339c4bbdc4d98c8c0a6bdf9bad9bfa476152cd7acfe7c03bb`, catalog raw hash `9024a7e22cb9cddf25031afc81e71d99b3b7c23bfb8bcfd6964ea4b91cbf354b`, freeze evidence `75cb08efcdc1eeec775a579f4811ccce3ddccb2e2ab1e80f79781d8908da69d3`, controlled clock `2026-10-28T04:23:39.263755+00:00`. All4,200 batches/104,218 chunks and104,501 raw captures were independently validated/replayed. All182,458 normalized/retained bars, every source reference and complete lineage matched. All3,000 entries matched in full:2,995 AVAILABLE,5 NO_DATA,zero quarantines. All5,986 unique shard files match exact bytes, total135,052,142bytes. Actual source reads180,712,399bytes, zero source failures; all original metadata/source/output bounds were retained.

Index raw SHA256 is `df224f261d6f46234114744ecbfada5b82f0c58f1ed7711b5d8fcd5733dad834`; index evidence SHA256 is `40e9051944f815ca0edefc52366654bdb8937fc1489f5fed98e8933b3f39bf5c`. Oracle time58.899137seconds and peak RSS716,044KiB are independent-validator costs, not export timing or a revised export/cache budget. The new oracle copy adds only explicit output-root/device binding to the prior separately preserved script; no replay/validation boundary was relaxed. New oracle source `E:/QTrade-temp/independent_current_market_nfs_oracle_20261028.py` SHA256 `11f9be8ad56cba365df3af14c07d9c18e007fb8086d484cd6ae26e9e6245a1ca`; runner SHA256 `79a11178ac86bf2929f852c60c935ffa141335767eed64d20087358f98d7b53f`.

Local full report: `E:/QTrade-temp/cluster-evidence/market-current-nfs-independent-oracle-20261028T055202Z/oracle-report.json`, SHA256 `3694a81fc28fe8f0fe9946088c6e9f670550407446caa5b9ca5ffacd023633e8`. Exit0, no production actions; NFS slot was released to the parent/performance owner immediately after completion. The owner reports the bound NFS cold run44.466431export/44.860369wallseconds and warm33.691126/33.992167seconds. This supplies the final-source old-freeze equivalence gate, not stable timing margin, a newer afternoon freeze, a deployable commit or publisher production acceptance. Deployment remains unapproved at this checkpoint.
