# Encoded shard reuse and sixteen-object pipeline review

Reviewed locally on 2026-10-28. **No source code blocker found in the four exact files below. This is not deployment approval.** Final current-freeze cold and warm exports on production-equivalent storage, final committed-source binding, and complete independent original458 output/lineage comparison remain required. No production control, source lake access or pointer mutation was performed by this review.

## Exact candidate

| File | SHA256 |
| --- | --- |
| `scripts/publish_unified_market.py` | `ff820202480aa94e72fbf0dc2d6c38cf1586bd1877e914d0be31269aa1017ad1` |
| `src/qtrade/research/unified_market.py` | `64982440d5c876a58c282fb7c8ab4923d03376864c489e8224565a9aff760bb6` |
| `src/qtrade/market_data/sina_incremental.py` | `c0f783604750ac2648c8dcb2496e2e02994ddb3cf469d6a69e762ebfb9bff63b` |
| `tests/test_unified_market.py` | `8f55bac8a2eb4fd48db0d2142c77cacc4c8890cc2cf75f90869b7af69b534f07` |

The reviewer checked these hashes before and after the checks. The performance owner confirmed this window is frozen. The narrow change from the preceding encoded-reuse window is eight to sixteen bounded object tasks and corresponding cap tests, plus line-ending normalization. The unsuccessful heap scheduler was reverted; this candidate uses cohort ordering and the already reviewed complete-batch-before-counter-decrement pair sealing.

## Contract review

`_document_bytes` retains the original field-set short circuit before serialization, exact pretty JSON serialization and size bound, exact authority type/value checks, and canonical signature comparison in the original order. Public `document` still returns None. `_validated_shard_bytes` performs the entire shard identity, scope, source-binding, per-bar quality, independent quality-gate, Decimal context/flag, ordering and duplicate checks before returning its private tuple. Public `validate_shard` still returns the supplied validated dict and retains the prior outer/inner error wrapping.

The bytes returned by the private helper are the same serialization already used for the size check. The exporter uses those exact bytes for the digest, byte count, immutable writer and references. It does not bypass the validator or reserialize a second time. Capturing the original evidence digest before encoding and rechecking both that digest and the complete canonical signed value after validation rejects content mutation, including mutation that re-signs the object. This is a content guard; it does not claim detection of insertion-order-only changes or general concurrent-mutation safety. The exporter owns its constructed ordinary dictionaries throughout the synchronous validation call.

The sixteen-task change preserves the existing bounded submission loop, stop-submission/cancel/join behavior and all full-source checks. There is no additional executor queue of unbounded pending objects. Per-file fsync remains mandatory. All object-directory/new-ancestor syncs occur before the final ledger/master/index/deadline gates and pointer write, including a warm retry with every object already present. The same 45-second export, 512,000,000-byte physical source, 128,000,000-byte estimated decoded cache, and existing output/count limits remain. The cache bound is not a total-process RSS bound. A filesystem syscall remains cooperatively timed; already-running tasks are joined before an error returns.

All precommit generator, validation, serialization, output-budget, deadline, writer and object/ancestor-directory sync failures leave the old pointer and success-state bytes unchanged. The pre-existing index `replace` followed by index-parent fsync remains an uncertain-commit boundary: failure after replacement cannot promise the old pointer. This limitation was not introduced or hidden by this candidate.

Raw size/JSON/count/hash/source/calendar/time/OHLC/amount/vwap/schema/lineage checks remain present. The parser hash is unchanged from the separately reviewed bounded captured-reader implementation. All selected chunks, repeated chunk multiplicities, complete batch failures, quarantines, full target entries and final ledger checks remain in the pair pipeline.

## Independent evidence

**14,708 original458 differential cases pass**, zero failures: 2,000 standalone bar cases, 688 public raw parser cases, 720 private captured-reader cases, 3,240 calendar-boundary cases, 4,016 public shard cases, 4,000 private encoded-byte cases and 44 malformed-document/error-order cases. The four Decimal contexts include hostile caller settings and pre-set flags. Every accepted private-helper encoding equals original458 `encoded(shard)` exactly. Original raw-parser arithmetic/row statements and public quote/reconciliation components are AST-checked; the private reader memo limits remain 4,096. This is a constructed-input differential, not the complete production input oracle.

**38 local tests pass**, zero skips/failures: 22 reviewer-owned failure and original458 full-index/every-shard-byte fixture tests; 13 repository tests covering the new helper, mutation rejection, actual serialization count, correctly rebound validation failure, late whole-batch failures, full per-bar validation and directory durability; and three sixteen-task peak/submission/cancellation tests. The independent failure injections patch the actual new exporter `_validated_shard_bytes` and the actual `contract.encoded` call, so obsolete function names cannot make those tests vacuous. The peak test reaches exactly sixteen active writes, and error/deadline tests leave no market-object thread alive.

Reproduction from `E:/QTrade-git`, with `LIVE_TRADING_ENABLED=false` and `PYTHONPATH=src;.`:

```text
python -B E:/QTrade-temp/export-encoded-reuse-review-differential-20261028.py
python -B -m pytest E:/QTrade-temp/test_export_encoded_reuse_independent_review_20261028.py E:/QTrade-temp/test_export_pair_pipeline_review_20261028.py -q -p no:cacheprovider -o addopts=
python -B -m pytest tests/test_unified_market.py -q -p no:cacheprovider -o addopts= -k "validated_shard or export_validation_fault or shards_are_encoded_once or preencoded or entire_batch or directory_sync or post_write or every_published"
python -B -m pytest tests/test_unified_market.py -q -p no:cacheprovider -o addopts= -k "immutable_object_io_is_bounded or object_io_stops_submission"
```

| Evidence under `E:/QTrade-temp` | SHA256 |
| --- | --- |
| `export-encoded-reuse-review-differential-20261028.py` | `113ec341a7089c8db4bea4b84c20239871eaedc149bcc8b31dc8df0fd338e0d8` |
| `export-encoded-reuse-review-differential-20261028.json` | `7ef6a481d2abe9c2f39d330082b175de71941591f615e0dfdc4703b8dd57f20f` |
| `test_export_encoded_reuse_independent_review_20261028.py` | `a69114d8b542eb667f9ad1bae788fd999c7f45e7dca20db1bcaee688763bb048` |
| `test_export_pair_pipeline_review_20261028.py` | `b3eac3dd8f6cf5751c26e1930c286fdd7e83cf9d0929a106f7dfda3a0743116d` |
| `export-encoded-reuse-independent-tests-20261028.xml` | `0e7131e606647b8e4f9db1a91b464c2e3c38478bf90c6ef436cb72030cb02a92` |
| `export-encoded-reuse-repository-tests-20261028.xml` | `0ac1e37578008cf8c4085df01d77371167f139193b563ae6d27de67f1765aaa8` |
| `export-encoded-reuse-16io-tests-20261028.xml` | `e5f9afcd5d119014c0516dda2f5cd48301e8760b1a1e8c22cb4c2c42b4229bf4` |

The completed full current oracle remains bound to the earlier `53e66dee...` candidate and its exact 4,200-batch freeze/output, as recorded in `export-pair-pipeline-review-20261028.md`. It is not silently relabelled as a full-input test of this changed source. The performance owner reported the preceding eight-task encoded-reuse cold export failed at45.2507seconds. The current sixteen-task cold/warm result is pending at this review checkpoint. Passing local tests cannot substitute for those missing gates.
