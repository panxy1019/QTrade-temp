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

Project status and exact continuation steps are maintained in
[`CURRENT_STATE.md`](CURRENT_STATE.md) and [`plans/MASTER_PLAN.md`](plans/MASTER_PLAN.md).
