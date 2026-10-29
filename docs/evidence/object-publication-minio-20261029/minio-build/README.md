# Pinned MinIO protocol-fixture source build

Result: **BUILT_VERIFIED_NOT_STARTED**. Binary lives at:
`/root/qtrade-local/operator-evidence/minio-protocol-20261029T163700Z/build-mirror-20261028T165956Z-f5217074/minio`

SHA256 `ea71aa0735f6930c3ae3fcc27f0dc2d670f941043085d9a501cad323a393db2d`, 151037391 bytes. No MinIO server was started by this builder. Parent owns any later create-only/fsync bridge to the harness's ROOT/minio and ROOT/build-result.json, and server/service tests. This is independent fixture tooling, with no production configuration, deployment, cloud account or object-store claim.

The original build used a clean Go environment with no network proxy. Read-only inspection found all eight proxy-presence flags false in the SSH environment too. Cluster official proxy.golang.org probes timed out twice; its first build had zero module ZIPs. Local official proxy and sum.golang.org probes succeeded. Cluster goproxy.cn served the exact fixed module .mod, checksum-database supported endpoint and signed-latest bytes; goproxy.io returned HTTP errors. These observations show a usable transport route, not proof that the original timeout had one exclusive cause.

The second attempt is isolated under a new build-mirror root and has an independent 600-second compile budget, TERM2/KILL2 owned-group cleanup, four Go workers and private 0600 logs. It uses one explicit `GOPROXY=https://goproxy.cn` with no direct fallback, `GOSUMDB=sum.golang.org`, `GOTOOLCHAIN=local`, `GOTELEMETRY=off`, `CGO_ENABLED=0` and `GOFLAGS=-mod=readonly`. No checksum-disable setting or dependency-version change was introduced. [Go's module authentication](https://go.dev/ref/mod#authenticating) checks downloaded modules against go.sum and uses its checksum database when needed; the mirror is a transport, with Go checksum verification retained. The probe's latest response was not separately signature-verified by this probe, and this report does not claim it was used during the build when existing go.sum entries sufficed.

Pinned inputs were reused read-only from the original owned fixture root:

| Artifact | SHA256 |
| --- | --- |
| Official Go 1.24.13 linux-amd64 archive | 1fc94b57134d51669c72173ad5d49fd62afb0f1db9bf3f798fd98ee423f8d730 |
| Official MinIO source archive, commit 07c3a429bfed433e49018cb0f78a52145d4bedeb | 8819e3e7817e46b7b3798f8f200ead208562e571563c2e040352378031abe9f2 |
| go.mod | e362dbf17ddb50992fe199e0d777b3da147ec398e6db9e590fef2a573060acb1 |
| go.sum | e81f196b31076aa8928bcf74456d1a9c20113aca174089bc5c7d0642de3b316f |

The full 1365-file extracted MinIO source inventory is identical before and after. The original archives, build-failure.json, build.log, go.mod and go.sum bytes are also identical before and after. The old 600-second failure remains preserved: failure SHA2120476410ee149645cd46beb555fe274405aba5e3683d9e3fc615ad5858a7dc; original log SHA7744b3510f7f83feb3466bce49cb57f2fbb142f303dc1e8575c68ebfa11e0ee5. This successful second attempt does not erase or reclassify that failure.

Observed: build **94.982s**; `go mod verify` **7.722s**, `all modules verified`; overall **109.523s**; 342 cached module ZIPs; peak child RSS1431092KiB. Resource gates required >=2GiB local and >=100GiB NFS before execution; after execution local14695690240 and NFS281086525440 bytes remained. All five owned process groups are empty in post-cleanup.json.

The binary reports DEVELOPMENT.GOGET and Go1.24.13. Its source identity is established by the pinned archive, exact source-file inventory and build evidence; it is not presented as an upstream precompiled/signed MinIO release. `go version -m` and raw build/module-verify/version logs remain private in the remote owned directory; this package records their SHA/size without copying arbitrary raw logs into Git. The parent may inspect them privately. This is a successful build and module-integrity check, not S3 protocol, durable storage, deployment or Cloudflare/MinIO production acceptance.

Reproduction requires a new owned build-mirror directory, the same original verified archives and the existing authorized cluster connection capability. `run_minio_mirror_build_20261029.py` creates such a directory, verifies upload bytes and invokes the included builder. The builder never reads the connect helper or credentials and never starts the service. No system proxy/configuration or original build files are modified. The builder and invocation are preserved here; re-running downloads/compiles into a fresh directory and does not overwrite this evidence.
