"""Probe an authorized RQData market-data session without exposing credentials."""

from __future__ import annotations

import argparse
from importlib.util import find_spec

from qtrade.market_data import (
    NativeRqDataBridgeClient,
    ProviderChoice,
    RqDataProvider,
    inspect_provider_selection,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--connect", action="store_true")
    parser.add_argument("--symbol", default="510300")
    args = parser.parse_args()

    selection = inspect_provider_selection(ProviderChoice.RQDATA)
    print("classification=READ_ONLY_MARKET_DATA_PROBE")
    print(f"sdk_installed={str(find_spec('rqdatac') is not None).lower()}")
    print("external_order_transmission=false")
    if selection.missing_configuration:
        print("missing_configuration=" + ",".join(selection.missing_configuration))
    if not args.connect:
        print("connection_attempted=false")
        return 0 if not selection.missing_configuration else 2
    if selection.missing_configuration:
        print("connection_attempted=false")
        return 2

    provider = RqDataProvider(NativeRqDataBridgeClient())
    try:
        provider.connect()
        heartbeat = provider.heartbeat()
        snapshot = provider.get_snapshot((args.symbol,))[0]
        capabilities = provider.capabilities
        print("connection_attempted=true")
        print("authorized_session=true")
        print(f"provider={capabilities.provider}")
        print(f"sdk_version={capabilities.sdk_version}")
        print(f"realtime_ticks={str(capabilities.ticks).lower()}")
        print(f"minute_bars={str(capabilities.minute_bars).lower()}")
        print(f"snapshot_instrument={snapshot.instrument}")
        print(f"snapshot_exchange={snapshot.exchange}")
        print(f"snapshot_price={snapshot.price}")
        print(f"snapshot_exchange_at={snapshot.timestamp_exchange.isoformat()}")
        print(f"heartbeat_at={heartbeat.isoformat()}")
        return 0
    except Exception as error:
        print("connection_attempted=true")
        print("authorized_session=false")
        print(f"failure_type={type(error).__name__}")
        return 3
    finally:
        provider.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
