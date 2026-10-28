# Relay v3 frozen-shard preparation: author seal

Frozen operator SHA256: `76bf3e2653ab51491b966db9f62c415c0d7161b4d642e7f89cefc30b687a61fc`.
The v2 source remains `91bfd970f1def55b01a4935907578206a7d91ece39ae857f3b451a99037c84c1`.
No v2 source or previous result was overwritten. No network, upload, init, push,
production repository write, owner control, wrapper read, or credential access
was performed by these author checks.

V2 actual preparation exhausted 90 seconds in `git cat-file --batch-check` for
5987 references. That failure is preserved at
`E:/QTrade-temp/cluster-evidence/publication-relay-prepare-20261028T131959Z/result.stderr`.
It produced no target or PUSH_INTENT. This repair does not assert that delivery
or the ordinary publisher has recovered.

V3 performs one complete bounded `ls-tree -r -z` of the frozen commit's public
object directory. The parser requires exact 100644/blob/40-hex OID/64-hex JSON
paths, rejects duplicates and incomplete records, and requires all current
references. Valid older immutable paths may be present in the full listing;
they are counted, not silently truncated. Only current referenced bodies are
read and quality-validated. Limits are 16,000,000 listing bytes, 100,000 paths,
and 1,000,000 bytes per current shard.

The object directory is opened from filesystem root component by component
with O_DIRECTORY|O_NOFOLLOW. All ancestor descriptors remain held; directory
identity is rechecked after validation. Every shard is opened by basename
relative to that descriptor using O_NOFOLLOW|O_NONBLOCK, then requires a regular
file and exact size. Data hardlinks are allowed (actual source has nlink=2).
SHA1(blob-header + bytes) must match the frozen Git OID and SHA256 must match
the reference. The existing unmodified verify_shard_bytes validator runs for
every occurrence, including repeated digests in different entries. Config and
metadata one-link restrictions inherited from v2 remain in force.

Prepare has one 90-second audit deadline beginning before repository guards;
remaining time is passed to each Git command and checked around reads and each
semantic verification. There is no segment-by-segment fresh 90-second allowance.
Blocking filesystem operations and final durable save are not hard-preempted;
this is an elapsed budget check, not a claim that a stalled NFS syscall can be
interrupted at exactly 90 seconds. Production export45 and Git90 are unchanged.
The native push leaf, network live repository/URL checks, one-shot fsynced intent,
SSH pinning and owned process-group cleanup are inherited without behavior change.

Author verification: **39 passed / 0 skipped** with real synthetic body bytes,
double hashing and unchanged qtrade index/shard/signed-document validators.
Windows dir_fd and POSIX flag tests explicitly translate the syscall boundary;
they are not Linux acceptance evidence. The separately copied root independent
Linux XML records **25 passed / 0 skipped**, including actual POSIX file/directory
operations and real nlink=2. The old v2 71-case XML is not counted as a v3 run.

Reproduce author checks on this workstation:

```powershell
python -B -P -m pytest -q -p no:cacheprovider E:/QTrade-temp/test_publication_native_relay_v3_author_20261028.py --junitxml=<new-unique-result.xml>
```

The author test's synthetic market fixture helper is copied in this archive
with its SHA. Its qtrade implementation dependency remains the local read-only
E:/QTrade-git/src and calendar JSON used by the existing independent fixture.
Actual full-source preparation timing, relay routing, push result and subsequent
autonomous publisher acceptance are separate root-owned evidence. A successful
v3 synthetic test does not authorize a public write.
