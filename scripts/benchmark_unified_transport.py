"""Bounded public GET A/B probe; no collection ledger, publication or service writes."""

from __future__ import annotations

import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

from scripts.collect_unified_data import PooledSinaSource, fetch


def main() -> None:
    # Exactly sixteen GET attempts. Four connections remain the global ceiling.
    symbols = ["sh600036", "sz000001", "sh600000", "sz000002",
               "sh600519", "sz000333", "sh601318", "sz000651"]
    results = []
    for mode in ("urllib", "pooled"):
        with PooledSinaSource() as pooled:
            source = fetch if mode == "urllib" else pooled
            started = time.monotonic()
            with ThreadPoolExecutor(max_workers=4) as pool:
                captures = list(pool.map(lambda symbol, source=source: source("sina_klines", {
                    "symbol": symbol, "scale": "5", "ma": "no", "datalen": "2",
                }), symbols))
            results.append({"mode": mode, "seconds": time.monotonic() - started,
                            "requested": len(captures),
                            "downloaded": sum(c["status_code"] == 200 and c["body_complete"]
                                              and c["transport_error"] is None for c in captures),
                            "captures": [{**{k: v for k, v in c.items() if k != "body"},
                                          "body_sha256": sha256(c["body"]).hexdigest(),
                                          "body_base64": base64.b64encode(c["body"]).decode()}
                                         for c in captures]})
    print(json.dumps({"schema_version": "qtrade.transport-probe.v1", "results": results}))


if __name__ == "__main__":
    main()
