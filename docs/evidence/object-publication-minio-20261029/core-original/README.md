# Nonproduction object-publication protocol core

Status: local protocol PoC only. New source is
`E:/QTrade-git/src/qtrade/research/object_publication.py`, SHA256
`d362529ba56f2283a0308c10103abe55b27e8feff9bdcf75513b42cc2f21d8dc`.
Test SHA256: `ca66f89ac761e1efb5ed379b87c3d5c57eb7b009c5a2d4bc584da333f52a2032`.
There is no production entry point, owner change, cold-operator edit, credential
loading, cloud client, cloud-account connection, bucket creation or purchase.
Only this new module and its new tests were edited in the repository; concurrent
state/provider/factor changes belong to the parent/other work and were untouched.

## Stable adapter interface

```python
@dataclass(frozen=True)
class StoredObject:
    raw: bytes
    etag: str

class ObjectStore(Protocol):
    scope_id: str  # 64hex hash of actual endpoint/bucket/fixed namespace configuration
    def get(self, key: str) -> StoredObject | None: ...
    def put(self, key: str, raw: bytes, *,
            if_none_match: str | None = None,
            if_match: str | None = None) -> StoredObject: ...
```

Only exact NoSuchKey/404 maps to None. Conditional412 maps to PreconditionFailed;
transport/ACK uncertainty and conservative409 map to OutcomeUnknown. Disable SDK
write retries and redirects/alternate destinations. The GET body must be bounded
and closed; strong single ETag tokens are accepted, but wildcard/weak/list/control
header forms are rejected. ETag is opaque and never substitutes for SHA256.
PUT may return submitted raw bytes; core still does a full GET/check. The actual
adapter config fingerprint must distinguish R2, local MinIO, accounts/buckets and
prefixes. Do not reuse a cloud scope_id for a loopback service.

Key whitelist: market/shards, index, catalog, quality, manifests each have exact
64hex.json content-addressed leaves; readonly/summary has the same leaf format.
The only mutable key is market/latest.json. There are no arbitrary URLs, paths,
queries, bucket selectors, delete, multipart, listing or account-management APIs.

## Protocol and evidence

prepare_market(reader, parent=read_parent(store), source_git_commit=<40hex>, now=...)
returns (PreparedPublication, object_bytes). The reader receives only fixed local
source names and exact shard names. Current bytes are read/hash/validated again
by publish; no mtime, historical PASS or ETag authorizes skipping them. Index,
catalog and quality use the existing validators. Every shard occurrence uses
verify_shard_bytes, including a repeated digest attached to the wrong entry.
Catalog target/universe/master lineage is bound to index, and quality universe
is checked. Future read-only summary is disabled unless an explicit validator
and separate64hex contract identity are supplied; it is not enabled as a new
production route or implicitly approved schema.

This is a transport layer, not a replacement for the existing producer's vendor
raw replay, complete-source physical SHA, admission or fresh successful export
contract. Production integration must retain those upstream gates. Public shard
validation does not independently replay private source lake objects. No PIT,
PAPER/live execution or freshness authority is added; original source timestamps
and limitations survive unchanged.

Publication ID = SHA256(canonical immutable manifest bytes), always64hex. The
40hex source Git commit is a distinct lineage field, never an interchangeable
publication identity. The manifest binds required catalog/index/quality and all
shard descriptors plus parent64ID and monotonic generation. All immutable objects
are create-only If-None-Match:*; after complete byte verification, latest is
created the same way for generation1, otherwise updated only with the captured
parent ETag/If-Match. A stale parent or competing writer cannot overwrite a winner.
An application's always-incrementing generation avoids legitimate head rewind;
this is conditional publication fencing, not an owner lease or cross-object CAS.

Immutable filesystem journal evidence uses existing atomic_json file/link/fsync
and shared strict canonical JSON. New ancestor entries are synced; existing
records are synced on recovery before reuse. Upload/CAS intents are durable
before writes. Concurrent claim creation grants one caller; a found record does
not authorize repeating its write. Receipts bind store scope/key/current byte
SHA/size and verified opaque ETag. Corrupt/unknown/oversized evidence fails closed
and remains unmodified. Definitive CAS rejection is retained, never PASS.

A timed-out PUT is unresolved even if the next read returns404 or the old head.
Read-only reconciliation may establish the exact proposed complete head, an
observed old head (still UNCERTAIN), or conflict/supersession. It never rolls a
head back or automatically retries an unknown conditional write. Exact-head
reconciliation revalidates full remote bodies, public schemas and all occurrence
lineage. Missing/corrupt objects cannot be called a complete delivery.

The actual local SQLite adapter uses FULL/WAL and BEGIN IMMEDIATE, with unrelated
random opaque ETags. Separate connections demonstrate actual competing writes,
and a new Python process reopens the real DB/journal and reconciles without
rewriting latest. SQLite is for a local filesystem only, not NFS/S3/R2 emulation
certification.

## Validation and limits

Windows: **39 passed / 0 skipped**, including actual SQLite transactions, separate
writer threads, immutable persistence, subprocess restart, actual physical file
corruption at unchanged size/mtime, lost ACK, delayed commit after a negative
read, stale parent, current remote corruption under unchanged ETag, receipt
poisoning, scope/URL rejection and explicit future-summary extension. Ruff and
strict mypy for the module pass. Two earlier failed fixture runs remain: initial
source import path was missing; the first subprocess fixture omitted bundled
pyarrow search paths. Final test explicitly supplies runtime dependency paths,
without inheriting cloud/private environment configuration.

Windows directory-fsync calls are explicitly instrumented because os.open cannot
open a directory there; file writes/fsync/link and SQLite are real. No independent
Linux/NFS durability, actual MinIO or R2 result exists in this core seal. Parent
is preparing a dedicated local MinIO service/harness, with real loopback S3 SDK
acceptance separate from these tests. MinIO success will not certify R2 behavior.
Faults are deterministic injected outcomes, not real power loss or cloud outage.

Performance is deliberately unclaimed. Every round rereads physical source
bytes, GETs full remote bodies when checking receipts, verifies them again before
CAS, and after CAS validates the complete remote generation. This first strict
PoC can approach three full GETs per object plus other calls: ~18000 shard GETs
and >500MB downloaded for the known5987/167MB payload, plus journal fsyncs. It is
not a45s/90s production publisher candidate. A future reduction needs an explicit
immutable-store/retention/permissions contract and service experiments; do not
turn ETag/mtime/old receipts into integrity authority.

R2 documents atomic conditional writes and explicitly warns that a negative read
cannot disprove an unknown in-flight commit:
https://developers.cloudflare.com/r2/reference/consistency/
https://developers.cloudflare.com/r2/api/s3/extensions/
AWS documents If-None-Match/If-Match and conditional conflicts:
https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html
These are protocol-design references, not this user's cloud acceptance. Application
create-once is not WORM/ObjectLock; privileged deletion/overwrite and lifecycle
rules are outside this core. A multi-object generation requires trusted writers
and an established no-delete/no-unconditional-overwrite operating contract;
provider token capabilities/retention limitations must be disclosed, not assumed.

Reproduce from E:/QTrade-git with source root/src on PYTHONPATH:

```powershell
$env:PYTHONPATH='E:/QTrade-git/src;E:/QTrade-git'
python -B -P -m pytest -q -p no:cacheprovider tests/test_object_publication.py --junitxml=<new-result.xml>
python -B -m ruff check src/qtrade/research/object_publication.py tests/test_object_publication.py
python -B -m mypy src/qtrade/research/object_publication.py --follow-imports=silent
```

Local-only usage:

```python
store = SQLiteObjectStore(new_local_directory / 'objects.sqlite')
parent = read_parent(store)
prepared, _ = prepare_market(current_physical_reader, parent=parent,
                            source_git_commit=reviewed_git40, now=now)
publisher = Publisher(store, new_local_directory / 'upload-receipts')
result = publisher.publish(prepared, now=now)
# OutcomeUnknown => preserve evidence; publisher.reconcile(prepared) only reads.
store.close()
```
