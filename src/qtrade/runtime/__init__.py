"""Runnable PAPER/SHADOW composition root."""

from qtrade.runtime.orchestrator import PaperBarResult, PaperBarRunner, PaperTargetStrategy
from qtrade.runtime.paper import PaperRuntimeBundle, build_paper_runtime

__all__ = [
    "PaperBarResult",
    "PaperBarRunner",
    "PaperRuntimeBundle",
    "PaperTargetStrategy",
    "build_paper_runtime",
]
