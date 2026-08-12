"""Run the PAPER control plane locally; real trading is unavailable."""

from decimal import Decimal
from pathlib import Path

from qtrade.api import context_from_runtime, create_app
from qtrade.domain import RuntimeMode
from qtrade.execution import FeeSchedule
from qtrade.risk import RiskLimits
from qtrade.runtime import build_paper_runtime

bundle = build_paper_runtime(
    mode=RuntimeMode.PAPER,
    opening_cash=Decimal("100000"),
    limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
    fees=FeeSchedule("local-unconfigured", Decimal("0"), Decimal("0")),
    journal_path=Path("runtime/control-plane.db"),
)
app = create_app(context_from_runtime(bundle))

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
