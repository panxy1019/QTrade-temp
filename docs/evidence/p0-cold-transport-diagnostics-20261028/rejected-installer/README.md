# Rejected nondelta pack-cache optimization: local installation proof

Decision: **do not prepare or perform a production installation of this candidate**.
The fixture installer has no production entry point. Its installation boundary is
synthetic fixture.json plus source exactly <fixture>/source. No cluster write,
SSH, public network, production object installation, owner control or real push
was performed in these author tests. Fixture Git subprocesses allow file protocol
only. The old real manual PUSH_INTENT/UNCERTAIN remains untouched and unconsumed.

The copied actual Linux comparison report binds frozen target
`dde33b30a54d46beb0197a80ddbcb35afb5830da` and base
`5a0925f47e541726210a8879e2f2bed4ae2a62fd`:

| Existing object store | Native wire-pack bytes | Generation seconds | Objects |
| --- | ---: | ---: | ---: |
| Original SOURCE, read only | 19,444,657 | 2.698772 | 3350 |
| Isolated cache with nondelta candidate | 42,749,932 | 2.102264 | 3350 |

The same target/base/argv produced about 2.20 times as many wire bytes after the
candidate cache was added. Saving roughly 0.60 seconds of generation does not
justify assuming delivery improves. Filesystem caches were not purged; this is
not an independent cold-cache SLA certification. The report explicitly states
network_used=false, production_cache_installed=false and git_push_accepted=false.
It does not establish why the previous real push timed out or whether a server
received the complete pack. Production export45/Git90 are unchanged. A separate
120-second staging diagnostic is not a production publishing result.

Author local tests: **17 passed, 0 skipped**. Actual native Git creates each new
source/remote/stage repository, existing source pack and MIDX, public loose
objects and candidate/index. Tests cover exact pack/index SHA256 and copy
readback; non-delta object-set equality; candidate-only scratch Git object reads
with each raw Git SHA1 and SHA256; every new commit tree's synthetic public-path
and JSON scope, including a deleted historical disallowed path with a clean tip;
create-only pair installation; duplicate completed hit without rewrites; intent
rebind rejection; bad pack/index/certificate/object-set rejection; existing
conflicting index rejection; partial copied index; pack-only/index-linked fault
states; directory fsync failure after pack and after index; and a real native
reader plus local push while only the pack is visible. All preexisting source
files, loose objects, refs, index, config and MIDX retain their original bytes.
The final fixture remote contains the complete target tree and passes fsck.

Durability sequence is intent fsync -> verified exclusive temporary copies ->
create-only final .pack -> directory fsync -> create-only final .idx -> directory
fsync -> copy/readback confirmation and temporary-link cleanup -> result fsync.
A durable unfinished claim refuses an install retry. Its key fixes source and
pack identity, so replacing the old manual-intent binding cannot reset it. An
incomplete result remains uncertain; it never grants another manual push.

The Windows run explicitly instruments directory fsync because its supported
os.open API cannot open a directory for fsync. POSIX runs delegate to native
fsync, but **no independent Linux durability result exists for this installer**.
Fault hooks model filesystem states; they are not actual power-cut experiments.
The fixture JSON/path scope is not the production historical public-object
allowlist. This module is not certified against hostile same-user directory
races, production NFS behavior or production subprocess/group lifecycle. These
limitations and the adverse wire-size result prohibit using these local passes
as a production approval. Do not extend or deploy this rejected optimization by
substituting actual production paths into the fixture boundary.

First author run had 15 cases / 1 fixture failure / 0 skipped: Windows Git made
the candidate index read-only, so the corruption setup failed before it reached
the installer. Its XML and source are preserved. The fix changes only that
owned synthetic file's permission before corruption; the final run adds the two
focused boundaries above and passes all 17. No failure output was overwritten.

Reproduce in another new temporary fixture directory:

```powershell
python -B -P -m pytest -q -p no:cacheprovider E:/QTrade-temp/test_publication_pack_cache_fixture_installer_20261028.py --basetemp=<new-unique-fixture-root> --junitxml=<new-unique-result.xml>
```

Module SHA256: 8a71ba8e329b74fbbb6f0212d71f861e4717e0b56c90cc6079154a3d2b36e9aa
Test SHA256: 3d10a4e9668f1ffed125ff85640ebd55118a3fbdd570bdac61613106e1adedee
Final XML SHA256: 34cb2f41281c27b6f293e39b5402f51dff6187dd6c2ebe292137b291f7644c06
Actual Linux report SHA256: 7f5dc55b52a94d1fc3d215a89f85452f31ca5e4693ec9ff8411a042a6e2c61c0
