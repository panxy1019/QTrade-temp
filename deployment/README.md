# Vercel deployment boundary

The Vercel deployment is a public, stateless research surface. It serves the
React application at `/` and mounts a FastAPI function at `/api`.

- `api/index.py` is the Vercel entrypoint.
- `deployment/evidence/` contains the checksum-bound subset displayed online.
- `web` uses `/api` only for the `vercel` Vite build mode.
- `ApiAuthConfig()` is intentionally empty, so every control or trade-sensitive
  route fails closed with HTTP 503.
- No SQLite runtime, provider credential, broker adapter, PAPER session or live
  order authority is deployed.

Local verification:

```bash
.venv/bin/pytest -q tests/test_vercel_entry.py
npm --prefix web run build:vercel
```

The evidence bundle is copied byte-for-byte from the accepted local artifacts;
the API revalidates every report envelope, artifact hash and Parquet checksum at
read time. Replacing an artifact without its matching signed envelope causes the
corresponding online result to fail closed.
