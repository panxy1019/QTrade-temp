# Q — A-share and ETF quantitative trading platform

Q is a safety-first personal quantitative trading platform for Shanghai and
Shenzhen A-shares, exchange-traded funds, and index benchmarks.

The project is under active construction. The first executable milestone is a
deterministic paper-trading safety kernel. Real-money trading is not enabled.

## Safety invariant

`LIVE_TRADING_ENABLED` is false by default and cannot be enabled by the current
software. Strategies never call a broker directly: every order follows
portfolio → risk → OMS → execution → gateway.

## Development

Requires Python 3.11 or newer. From the repository root:

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python scripts/run_control_plane.py
```

The local dashboard is then available at `http://127.0.0.1:8000`. It boots
fail-closed (gateway disconnected and reconciliation unknown); the page is an
operational status surface, not authorization for external order transmission.

Control endpoints are disabled unless `QTRADE_CONTROL_TOKEN` is supplied in the
process environment. Send it as `Authorization: Bearer <token>`. Read endpoints
do not accept a caller-provided role escalation. Bind the development server to
loopback unless a separately reviewed TLS/authentication proxy is deployed.

After a restart the PAPER runtime is HALTED. `POST /system/recover` (authenticated)
must complete local reconciliation and issue a fresh readiness attestation before
`POST /system/start` can succeed. Pending/failed fills or uncertain submissions
make recovery fail closed.

Run the complete synthetic provenance workflow with:

```powershell
python scripts/run_baseline_workflow.py
```

It writes ignored runtime artifacts under `runtime/sample`: checksum-verified
Parquet bars and an immutable experiment record containing `git_commit` and
`data_version`.

Project status and exact continuation steps are maintained in
[`CURRENT_STATE.md`](CURRENT_STATE.md) and [`plans/MASTER_PLAN.md`](plans/MASTER_PLAN.md).
