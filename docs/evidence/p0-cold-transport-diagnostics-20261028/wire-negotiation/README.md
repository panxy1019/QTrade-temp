# push.negotiate: actual local-wire negative experiment

Six new independent local-bare cases compared `push.negotiate=false/true` with
(1) no sender auxiliary metadata, (2) sender has validated auxiliary commit/tree
metadata only, and (3) metadata plus an explicit sender auxiliary ref (diagnostic
only, beyond the earlier production metadata-only proposal).

All six ordinary `pack.useSparse=false push HEAD:main` operations sent 9 Git
objects / 152543 pack bytes and retransmitted all five previously staged public
blobs. Initial main stayed unchanged during auxiliary staging. The final complete
main was verified and each receiver passed strict fsck. The source refs were
unchanged during each final push. Synthetic random JSON only; no production Git
paths, hostnames, credentials, SSH or public network were used. Native Git version
is recorded in report.json; protocol allowlist is file only.

The exact wire stream was captured by an isolated stdin tee around mature native
Git receive-pack, without implementing Git packet or authentication protocols.
The sender's PACK header declares 9 objects. The captured pack bytes/SHA256 equal
the receiver's newly stored .pack byte-for-byte in every case; therefore this is
not a verify-pack overcount caused by thin-pack base materialization. Per-object
verify-pack records and wire object IDs are in report.json. All true cases have
actual upload-pack v2 negotiation in packet traces. Their `have` set contains the
new main commit and baseline; the auxiliary commit is not a common main ancestor.
This is a negative result for these exact fixture/options, not a universal claim
about all Git negotiation policies or transports. Do not deploy the option as a
fix based on this experiment.

Earlier independent failures and original aux prototypes remain untouched. First
author probe failed on Windows reserved name AUX.git and is preserved under
push-negotiate-local-probe-20261028T135032Z. The first wire-wrapper attempt failed
on Windows shell backslash quoting; its source and failure are preserved under
push-negotiate-wire-probe-v2-20261028T135503Z. A completed earlier six-case receiver
pack observation is preserved under push-negotiate-local-probe-20261028T135058Z;
this wire-v2 result supplies the missing exact-wire check.

Reproduction creates another unique new fixture directory, never reruns an old
namespace or writes production objects:

```powershell
python -B -P E:/QTrade-temp/publication_push_negotiate_wire_probe_v2_20261028.py
```

There was no production install or configuration change. This experiment does
not consume or reset any real manual PUSH_INTENT/UNCERTAIN, and does not authorize
another assisted main push. The separate bundle/cold-cache proposal remains at
design or isolated diagnostic stage.
