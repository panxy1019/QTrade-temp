# Qlib research workflow and unified web integration

Goal opened 2026-10-26 at the user's request.

## Acceptance contract

1. Vercel research login submits a bounded, immutable task; anonymous users
   cannot create/cancel jobs. No broker/control token is reused.
2. The cluster pulls a dedicated private queue outbound, executes one allowlisted
   Qlib job at a time, and records status, cancellation and restart uncertainty.
3. Linear and LightGBM configurations use explicit dataset, features, splits,
   costs, universe and TopkDropout parameters. No arbitrary modules or shell
   commands are accepted from the browser.
4. Strategy center lists research configurations. Research creates tasks.
   Experiment center tracks unified runs. Backtest selects/compares completed
   runs. Legacy ETF/minute tools move to an explicit historical workspace.
5. Each experiment binds its data/source/code identity and limitations, including
   PIT coverage and execution-rule status. Uncertified fields never become
   certified by copying a boolean or by completing a job.
6. PIT work runs in parallel: collect versioned security/status evidence where
   available, enforce causal admission and explicit unknown-state handling, and
   keep delisted/unavailable histories and exact auction constraints as visible
   blockers until verified.
7. Tests, cluster integration, production acceptance, documentation and
   reproduction commands accompany completion. LIVE_TRADING_ENABLED=false.

## Reuse/gap audit

- Reuse Qlib Alpha158, DatasetH, LinearModel/LGBModel, TopkDropout, Exchange
  and MLflow. Extend the fixed runner rather than introducing another model
  or matching engine.
- Reuse QTrade manifests, checksum archives, observer publication, and existing
  PIT/instrument/rule contracts after checking their coverage limitations.
- Reuse the existing Git-connected Vercel deployment. The user selected direct
  authenticated Vercel submission rather than an SSH-only console.
- Add a dedicated repository-scoped private queue and research-only credentials;
  no cluster SSH credentials, live gateway, public cluster port or trading
  control authorization is exposed.
- vn.py execution reuse remains outside this research workflow; importing it
  would not supply Vercel job orchestration or missing historical state.

## Milestones

- [x] M1: validated configurations, research authentication and private queue.
- [x] M2: cluster worker, immutable runs and unified catalog/detail publication.
- [x] M3: reorganized pages and submit/status/detail/compare navigation.
- [ ] P0 parallel: causal universe/state audit, historical source ingestion,
      constraint enforcement and per-run unresolved-coverage disclosures.
- [x] M4: targeted/full checks, production deployment and browser acceptance.

Evidence: Linux 544 full-suite tests passed on release da2878c; frontend 201
full-suite tests and nine final workflow/navigation checks passed. A later
market-clock regression test also passes. Both real authenticated local API
requests completed 11,911-file verified archives, with 891 aligned instruments.
See docs/evidence/qlib-workflow-20261026.json. Qlib, publication, source-repair,
market observer and maintenance services are supervised. Vercel preview shows
the integrated form with authentication closed until Production credentials
are configured. That credential handoff and final public-browser submission
are still required; do not mark the overall goal complete.

Production checkpoint: PR #4 merged as 6573e3fc4ab993593bcb9480e1a7d2a0dd4294ae.
Live API and browser confirm two completed aligned runs, newest-run selection,
actual model comparison, fresh worker/data clocks and anonymous POST rejection.
The Production/Secret import dialog is prepared without entering credentials.
User credential setup, redeployment and authenticated UI acceptance remain.
The temporary cluster transfer key has been revoked; no credential values were
committed. Final production evidence is staged locally for the remaining handoff.

2026-10-26 follow-up: the user imported all seven Production values. Redeploy
G8kg8mdRQUPtoPGXZmdyJvz2eJX1 enabled actual browser login. Submission with request
34dd6dd3094342e1871f0046135f5656 was initially unconfirmed; uv lock --check exposed
missing Dulwich/Paramiko dependencies. PR #5 repairs the frozen lock and adds
credential-safe exception-class diagnostics; 26 targeted checks, Ruff, mypy
and Vercel preview pass. Main 2b1e200 now accepts the same browser request;
cancellation was submitted and final cluster/result acceptance is in progress.
Values were saved as Config; sensitive values still need owner-managed Secret
storage. Do not print or copy their contents.

PIT source recovery: last master refresh failed while old accepted coverage
remained visible. Add independent attempt evidence, verified cache reuse with
original clocks, bounded same-scope failure retries and child cleanup. Nine
source/scheduler tests pass locally. Release 6c6092a passes all 556 Linux tests;
the supervised PIT service runs this release and resumes from the verified
master without changing its original clock. Final source coverage remains
pending this bounded pass, and historical certification remains unresolved.

Final production acceptance: login, submission and cancellation were performed
through the production browser. 34dd6dd3094342e1871f0046135f5656 reached CANCELLED;
316ff5642f3344d588c8de2646598314 reached COMPLETE in about 3m34s. Its 11,911-file
archive verifies, and its public detail, metrics and PIT coverage render in the
actual backtest page. Anonymous submission remains 401. The two browser
screenshots and final checkpoint are in docs/evidence. Source certification is
not inferred from this operational acceptance. Secret storage conversion remains
an owner task because Vercel requests email verification before editing.

The resumed 40-stock source pass finalized 33 stocks / 79,299 rows with seven
vendor failures (10002007), preserving the master observation time. Follow-up
state handling distinguishes PARTIAL evidence from a zero process exit: retain
the same scope and back off instead of advancing the sample. Ten focused source
tests pass. The user reported Secret conversion, but the freshly loaded project
list still shows Config; it remains unverified rather than marked complete.

Final checkpoint: the user explicitly chose to retain Config for now, so Secret
conversion is a deferred owner choice, not a production login blocker. Release
24b6298 passes 40 targeted Linux checks after the prior 556-test full suite.
The supervised PIT PID/cwd was verified on this release; the old PARTIAL pass
was preserved and its next schedule corrected to scope 40 at 12:23 Asia/Shanghai.
All five services are running. Accepted coverage/attempt hashes and schedule are
recorded in docs/evidence/pit-source-recovery-20261026.json. The temporary upload
key was revoked. P0 historical PIT availability and exact-rule enforcement remain
open; do not mark these as certified based on this workflow acceptance.

Learning documentation already in the working tree belongs to the previous
request and must be preserved. Existing fixed experiments remain immutable.

Source-bound execution acceptance: PR #7 merged as 82c3295; cluster worker release
846a836 freezes verified raw sources and binds their identity to browser requests,
runs and model comparisons. Production-browser Ridge job 6f04b986 and LightGBM job
4fc474f9 completed from the same b39408fa source. Both 11,955-file archives verify,
and both actual Qlib directional reload proofs pass 12 checks. Production backtest
shows aligned 89.00%/117.23% results and explicitly rejects cross-protocol model
attribution. Only 36/990 stocks have state backfill in these frozen runs; counts
describe the entire input provider, not actual orders. Full Linux 579 tests, final
focused 72, full frontend 204 and final focused nine pass. The wording follow-up
passes five page tests, lint/types/build. Five supervised services remain running
with LIVE disabled and temporary transfer access revoked. Evidence and reproduction
are in docs/QLIB_SOURCE_CONSTRAINTS.md and
docs/evidence/qlib-source-constraints-20261026.json. Independent historical universe,
availability and exact-rule certification remain open; do not mark the overall
goal complete based solely on this operational milestone.
