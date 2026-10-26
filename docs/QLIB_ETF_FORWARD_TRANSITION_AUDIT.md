# Read-only ETF SHADOW next-session acceptance

`scripts/audit_etf_forward_transition.py` audits one explicitly pinned ETF target
and its exact intended next trading session. It complements the stock-only
`audit_qlib_forward_transition.py`; it does not certify the entire STEP1, historical
PIT, ETF execution rules, historical simulation quality or browser rendering.

The CLI has no `--now`. It uses actual system wall time and the checked-in verified
calendar. The requested day must be an exchange session whose daily completion
cutoff has actually been reached: `Calendar.expected(now, 'daily') >= day`, which
uses 16:30 Shanghai time. Passing 15:00 alone is insufficient. A future day,
intraday run or date outside the verified calendar cannot pass. No future session
acceptance is inferred from synthetic fixture clocks.

## Evidence and checks

Provide the existing ETF immutable archive root, the separate ETF service local
root, the target identity and intended execution date. The auditor reads:

```text
<local-root>/targets/<target_id>/target.json
<local-root>/targets/<target_id>/admission.json
<local-root>/targets/<target_id>/observations/<observation_id>.json
<local-root>/contracts/<contract_id>/sessions/<day>/intent.json
<local-root>/contracts/<contract_id>/sessions/<day>/target-binding.json
<local-root>/contracts/<contract_id>/sessions/<day>/observation-source.json
<local-root>/contracts/<contract_id>/sessions/<day>/completed.json
<archives>/<target-source-id>/{diagnostic.json,receipt.json,capture-store/objects/...}
<archives>/<observed-source-id>/{diagnostic.json,receipt.json,capture-store/objects/...}
```

The intent and completion receipts pin the sources and observation; the latest
pointer and directory order cannot substitute another day or release. The local
intent must precede the original target freeze. `verify_etf_target` checks the
target's immutable source replay, allocation, identities and separate admission
receipt: `frozen_at <= durable_at < exact next-session open`. A missing or late
admission never becomes valid because an audit or restart ran later.

`verify_etf_shadow_observation` then replays the target source and exact next-day
source from their captured raw bytes. The observation must already exist and
match the completion identity. Missing selected ETF closes block acceptance.
A verified target with no positive scores may have no selected ETFs and an empty
observed-close list; that is an unallocated research target, not a cash account
or zero return.

The supplied public document must be the signed `etf-forward.json` service
publication or an unmodified capture of the actual GET
`/api/research/online/etf-forward` response. For the API envelope,
`publication_available` and `service_fresh` must both be true. Its `current_target`
boolean refers to a currently prospective target, which may be for a later day;
it is not used to certify the pinned completed transition.

The inner publication is checked by `validate_etf_shadow_publication`. Both its
generated time and heartbeat must be no more than 720 seconds old and not future;
the service must be RUNNING with no errors. Its entry for the exact target must
equal the privately replayed target and observation in full. Other pending or
historical entries cannot satisfy that binding. A valid STOPPED/DEGRADED document,
an old heartbeat, a missing observation or an invented close remains BLOCKED.

Core/public validators enforce SHADOW, blocked PAPER, empty orders/fills/holdings
and null cash/NAV/portfolio return. This adapter reuses those checks. It does not
call a service cycle, target freeze, observation writer, account constructor,
database connection, gateway or network source. It hashes local evidence, both
source archives' diagnostic/receipt/raw objects and the exact supplied public
file. It rechecks input hashes and freshness after raw replay; a changing input
or elapsed freshness budget blocks acceptance.

## Run after the actual next session completes

Use the reviewed repository revision and real existing paths. The audit output
must be a new file outside the archive tree, local service tree and supplied
publication's parent directory. Keep the actual captured response if public API
delivery is being tested; do not replace it with a handcrafted envelope.

```sh
LIVE_TRADING_ENABLED=false PYTHONPATH="$PWD/src" \
python -m scripts.audit_etf_forward_transition \
  --archives /path/to/base/online-research/etf-trend \
  --local-root /root/qtrade-local/etf-shadow-service \
  --published-forward /path/to/public-capture/etf-forward-api.json \
  --target-id <original-64-character-target-id> \
  --day <exact-intended-execution-day> \
  --revision <reviewed-40-character-git-revision> \
  --output /path/to/separate-audits/etf-transition-<day>-<unique-run>.json
```

`--calendar` can explicitly select the reviewed calendar file; its document hash
must match the target/source contract. The default is
`configs/observer_calendar_2026.json`. Extending its horizon requires separately
reviewed source evidence.

A successful audit exits 0. A completed audit that is BLOCKED exits 2 and still
writes its signed immutable diagnostic report. Invalid invocation/configuration
or unusable output paths exit 2 without claiming acceptance. An existing output
file is never overwritten; use a new filename for a later attempt. Only the
specified independent report and its temporary write in that same output
directory are created. No source receipt, service file or public publication is
created, repaired, replaced or backfilled.

The report schema is `qtrade.etf-forward-transition-audit.v1`, with fixed
`whole_step1_accepted=false`, `browser_verified=false`, blocked PAPER and null
execution-account values. `etf_shadow_transition_accepted=true` means only that
the pinned SHADOW transition passed the specified evidence checks at the recorded
system clock. SHA256 binds content; it is not an externally authenticated
timestamp, an operator signature or a PIT certificate. An input file alone does
not prove how it was transported or that a browser rendered it.

## Focused reproduction

```powershell
$env:PYTHONPATH='E:/QTrade-git/src'
$env:MYPYPATH='E:/QTrade-git/src'
python -m pytest tests/test_etf_forward_transition_audit.py -q --basetemp=E:/etfa1
python -m ruff check scripts/audit_etf_forward_transition.py tests/test_etf_forward_transition_audit.py
python -m mypy --explicit-package-bases --platform linux scripts/audit_etf_forward_transition.py
```

Tests use actual immutable raw packs and the existing ETF service/core helpers
with explicitly controlled dates. The callable exposes `now` only for such tests
and labels those reports `CONTROLLED_TEST_CLOCK`; the CLI never exposes it.
These fixtures establish rejection and replay behavior, not production forward
acceptance. Real acceptance must wait for the actual intended session and its
verified subsequent raw receipt.
