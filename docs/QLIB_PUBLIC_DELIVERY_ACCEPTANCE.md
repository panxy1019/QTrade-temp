# C-chain production delivery acceptance

`audit_qlib_session_transition` already verifies private version, automatic
completion, approved-model prediction and signal archives before assessing a
real subsequent session. Its optional production check adds only the public
delivery of those exact identities. It never certifies the whole STEP1, browser
rendering, candidate validation, ETF observation or PAPER accounting.

The existing command keeps its private-only behavior without the two new flags.
To include production delivery, append both:

```bash
PYTHONPATH="$PWD/src:$PWD" LIVE_TRADING_ENABLED=false \
python -m scripts.audit_qlib_session_transition \
  --previous-version "$PREVIOUS_VERSION" --current-version "$CURRENT_VERSION" \
  --automatic-completion "$AUTOMATIC_COMPLETION" \
  --prediction "$PREDICTION_ARCHIVE" --signals "$SIGNAL_ARCHIVE" \
  --database "$DATA_DATABASE" --output "$NEW_ACCEPTANCE_REPORT" \
  --verify-production --expected-production-revision "$REVIEWED_PRODUCTION_MAIN_SHA"
```

The expected revision is the reviewed full 40-character production main commit,
not the cluster worker revision. The public worker revision must independently
match the prediction's frozen code revision. Use a new immutable output path for
each attempt; failed checks raise without creating a report. Existing immutable
reports are preserved, so their recorded check times remain significant.

The helper makes five fixed-origin GET requests to
`https://q-trade-temp.vercel.app`: deployment, catalog, data-versions, signals,
then catalog again. It sends no authentication, cookies or payload, follows no
redirects, uses no proxy credentials, and accepts no origin/path override.
It follows urllib's existing system/environment HTTPS proxy discovery, with
urllib's normal proxy-bypass rules. Only a configured HTTP(S) proxy with a hostname,
no userinfo, no query/fragment and no path beyond `/` is accepted. Invalid discovery
or configuration fails before the request; it never silently falls back to direct.
Other proxy schemes and HTTP/ALL proxy entries are not promoted to the HTTPS route.
Without an HTTPS proxy, requests use direct connections. TLS verification remains
the default HTTPS verification, including when using an accepted proxy.

Evidence records only `DIRECT` or `CONFIGURED_CREDENTIAL_FREE_PROXY` configuration
mode, never the proxy URL, host, port or credentials. This is configuration metadata,
not proof of the actual socket route: urllib may bypass a configured proxy according
to existing platform/NO_PROXY settings. The tool does not change those settings.

It has no retries, a 50-second HTTP budget, at most 10 seconds per socket
operation, and body limits of 16 KB / 3 MB / 1.1 MB / 3 MB respectively. Research
responses must be HTTP 200 JSON with `Cache-Control: no-store`; the deployment
endpoint may retain its normal short cache policy.

Both catalogs must independently validate, have current worker/data-service
heartbeats within 720 seconds, and name the same version, admission event,
approved model, current prediction and worker source revision. Heartbeat-only
changes are allowed. The version head/event, manifest hashes, ACTIVE model,
prediction proof hash, bindings, contract and counts must match the private
archives. The signal manifest must match the independently replayed private
signal manifest in full. API-computed catalog fields are removed only for
validating the original sealed catalog, then checked separately.

Acceptance uses real wall time. The audited day must still be the latest complete
exchange session and the next intended execution open must remain in the future
at the end of the requests. A stale snapshot, head/epoch drift, deployment mismatch,
older prediction, future clock, unavailable publication or failed fetch cannot
produce production acceptance. Operators can rerun a new attempt after ordinary
publication propagation; the tool does not poll or repair state.

The nested delivery receipt preserves the exact received UTF-8 bodies, byte
counts, SHA256 hashes, selected response headers, fixed URLs and local request /
receipt clocks. These are operator observations over HTTPS, not independent
timestamp signatures. Public prediction fields are a bounded manifest projection;
the check does not claim the full private prediction Parquet was published.

Reproduce focused behavior without network or production writes:

```bash
PYTHONPATH="$PWD/src:$PWD" python -m pytest \
  tests/test_qlib_public_delivery.py tests/test_session_transition_cli.py \
  tests/test_session_acceptance.py
python -m ruff check scripts/qlib_public_delivery.py \
  scripts/audit_qlib_session_transition.py tests/test_qlib_public_delivery.py \
  tests/test_session_transition_cli.py
MYPYPATH=src python -m mypy --explicit-package-bases --follow-imports=silent \
  --platform linux scripts/qlib_public_delivery.py scripts/audit_qlib_session_transition.py
```

Tests use controlled clocks and HTTP fixtures. They establish failure behavior,
not that the next real market session or production delivery has occurred.
`LIVE_TRADING_ENABLED=false`; no service, model, queue, version or account is changed.
