# PAPER/SHADOW readiness report

Assessment date: 2026-08-12.

## Delivered software checkpoint

The repository implements and integrates the required local path:

`Data -> Research -> Portfolio -> Backtest/PAPER -> Risk -> OMS -> PaperGateway -> Accounting -> Monitoring -> Control/Recovery`

It also provides a capability-gated, read-only SHADOW event bridge. `RuntimeMode`
has no LIVE member, unsafe configuration is rejected, and every runnable gateway
advertises no external order-transmission capability.

The PAPER bar runner is a serialized next-bar event consumer. Its durable
checkpoint binds strategy, portfolio, venue, and instrument identity to the last
consumed timestamp and pending target. Filled dated orders persist acquisition
lots in the same durable projection as account, OMS, and reservations, enforcing
date-effective T+N sellability after restart.

## Reproduction

```powershell
python -m pip install -e ".[dev]"
python -W error::ResourceWarning -m pytest -q
python -m ruff check .
python -m mypy src
python -m pytest --cov=qtrade --cov-report=term-missing -q
python scripts/run_baseline_workflow.py
```

Acceptance evidence on Python 3.14.6: 122 tests pass, strict mypy and Ruff are
clean, statement coverage is 92%, dependencies are consistent, and the synthetic
workflow produces checksum-linked Parquet/experiment/catalog artifacts on
repeated runs.

Independent safety review reproduced and verified fixes for concurrent target
doubling, adverse-price fill recovery, post-commit audit failure, reservation
aggregation, cancellation persistence, and restart convergence. No known
Critical/P0 defect remains in the tested local PAPER/SHADOW boundary.

## Deliberate boundary and external blockers

- SQLite is a single-process local journal, not a distributed exactly-once store.
- Audit hashes are tamper-evident but do not have an external or WORM tail anchor.
- PAPER matching is deterministic and configurable, not an exchange microstructure model.
- The SHADOW bridge is a protocol primitive; no licensed realtime feed is configured.
- vn.py native broker wheels have incompatible Python ABIs across candidate gateways.
- A data provider, broker, sandbox, and credentials must be selected externally.

No broker login, credential, real account, or external order-transmission path was
created or used. `LIVE_TRADING_ENABLED` remains permanently false in this build.
