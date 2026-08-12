"""Runnable PAPER/SHADOW composition root."""

from qtrade.runtime.orchestrator import PaperBarResult, PaperBarRunner, PaperTargetStrategy
from qtrade.runtime.paper import PaperRuntimeBundle, build_paper_runtime
from qtrade.runtime.realtime import (
    FinalizedBarStrategy,
    PendingTarget,
    RealDataPaperResult,
    RealDataPaperRunner,
)

__all__ = [
    "PaperBarResult",
    "PaperBarRunner",
    "PaperRuntimeBundle",
    "PaperTargetStrategy",
    "FinalizedBarStrategy",
    "PendingTarget",
    "RealDataPaperResult",
    "RealDataPaperRunner",
    "build_paper_runtime",
]
