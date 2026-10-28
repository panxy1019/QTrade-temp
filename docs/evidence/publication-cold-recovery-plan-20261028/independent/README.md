# Independent read-only review: one-shot cold recovery candidate

Final exact operator: `738c295d55d9b1a4081ca2547d2bfdad3ea59553d0102cc152bb34b3916917fa`.
Author test source: `c00aeac29cf2697b68b68046f56a877d28c205f6e4023868c20510fa30e8742f`.
Decision: **no remaining blocker found within the reviewed scope, subject to a
fresh explicit human approval of the exact actually prepared plan**. No execution,
production preparation, send mode, real Popen, Git process, SSH, network, push or
owner control was performed by this independent review. Root owns Linux and
actual prepare; this review does not authorize them by itself.

The original 7ec53cc5 candidate had one reproduced blocker. Its generic git()
used a Popen context manager, so a cleanup exception re-entered Python's real
Popen.__exit__ and called wait(timeout=None). The final operator changes only
that function: explicit Popen, frozen owned group cleanup and finally-close of
both pipes, with no context-manager wait. Independent tests use the actual Python
Popen.__exit__ on a fake child: the old candidate calls the unbounded wait and
the final candidate does not. The previous package and first result are retained.
The production publisher and frozen relay-v3 source were not modified.

Independent offline results: **7 passed, 0 skipped**. The subprocess boundary is
always a fake: no actual process is launched. Tests also cover ordinary leaf
rejection of push and >90 timeout, explicit timeout/pipe close, duplicate-key
exact-hash documents and safe progress projection. This Windows result is not
Linux process, filesystem, transport or performance acceptance.
The copied root Linux XML, parsed separately, records **64 passed, 0 skipped**
against the exact final operator/author source. It is evidence of the tested Linux
contracts; it is not evidence of a 180-second cold network delivery.

Reviewed behavior:

- send-cold requires SHA-bound target.json and USER_AUTHORIZATION.json. The latter
  has an exact schema, the NEW exact-plan authorization kind, exact plan hash,
  180-second one-shot scope, unchanged ordinary budgets, no force and no retry.
  Clocks require preparation <= authorization <= current time and authorization
  age <=1h. Generic A/B/C is not this new 180-second approval. The operator does
  not manufacture an authorization file or interpret the old approval as new.
- The authorization document is a trusted external attestation. Its creator must
  use create-only file/fsync after an actual human response and preserve that
  response reference. File contents, a checksum or the kind string alone cannot
  cryptographically establish that a human spoke; this remains the caller's
  authorization boundary. No such approval was created by this reviewer.
- Old INTENT/UNCERTAIN documents are bounded regular files bound by exact hashes
  and target/plan semantics. They are only read. Fresh PREFLIGHT/INTENT/STARTED
  files are create-only, so an old or failed cold attempt cannot be silently
  retried. The old namespace is not reset or removed.
- Preparation validates the real signed export-attempt document with existing
  contract code, <=45 elapsed, schema/authority/checksum and causal clocks. It
  checks 3000 entries and exactly5987 distinct current shard references, a full
  bounded frozen tree listing, actual worktree bytes, Git blob SHA1 and SHA256,
  exact total bytes and every occurrence via existing verify_shard_bytes, which
  checks content/quality/index/lineage bindings. Data nlink>=1 is allowed;
  metadata/config/approval inputs require one regular link. Prepare's current
  reference audit is not a fresh audit of every historical Git object.
- The direct SSH command uses the pinned existing command hash and metadata of
  one private identity, without reading or hashing the key. It pins explicit
  options, no config/proxy/multiplexing, strict known-host checking. Public
  known_hosts path/body hashes are captured during prepare and recomputed before
  the durable send claim. These are checks at specific times, not an OS lock or
  immutable snapshot of a trust file; it must remain controlled through SSH use.
- Native Git receives one explicit target:refs/heads/main, no force/lease/mirror
  and no follow-tags. A divergent server change fails native nonforce semantics.
  ls-remote and send are separate transactions: a safe fast-forward change in
  that gap may still be accepted. The plan explicitly says the preflight is not
  a cross-transaction lock. Existing root local-native tests cover that boundary.
- The cold child has its own monotonic180 limit and existing TERM2/KILL-wait2
  cleanup. The final generic read-only helper retains90 and no hidden context
  wait after cleanup failure. This is not an end-to-end184-second wall-clock SLA:
  preflight ordinary Git calls and filesystem operations have separate bounds,
  and blocking kernel/file operations or process death are not certified hard
  cancellation. Ordinary production export45/Git90 are unchanged.
- Stderr is read in bounded chunks and projected only into finite enumerated
  phase names and bounded integers, with bounded event storage. Unknown text is
  represented only by byte counts/hash; no raw stderr or SSH configuration is
  saved. Writing100% is explicitly not server ACK. Even native exit0 returns
  reconciliation-required and ordinary_publisher_success=false. A separate fresh
  remote check, public/index/API checks and later autonomous90 observations remain
  necessary before calling delivery or the publisher recovered.

The exact source pins/import-origin checks and prepared plan binding are the
reviewed trust inputs. This is not a new full2194-file runtime audit, adversarial
root-user filesystem race certification, or proof of historical source secrecy.
No budget, permission, activation, LIVEfalse, model or book boundary was changed.
