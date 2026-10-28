# Fixed R2 SDK adapter: author validation

The original independent findings and original d878f6c2 source remain intact.
The parent delegated the narrow implementation fix to this reviewer after the
findings, so the fixed-source tests below are author validation. They are not a
second independent review of my own patch.

Final source: r2_object_store_fixed.py, mirrored from
E:/QTrade-git/src/qtrade/research/r2_object_store.py.
SHA256 d26685bf8f95db48ef3db8d0c8d4716aaac56d68963fd2c302fa9b5c62c16202.

Final repository tests: test_r2_object_store_fixed.py, mirrored from
E:/QTrade-git/tests/unit/test_r2_object_store.py.
SHA256 16c2034f0782258d1058906c11adaa8d2175e39458650a573f2903c3f71bb1d0.

## Final behavior

The parent first isolated config/credential files to os.devnull and removed
ambient profile selection. It added DEBUG checks at construction, before each
SDK operation, and before wire dispatch, including configured child loggers.
This patch additionally disables the session data_path provider and installs a
loader that searches SDK-bundled model data only. boto3 may append its own
bundled model directory; AWS_DATA_PATH and ~/.aws/models are excluded.

Client construction runs with the pinned SDK's context-local
PluginContext(plugins="DISABLED"). It restores the caller's prior context in a
finally block, including failure paths. No process environment or global plugin
function is modified. Environment-selected or outer-context client plugins do
not initialize. The SDK private hook is version-sensitive: actual boto3 and
botocore here are 1.43.120. Missing hook support fails closed. Upgrades require
review and service tests; a matching explicit botocore dependency pin was
recommended to the parent because boto3's dependency range alone allows patch
upgrades.

Fixed-account HTTPS origin, TLS verification, no inherited proxy, path style,
one wire attempt, conditional writes, opaque strong ETag validation, full SHA256
read verification, body byte bounds and sanitized errors remain unchanged.

## Results and reproduction

fixed-author-combined.xml: 86 passed / 0 skipped on Windows:

- 41 repository provider tests against actual optional SDK 1.43.120.
- 39 repository object-publication core tests.
- 6 inverse replays of the original independent counterexamples against the
  exact frozen fixed source; the corresponding original 6 vulnerability tests
  remain preserved with their original passing XML.

The provider additions exercise poison environment/default model files,
configuration-discovery denial, environment and inherited-context plugins,
context restoration on success/failure, child logger DEBUG at construction,
late DEBUG before signing/send, and a separate wire-dispatch DEBUG check.
Every network connect is denied; signed SDK responses are synthetic in-memory
responses. No real account, credential file, plugin, cloud request or bucket is
used. Ruff and strict mypy for the provider pass.

```powershell
$env:PYTHONPATH='E:/QTrade-temp/r2-sdk-20261029;E:/QTrade-git/src;E:/QTrade-git'
# Run from E:/QTrade-git, choose a new XML output path.
python -B -P -m pytest -q -p no:cacheprovider tests/test_object_publication.py tests/unit/test_r2_object_store.py E:/QTrade-temp/r2-sdk-independent-review-20261029T004200Z/test_r2_sdk_fixed_counterexamples.py --junitxml=<new-path.xml>
python -B -m ruff check src/qtrade/research/r2_object_store.py tests/unit/test_r2_object_store.py
python -B -m mypy src/qtrade/research/r2_object_store.py --follow-imports=silent
```

The first 40-provider-case XML was before adding a context-restoration failure
test; it remains a prior successful author run, not the final41-case count.

## Acceptance limits

This seal does not establish Linux behavior, actual MinIO/R2 acceptance, cloud
authorization, durable cloud semantics or a 45/90s publication SLA. The parent
owns a separate loopback MinIO service and SDK/harness. MinIO requires an
explicit nonproduction adapter or injected test client; the production R2
account origin is not loosened.

The DEBUG check is a steady-state safety check. It is not an OS lock against
malicious code concurrently changing loggers or trusted SDK functions in the
same process. Application create-once is not WORM. Exact 404 after an uncertain
write does not prove noncommit. The frozen core currently lacks explicit
immutable-upload resumption after unknown PUT plus 404; the separate
object-publication-upload-resumption-limit-20261029.md explains that known
limitation and a future safe conditional-resumption contract. Latest CAS and
all prior production manual intents retain their existing no-blind-retry rules.

No object-publication core, production entry, owner, active/paper state, cold
operator, cloud account or live-trading control was changed by this patch.
