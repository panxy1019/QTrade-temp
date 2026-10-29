# Failed physical source reads are not included in the exporter budget

Readonly local reproduction on the moving one-ahead prefetch candidate. Exporter SHA256 at the reproduced implementation: `fb1fe907e88a3f437819fd50163f9d9722d5b3319ec119b4fc4a0364894023c3`. No production objects, services or pointers were touched; only isolated pytest fixtures were corrupted.

**P1 finding:** `_SourceReads.material` adds `material.source_bytes_read` only after `_batch_material` returns. `_BatchMaterial.__init__` initializes that field only after parsing/checking the manifest and reading/hash-checking both blobs. A malformed manifest or checksum failure can therefore throw after physical reads, before any bytes are added. `collect_batch` quarantines those data failures and continues; a completed export can claim zero source bytes despite actual lake reads and a source budget already exceeded. The constructor-accounting omission predates prefetch, but it blocks the required true-physical-read512MB contract of the current candidate.

All three independent cases with a deliberately one-byte test source budget incorrectly published a quarantined index instead of raising `UNIFIED_MARKET_SOURCE_BYTE_BUDGET` and preserving the old pointer:

| Failure | Physical bytes read | Reported bytes | Result |
| --- | ---: | ---: | --- |
| Invalid manifest JSON | 401 | 0 | Exported quarantine |
| Raw blob checksum mismatch | 2,359 | 0 | Exported quarantine |
| Normalized blob checksum mismatch | 8,338 | 0 | Exported quarantine |

The actual parser/hash checks rejected the corrupted data; this is an accounting and failure-budget defect, not admission of corrupt bars. Fix the accounting at a store-owned physical-read boundary, preserving all original bounds/hash/error checks and counting failed constructor reads as well as successful reads, including joined/discarded prefetch results. Ensure a budget overflow cannot be reduced to a quarantine-only success.

Reproduce from `E:/QTrade-git`, `LIVE_TRADING_ENABLED=false`, `PYTHONPATH=src;.`:

```text
python -B -m pytest E:/QTrade-temp/test_export_prefetch_failed_reads_review_20261028.py -q -s -p no:cacheprovider -o addopts=
```

The pre-fix result is three failures. Test source SHA256 `0863f669f86653170b770f4b66cc18fb28cafa34b1df3e07489dc193be1d4c34`; failure JUnit `E:/QTrade-temp/export-prefetch-failed-reads-review-20261028.xml`, SHA256 `31f12e59076a7a2dc2ec0c07235e02c5febb5f1a023ba4d9044004ca4439f2f2`. Preserve this failure artifact; write a separate fixed-candidate result after the change.
