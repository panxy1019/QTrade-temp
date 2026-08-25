"""Provider-neutral realtime/historical market data, recording and replay."""

from typing import TYPE_CHECKING, Any

from qtrade.market_data.akshare import (
    AkshareMarketDataProvider,
    AksharePollingPolicy,
    NativeAkshareClient,
)
from qtrade.market_data.baostock_five_minute import (
    BAOSTOCK_FIVE_MINUTE_FIELDS,
    fetch_baostock_five_minute_history,
)
from qtrade.market_data.models import (
    CanonicalTick,
    MarketSnapshot,
    ProviderCapabilities,
    ProviderConnectionStatus,
    ProviderStatus,
    TickQualityIssue,
)
from qtrade.market_data.provider import (
    HistoricalDataProvider,
    MarketDataProvider,
    ProviderBridgeClient,
    RealtimeMarketDataProvider,
)
from qtrade.market_data.quality import TickQualityGate
from qtrade.market_data.readiness import (
    MarketDataReadinessAttestation,
    MarketDataReadinessGate,
)
from qtrade.market_data.recording import (
    AvailableBar,
    CanonicalTickStore,
    RawTickArchive,
    TickBarAggregator,
    TickReplay,
)
from qtrade.market_data.rqdata import RqDataProvider
from qtrade.market_data.rqdata_native import NativeRqDataBridgeClient
from qtrade.market_data.selection import (
    ProviderChoice,
    ProviderSelectionStatus,
    inspect_provider_selection,
)
from qtrade.market_data.sina_minute import (
    SINA_COMPLETE_SESSION_TIMES,
    SINA_FIVE_MINUTE_SESSION_TIMES,
    SinaMinuteHistoryClient,
    complete_session_days,
)
from qtrade.market_data.xtdata import XtDataProvider

if TYPE_CHECKING:
    from qtrade.market_data.paper_session import (
        MultiAssetRecordedPaperEvent,
        MultiAssetRecordedPaperSession,
        RecordedPaperEvent,
        RecordedPaperSession,
    )


def __getattr__(name: str) -> Any:
    if name in {
        "MultiAssetRecordedPaperEvent",
        "MultiAssetRecordedPaperSession",
        "RecordedPaperEvent",
        "RecordedPaperSession",
    }:
        from qtrade.market_data.paper_session import (
            MultiAssetRecordedPaperEvent,
            MultiAssetRecordedPaperSession,
            RecordedPaperEvent,
            RecordedPaperSession,
        )

        return {
            "MultiAssetRecordedPaperEvent": MultiAssetRecordedPaperEvent,
            "MultiAssetRecordedPaperSession": MultiAssetRecordedPaperSession,
            "RecordedPaperEvent": RecordedPaperEvent,
            "RecordedPaperSession": RecordedPaperSession,
        }[name]
    raise AttributeError(name)

__all__ = [
    "AkshareMarketDataProvider",
    "AksharePollingPolicy",
    "BAOSTOCK_FIVE_MINUTE_FIELDS",
    "AvailableBar",
    "CanonicalTick",
    "CanonicalTickStore",
    "HistoricalDataProvider",
    "MarketDataProvider",
    "MarketDataReadinessAttestation",
    "MarketDataReadinessGate",
    "MarketSnapshot",
    "MultiAssetRecordedPaperEvent",
    "MultiAssetRecordedPaperSession",
    "NativeAkshareClient",
    "NativeRqDataBridgeClient",
    "ProviderBridgeClient",
    "ProviderCapabilities",
    "ProviderConnectionStatus",
    "ProviderChoice",
    "ProviderSelectionStatus",
    "ProviderStatus",
    "RawTickArchive",
    "RecordedPaperEvent",
    "RecordedPaperSession",
    "RealtimeMarketDataProvider",
    "RqDataProvider",
    "SINA_COMPLETE_SESSION_TIMES",
    "SINA_FIVE_MINUTE_SESSION_TIMES",
    "SinaMinuteHistoryClient",
    "TickBarAggregator",
    "TickQualityGate",
    "TickQualityIssue",
    "TickReplay",
    "XtDataProvider",
    "complete_session_days",
    "fetch_baostock_five_minute_history",
    "inspect_provider_selection",
]
