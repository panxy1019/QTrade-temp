# MinIO full-load Git reader independent review

Reviewed without network, SSH, MinIO startup, or production actions. Repository/shared status was not modified by this review. The scope is the full_load.git_read replacement and its fixed-commit/physical-reader/cleanup wiring.

Final harness SHA256: `f3495e4f74d2407618d447491e9c2d062921c7f25ebb4dc7246e4cda8bd31126`.
Original reviewed harness SHA256: `97a93ec0b0f2bdde9e89e4538ffe5ec1d3857a6d586292ad2613c035cd09c453`.
Reused physical reader SHA256: `738c295d55d9b1a4081ca2547d2bfdad3ea59553d0102cc152bb34b3916917fa`.

No additional blocker was found in the final narrow scope. The original reader left a read-only boundary implicit: native Git can consult the source repository's local promisor configuration and fetch missing objects. The final source adds `GIT_NO_LAZY_FETCH=1` and `-c gc.auto=0`. The explicit environment discards inherited Git overrides, disables system/global config and replacement refs, fixes the native Git executable and repository, and disables hooks. Only built-in show/ls-tree are allowed. This is an argv/environment review, not a test of the cluster Git build's implementation of that environment variable.

All three metadata reads use the literal commit `abe2986cd6e3f3faeb4984454498e4c67f1646d3:path`; the NUL tree listing uses that same commit. HEAD changes cannot redirect these calls. The helper's tree parser requires complete NUL records, regular blob mode, strict OIDs/content paths, no duplicate paths, and all current references. The anchored O_DIRECTORY/NOFOLLOW walk holds directory descriptors; physical files are regular, size bounded and opened NOFOLLOW/NONBLOCK. Each read checks Git SHA1(blob-header+raw) against the frozen tree OID and SHA256 against the content-addressed name. The candidate core prepare_market validates metadata lineage and every shard reference occurrence. Later directory identity changes fail the context exit. The helper module is imported only to call these three reader functions; no prepare/push/control entry point is invoked by full_load.

Git communicate has a 30-second timeout. On timeout the owned child receives TERM, then after two seconds KILL and another two-second wait; its stdout is closed and failure propagates. Git remains in the harness process group. The inspected parent runner starts that harness in a new owned session and invokes its existing owned group cleanup in finally. The MinIO child shares that group; its own finally also waits TERM2/KILL2 and the report requires an observed stopped child before PASS. This is bounded cleanup code inspection and mock evidence, not an assertion that uninterruptible kernel I/O can always be killed within that time.

`git-read-final-f3495-offline-v2.xml`: 11 passed, zero failures/skips. Tests extract only git_read from the hash-pinned snapshot, replace Popen with a fake, and never launch Git or a server. Cases cover exact command/environment, push/fetch/config/checkout rejection, normal pipe closure, TERM and KILL timeout paths, nonzero exit, excessive output rejection, the fixed commit, and NO_LAZY_FETCH. The first XML records exact-source pin rejection because the parent changed 97a93 to f3495 while tests were being prepared. The next XML retains a Windows-only expected-path fixture failure; final v2 corrects the fixture to use pathlib for the host platform. Neither earlier failure is represented as a harness defect.

Limits: the 16 MB output limit is checked after communicate buffers all output, so it is not a streaming memory bound for arbitrary hostile repositories. This is acceptable only for the existing reviewed fixed tree in this fixture scope. The new reader bypasses the old helper's runtime-origin guard solely for native Git reads; parent candidate-source hash verification remains required. The fixed source commit is caller-provided evidence provenance, not proof of freshness or fresh producer replay. Full load, all 5,987 physical files, 3,000 entries, two real MinIO generations, actual cluster native Git behavior, and actual elapsed time remain the parent's integration evidence. This review does not establish cloud/R2 acceptance, public delivery, production budgets, server ACL safety, or production readiness.

Reproduce offline:

```powershell
python -B -P -m pytest -q -p no:cacheprovider E:/QTrade-temp/minio-full-load-git-independent-review-20261029/test_git_read_independent.py --junitxml=E:/QTrade-temp/minio-full-load-git-independent-review-20261029/reproduced.xml
```
