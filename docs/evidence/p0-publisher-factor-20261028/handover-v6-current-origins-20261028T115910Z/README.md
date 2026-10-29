# v6 current-origin guard: isolated regression evidence

The operator now checks the exact tracked release file set, rejects aliases and additional files, and resolves all seven protected module origins with PathFinder in a new fixed-venv Python -B subprocess. The child uses the fixed release cwd and PYTHONPATH and never imports publisher modules. Origin paths and source hashes must match the complete bound Git inventory.

The check runs during release preflight, after the candidate temporary file is fully fsynced and before wrapper replacement, and again before the sole named TERM. Five isolated filesystem cases cover clean origins without execution/cache, an existing untracked shadow, a shadow after the inventory scan, a shadow inserted during the candidate fsync, and a shadow inserted after the first TERM precheck. The fixtures use synthetic wrapper bytes and mock RPC; no real wrapper was read or replaced and no production control was issued.

The combined original 47 artifact/parser tests plus 5 new cases passed locally: 52 passed, 0 skipped. The first local run had 51 pass / 1 failure because the Windows fixture lacked Linux O_NOFOLLOW. That failed XML is retained. The test-only compatibility shim uses O_NOFOLLOW=0 on that synthetic fixture; the production source keeps Linux O_NOFOLLOW. The second run passed all 52.

Reproduction (PowerShell, from the QTrade repository):

```powershell
python -B -m pytest -q -p no:cacheprovider E:/QTrade-temp/test_publisher_actual_evidence_v6_20261028.py E:/QTrade-temp/test_publisher_actual_composite_v6_20261028.py E:/QTrade-temp/test_publisher_v6_current_origins_20261028.py --junitxml=<new-unique-result.xml>
```

The older v6 source and original 47-test Linux/actual --check results remain separate historical evidence. The actual evidence parser/composite, 62 roles, performance source fba51aade2ee2410dda087e95cf054e53375ed3a and measured results are unchanged. The bridge only changes its pinned operator SHA. This archive contains safe sources and test metadata; no raw mount data, private wrapper body, private configuration body or authentication material is included.

Production budget remains 45 seconds. Cold 120-second runs remain nonproduction diagnostics. The first production cold cycle may fail while retaining validated receipts. These guards do not provide distributed CAS or freeze the filesystem against a concurrent external writer. New Linux and independent review results must be recorded separately; this local archive does not claim them.