# Independent offline review: original R2 SDK adapter

Original source SHA256:
d878f6c2cee389f936acb737ff51dd2ff23e55d7fbf5a0866135692476da944d.
Source is retained as r2_object_store_original.py. No provider source was edited
by this reviewer. Actual installed boto3 and botocore are both 1.43.120 from
E:/QTrade-temp/r2-sdk-20261029.

## Confirmed blockers before actual service acceptance

1. SDK debug logging is checked only at construction, and only for four parent
   logger names. Enabling botocore DEBUG after construction permits a signed PUT
   and logs its Authorization header. Enabling only botocore.endpoint DEBUG also
   bypasses the parent check, both before and after construction. The synthetic
   HTTP transport is reached once in each counterexample. Capture retains only
   a boolean that a synthetic signed header was logged; no raw header/signature
   is saved to this evidence package.
2. boto3.session.Session() uses default discovery even when explicit credentials
   are supplied. In the isolated fixture it parses synthetic AWS_CONFIG_FILE,
   consults an AWS_DATA_PATH model directory, and executes the initializer of an
   environment-selected synthetic BOTOCORE_EXPERIMENTAL__PLUGINS module. This
   contradicts the adapter's no-discovery contract. No real configuration file
   or plugin was used. The credentials file, profile, legacy BOTO_CONFIG and
   default customer-model directory are also scoped to synthetic test paths.

Narrow fixes: use a botocore session with config/credential file providers fixed
to os.devnull, no environment/default profile, and a model loader restricted to
the SDK's built-in data. Reject or explicitly disable environment-selected
experimental plugins. Recheck effective DEBUG levels for the SDK namespaces and
their configured descendant loggers before SDK operation and at wire dispatch.
This is a steady-state guard, not an OS-level guarantee against malicious code
concurrently changing global logger state or replacing trusted SDK functions.

## Reproduction and exact scope

From PowerShell, with no account or network:

```powershell
$env:PYTHONPATH='E:/QTrade-temp/r2-sdk-20261029;E:/QTrade-git/src;E:/QTrade-git'
python -B -P -m pytest -q -p no:cacheprovider E:/QTrade-temp/r2-sdk-independent-review-20261029T004200Z/test_r2_sdk_independent_original.py --junitxml=<new-path.xml>
```

original-counterexamples-six.xml records 6 passed / 0 skipped. These passing
tests prove the original vulnerabilities, not adapter acceptance. The earlier
four-case XML is retained. Socket connect/create_connection are denied. Only
synthetic credentials are supplied; SDK HTTP send returns an in-memory response.
Config parsing is allowed only on synthetic paths or os.devnull. SDK package
model files are the only non-fixture model data needed for these experiments.

Static review found the intended fixed account origin, path-style bucket URL,
TLS verification, no proxy inheritance, total_max_attempts=1, a second-send
guard, key allowlist, required conditional PUT, full-body SHA256 verification,
bounded response bytes, strong opaque ETags, and sanitized SDK errors. These
are useful protections, but the blockers above must be fixed before claiming
the factory's security contract. This report is Windows offline SDK evidence;
it is not Linux, MinIO, R2, cloud authorization or performance acceptance.
