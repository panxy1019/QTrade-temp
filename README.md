# Q — A-share and ETF quantitative trading platform

Q is a safety-first personal quantitative trading platform for Shanghai and
Shenzhen A-shares, exchange-traded funds, and index benchmarks.

The repository contains a runnable local PAPER/SHADOW platform checkpoint with
versioned data, research, event-driven backtesting, portfolio construction,
risk/OMS/accounting, recovery, monitoring, and a control plane. Real-money
trading is not enabled.

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

`qtrade.runtime.PaperBarRunner` consumes strictly ordered bars, executes a
strategy's previous target through the durable PAPER pipeline at the next bar
open, and checkpoints the pending target and last consumed event. Dated fills
persist acquisition lots and enforce date-effective T+N sellability across
restart. The runner is intentionally single-process; licensed realtime feeds
belong behind the read-only SHADOW adapter after a provider is selected.

Historical vendor exports can enter through `qtrade.data.CsvBarImporter`. The
canonical UTF-8 CSV schema is exactly `symbol,exchange,timestamp,trading_day,open,high,low,close,volume`;
timestamps must include an offset. Imports are checksum-versioned and must pass
the grouped quality gate before Parquet storage. Vendor-specific column mapping
belongs in a separate licensed-data adapter.

Project status and exact continuation steps are maintained in
[`CURRENT_STATE.md`](CURRENT_STATE.md) and [`plans/MASTER_PLAN.md`](plans/MASTER_PLAN.md).
