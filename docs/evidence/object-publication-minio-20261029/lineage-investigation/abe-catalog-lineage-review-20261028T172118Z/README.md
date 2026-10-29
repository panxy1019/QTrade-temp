# Frozen abe catalog/index lineage investigation

**Confirmed mismatch; no exact matching original catalog found within the authorized bounded scopes.** No source/data repair, checksum re-signing, public send, Git fetch, production configuration change or service start was performed. All writes are diagnostic evidence in new operator/temp directories.

Fixed publication Git commit: `abe2986cd6e3f3faeb4984454498e4c67f1646d3`.

| Original artifact | Git blob | Raw file SHA256 |
| --- | --- | --- |
| unified-market.json | 779f47292fa92fca6f15a2e46db9f29afcd1c09a | 164a721fd517567004fa10c4be9bc2cfbc478feb9354e5cd66948644510daf46 |
| unified.json | a317af8c977924c31e0f993543c3b864976a820f | c4a40629a78a7bb19945b9443a6753e9e8870da6bcdb7151612c49442e6a4b67 |

Index master_sha256 is `38f5d70670c6f2b1397bd3637e75794b9743849ee44a1ab3797a7e7f2ec9bd2e`; same-commit catalog master.evidence_sha256 is `c4eddb82a4c8db41198cfdd6c18ed8a547a9027560f88d8c1d088ef691f2018a`. Index, catalog and embedded master self-checksums each recompute correctly. This is a real association mismatch, with independently valid documents.

Index published_at is `2026-10-28T14:56:16.629025+00:00`. Catalog built_at is `2026-10-28T14:56:20.042776+00:00`. Master observed_at is `2026-10-26T08:59:06.887581+00:00`; that observation timestamp is not the catalog construction time and does not establish which snapshot the index consumed. The differing bytes alone do not prove the precise cause of the publisher's snapshot association failure.

Both documents declare universe `482c81a9b0981a677e9023197fa865b3c52510bcb249c115dc6c5affa30da308`. Index has 3000 frequency entries for 1500 distinct instruments, 5987 referenced files and167397950 bytes. Its instrument set exactly equals the catalog's 1500 in-target securities (1470 STOCK,30 ETF), among5594 master securities. Identical universe/target sets do not establish an identical complete master snapshot and cannot satisfy or replace the rejected lineage contract.

The first metadata probe stopped after0.075s because a whole-tree name listing exceeded its1MB bound (7282831bytes). That failed attempt remains separately preserved; no blobs were read by it. The corrected queries selected only the four known root/data catalog/index paths, avoiding further whole-tree enumeration.

Two bounded history scopes are clearly distinguished:

-20 unified.json change commits ending at fixed abe: built14:06:47.571988Z through14:56:20.042776Z;8.516s;0 matching masters.
-20 unified.json change commits ending at independently observed local HEAD `2b37ebba62cb926ad38eeaff4746cf3e784763f3`: built16:25:49.015589Z through17:16:11.639701Z;8.628s;0 matching masters. Every master self-checksum in both windows recomputes correctly.

The HEAD moved beyond the frozen target in the background; no publisher control or rollback was attempted. These are two explicitly pinned windows, not an unbounded scan, and the interval between them and other refs/history were not searched. Neither window supplies the required original master38f5 document.

`BASE/unified-data/public` was only listed at its top level: it contains one regular file unified.json and no historical subdirectories. No recursive archive search or current-file substitution was performed. Other existing operator archives or staging snapshots may exist outside the authorized search and remain unexamined; absence in this report is not proof that no matching original exists anywhere.

Every Git command was read-only show/ls-tree/log under GIT_NO_LAZY_FETCH=1,GIT_CONFIG_GLOBAL=/dev/null,GIT_CONFIG_NOSYSTEM=1,GIT_OPTIONAL_LOCKS=0,hooks/dev/null,gc.auto0,maintenance.autofalse, noninteractive credentials, with explicit owned process groups and the frozen fba TERM2/KILL2 helper. Git operations used a60s aggregate deadline; individual Git calls were at most10s. Both completed diagnostic processes took under9s. No private configuration, connection-helper body or credentials were inspected. Raw current index/catalog blobs are copied locally; all history refs and raw hashes are recorded, and their original raw bytes are retained in the remote diagnostic blobs directories.

The parent's OBJECT_PUBLICATION_INDEX_CATALOG_LINEAGE_MISMATCH rejection must remain. There is no approved dual-source frozen bundle from these findings. If an exact original matching catalog is later located in a separately authorized source, freeze its original commit/blob/raw SHA alongside abe's original index/refs as an explicitly different multi-source input and revalidate the complete contract. Do not relabel such a bundle as the original single-abe package or reconstruct/re-sign a master to make it match.

Commands, per-command status/stdout hash/size, original public blob identities, clocks, universe summary, history bounds and scope-specific evidence paths are in summary.json and the referenced result/commands JSON. manifest.json binds the retained local evidence. No performance or protocol result is promoted by this investigation.
