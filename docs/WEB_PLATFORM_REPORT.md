# Web platform report

The legacy single HTML shell has been replaced by a React 19, TypeScript, Vite,
Ant Design 6 and ECharts application under `web/`. FastAPI serves the production
bundle at `/` and `/web/` and retains typed read/control APIs.

The application is Simplified Chinese by default, desktop-first and responsive,
with dark/light modes and consistent mode/risk/status semantics. It exposes twelve
routes: overview, market, strategies, research, experiments, backtest, PAPER,
positions, orders/trades, risk, data centre and system monitoring. The prominent
boundary reads “实盘模式不可用”; there is no real-order action or LIVE mode.

Verified:

- TypeScript production build;
- ESLint;
- Vitest/Testing Library safety-boundary test;
- FastAPI serving contract;
- full-page dark-theme browser inspection including navigation, status cards and
  equity/benchmark chart;
- responsive CSS breakpoints and light-theme switch.

Current data-rich limitations are explicit. Provider-backed quotes/K-lines await
legal data authorization. Research/experiment/backtest pages currently expose the
long-term information architecture and empty states; richer catalog/artifact APIs,
pagination and report history remain tracked. The local control plane must remain
loopback-only until read authentication, operator principals, rate/size limits and
security headers receive a separate deployment-hardening review.
