# Vercel deployment boundary

The Vercel deployment is a public, stateless research surface. It serves the
React application at `/` and mounts a FastAPI function at `/api`.

Production URL: `https://qtrade.erapi0neer.xyz`

Aliyun DNS owns the zone. The dedicated `qtrade` CNAME targets Vercel's
project-specific DNS name; the root A record and wildcard CNAME remain unchanged.

- `api/index.py` is the Vercel entrypoint.
- `deployment/evidence/` contains the checksum-bound subset displayed online:
  the 30-ETF daily dataset, 11 baseline experiment records and artifacts,
  three-fold walk-forward report, 14-scenario robustness report, bounded factor
  search, repeated factor robustness and 75-stock sector-transfer evidence.
- `web` uses `/api` only for the `vercel` Vite build mode.
- Vercel rewrites `/api/:path*` to the single Python function while preserving
  the inner FastAPI path through a bounded query-path adapter.
- Anonymous successful GET responses use a 15-second browser cache and a
  60-second Vercel CDN cache with stale-while-revalidate. Requests carrying an
  authorization header or cookie, every POST and every non-2xx response remain
  uncached. The production dashboard refreshes once per minute; local operation
  retains its five-second cadence.
- `ApiAuthConfig()` is intentionally empty, so every control or trade-sensitive
  route fails closed with HTTP 503.
- `/api/metrics/prometheus` is a read-only, low-cardinality Prometheus 0.0.4
  export. It excludes credentials, account/order identifiers and dynamic
  strategy-heartbeat labels; non-finite metrics fail closed.
- No SQLite runtime, provider credential, broker adapter, PAPER session or live
  order authority is deployed.

Local verification:

```bash
.venv/bin/pytest -q tests/test_vercel_entry.py
npm --prefix web run build:vercel
curl -fsS https://qtrade.erapi0neer.xyz/api/metrics/prometheus
```

The evidence bundle is copied byte-for-byte from the accepted local artifacts;
the API revalidates every report envelope, artifact hash and Parquet checksum at
read time. Replacing an artifact without its matching signed envelope causes the
corresponding online result to fail closed.
