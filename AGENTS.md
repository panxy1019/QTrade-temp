# Repository instructions

## Priorities

Capital safety > correctness > research validity > auditability > reliability
> maintainability > performance > convenience > UI.

## Non-negotiable safety rules

- Never log in to a real brokerage account or submit a real order without the
  user's explicit current-stage authorization and credentials.
- Keep `LIVE_TRADING_ENABLED=false`. Do not add a code path that silently or
  automatically enables live trading.
- Strategies cannot access gateways. Enforce the pipeline: portfolio → risk →
  OMS → execution → gateway.
- A halted system cannot accept new risk. Recovery requires an explicit human
  action and successful reconciliation.
- Secrets must not enter source, Git, logs, configuration defaults, or fixtures.

## Engineering workflow

- Read `CURRENT_STATE.md`, `plans/MASTER_PLAN.md`, and `DECISIONS.md` first.
- Preserve user changes and inspect `git status` before editing.
- Prefer reuse/adaptation of vn.py components after a recorded source/gap audit.
- Every milestone requires implementation, tests, integration evidence,
  documentation, a reproduction command, and known limitations.
- Keep architecture decisions in `DECISIONS.md` and ongoing status in
  `CURRENT_STATE.md` and `plans/MASTER_PLAN.md`.

