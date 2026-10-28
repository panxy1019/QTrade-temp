# Object-publication reconcile binding fix

Final object_publication.py SHA256:
d11c801a90142d4b7142b8f67c422c341b30b84201999dd297c186c3582fa7d0.
Final test_object_publication.py SHA256:
f55e4a4bec2f01c193a82904be67ea7468bc615218ac68597f2c336e49a36806.

The old d362 source and original39-case author seal remain unchanged at
E:/QTrade-temp/object-publication-core-poc-20261028T163755Z/. The independent
reviewer retained the old-source 8-pass/1-fail actual counterexample and its
frozen source at E:/QTrade-temp/object-core-independent-review-20261029/.

The old public reconcile entry accepted a manually assembled PreparedPublication
whose manifest was valid for another source Git commit, but whose publication_id
was replaced with an already committed ID. Its derived head bytes were the same
and remote assets were valid, so it could report COMMITTED_RECONCILED without
binding that ID to that manifest. The publish entry already rejected this case.

The fix validates prepared manifest SHA256 against publication_id before any
remote read. It also binds source Git commit, created_at, parent raw identity and
generation, and optional summary contract to the prepared manifest. The committed
branch separately reads the actual stored manifest and requires exact byte
equality before validating every remote asset and shard. It performs no remote
write during reconciliation. Existing public APIs remain unchanged.

author-final-92.xml records Windows author validation: 45 core tests, 41 actual
SDK provider tests and 6 inverse replays of the original provider counterexamples,
all passed, zero skipped. Six new core cases cover the demonstrated forged ID,
four prepared-context mismatches before remote access, and a manifest changed
between read_parent and the committed-manifest read. Ruff check/format and strict
mypy for both core and provider pass.

Independent reviewer step1 subsequently froze exact d11 and reported 15 passed /
0 skipped in fixed-core-independent-v2.xml under its independent directory.
The original failing XML is retained there. This report does not copy or alter
that reviewer's evidence. Original and fixed source results remain separate.

```powershell
$env:PYTHONPATH='E:/QTrade-temp/r2-sdk-20261029;E:/QTrade-git/src;E:/QTrade-git'
# Run from E:/QTrade-git; use a fresh XML destination.
python -B -P -m pytest -q -p no:cacheprovider tests/test_object_publication.py tests/unit/test_r2_object_store.py E:/QTrade-temp/r2-sdk-independent-review-20261029T004200Z/test_r2_sdk_fixed_counterexamples.py --junitxml=<new-result.xml>
python -B -m ruff check src/qtrade/research/object_publication.py tests/test_object_publication.py src/qtrade/research/r2_object_store.py tests/unit/test_r2_object_store.py
python -B -m mypy src/qtrade/research/object_publication.py src/qtrade/research/r2_object_store.py --follow-imports=silent
```

Windows directory fsync remains explicitly instrumented by the existing fixture;
this is not Linux durability, MinIO, R2, cloud authorization, production integration
or 45/90s performance acceptance. The strict first PoC still does repeated full
remote GETs, and the fix adds an explicit committed-manifest GET. The known
unknown immutable PUT followed by404 resumption limitation is unchanged; see
E:/QTrade-temp/object-publication-upload-resumption-limit-20261029.md. No retry
protocol, remote-verification optimization, production owner, active/paper state,
entry point, existing cold operator or live-trading control was changed.
