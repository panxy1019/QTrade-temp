import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from qtrade.accounting import Ledger
from qtrade.audit import AuditLog
from qtrade.domain import RuntimeMode
from qtrade.execution import FeeSchedule, PaperGateway
from qtrade.oms import OrderManager
from qtrade.risk import RiskEngine, RiskLimits
from qtrade.service import TradingService


def test_paper_and_shadow_service_require_runtime_guard():
    audit = AuditLog()
    ledger = Ledger(Decimal("1"), audit)
    oms = OrderManager(audit)
    risk = RiskEngine(RiskLimits(Decimal("1"), 1, Decimal("1")), audit)
    gateway = PaperGateway(oms, ledger, audit, FeeSchedule("test", Decimal("0"), Decimal("0")))
    for mode in (RuntimeMode.PAPER, RuntimeMode.SHADOW):
        with pytest.raises(ValueError, match="runtime submission guard"):
            TradingService(risk, oms, gateway, mode)


def test_strategies_and_portfolio_do_not_import_execution_layer():
    root = Path(__file__).parents[1] / "src" / "qtrade"
    for directory in (root / "strategies", root / "portfolio"):
        for path in directory.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert "qtrade.execution" not in source
            assert "PaperGateway" not in source


def test_runtime_imports_in_a_clean_interpreter_without_market_data_cycle():
    result = subprocess.run(
        [sys.executable, "-c", "import qtrade.runtime; import qtrade.market_data"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
