"""One-shot, low-frequency AKShare ETF readiness probe; never starts trading."""

from __future__ import annotations

import argparse
import os

from qtrade.market_data import AkshareMarketDataProvider


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe-size", type=int, default=30)
    args = parser.parse_args()
    acknowledged = (
        os.environ.get("QTRADE_AKSHARE_NONCOMMERCIAL_ACK", "").lower() == "true"
    )
    provider: AkshareMarketDataProvider | None = None
    try:
        provider = AkshareMarketDataProvider(
            noncommercial_research_acknowledged=acknowledged
        )
        provider.connect()
        universe = provider.select_liquid_etf_universe(size=args.universe_size)
    except Exception as error:
        print("provider=AKShare")
        print("ready=false")
        print(f"error_type={type(error).__name__}")
        print(f"detail={str(error).splitlines()[0][:240]}")
        return 2
    finally:
        if provider is not None:
            provider.close()
    print("provider=AKShare")
    print("ready=true")
    print("classification=noncommercial_research_real_data_paper_polling")
    print("production_grade=false")
    print("universe=" + ",".join(universe))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
