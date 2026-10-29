# Frozen core PoC: immutable upload resumption limitation

Applies to object_publication.py SHA256
d362529ba56f2283a0308c10103abe55b27e8feff9bdcf75513b42cc2f21d8dc.
The original core seal remains unchanged.

The first implementation records an immutable-object upload intent before PUT.
If PUT has an unknown outcome and a later GET returns 404, it preserves the
intent and raises OutcomeUnknown indefinitely. Restart does not retry that PUT.
This is conservative but can permanently stop a generation whose PUT never
reached the service. It is a documented recovery limitation, not complete
network-recovery support.

An immutable object retry can be safe under a separate explicit resumption
contract: the key is the SHA256 of the exact body, the body is re-read and
validated from current physical source, the existing remote object is fully
validated if present, every retry still uses If-None-Match: *, and an uncertain
response remains uncertain. Two racing requests can only establish the same
bytes; a 412 must be followed by GET and full content validation. Each retry must
record its own attempt without discarding the original uncertain evidence.
This is a future design, not implemented by the frozen core.

The mutable latest CAS has different fencing and reconciliation requirements.
The immutable-upload resumption permission does not authorize blind retries of
latest CAS, production Git delivery, an order, or any previously consumed manual
operator intent. No production budget, owner, entry point or live-trading state
is changed by this note.

MinIO and R2 acceptance should report supported cases separately: initial
upload, validated existing-object reuse, conditional rejection, uncertain CAS
resolved by GET, and the currently unsupported unknown-upload/404 resumption.
Local SQLite or SDK mocks do not certify any actual cloud behavior or 45/90s
performance budget.
