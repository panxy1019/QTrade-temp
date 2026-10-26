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
- [ ] M4: targeted/full checks, production deployment and browser acceptance.

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
source/scheduler tests pass locally; real cluster recovery is not yet certified.

Learning documentation already in the working tree belongs to the previous
request and must be preserved. Existing fixed experiments remain immutable.
