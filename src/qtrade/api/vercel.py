from pathlib import Path
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Receive, Scope, Send

from qtrade.api.app import ApiAuthConfig, bootstrap_context, create_app

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_ROOT = PROJECT_ROOT / "deployment" / "evidence"

context = bootstrap_context()
context.factor_search_report_path = EVIDENCE_ROOT / "factor-search" / "report.json"
context.robust_factor_search_report_path = EVIDENCE_ROOT / "factor-robustness" / "report.json"
context.stock_sector_transfer_report_path = EVIDENCE_ROOT / "stock-sector-transfer" / "report.json"
context.historical_manifest_path = EVIDENCE_ROOT / "market-data" / "manifest.json"


class VercelQueryPathApp:
    """Restore the API path carried by the Vercel rewrite query."""

    def __init__(self, inner: ASGIApp) -> None:
        self.inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return
        query = parse_qs(scope.get("query_string", b"").decode("utf-8"))
        route = query.get("route", ["/"])[0]
        if not route.startswith("/") or len(route) > 2048:
            route = "/__invalid_vercel_route__"
        routed_scope = dict(scope)
        routed_scope["path"] = route
        routed_scope["raw_path"] = route.encode("utf-8")
        await self.inner(routed_scope, receive, send)


app = VercelQueryPathApp(create_app(context, auth=ApiAuthConfig()))
