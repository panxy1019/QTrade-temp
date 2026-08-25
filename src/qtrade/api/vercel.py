from pathlib import Path
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from qtrade.api.app import ApiAuthConfig, bootstrap_context, create_app

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_ROOT = PROJECT_ROOT / "deployment" / "evidence"

context = bootstrap_context()
context.research_comparison_path = EVIDENCE_ROOT / "akshare-v4" / "comparison.json"
context.research_artifact_root = EVIDENCE_ROOT / "akshare-v4"
context.walk_forward_report_path = EVIDENCE_ROOT / "akshare-walk-forward-v1.json"
context.robustness_report_path = EVIDENCE_ROOT / "akshare-robustness-v1.json"
context.factor_search_report_path = EVIDENCE_ROOT / "factor-search" / "report.json"
context.robust_factor_search_report_path = EVIDENCE_ROOT / "factor-robustness" / "report.json"
context.stock_sector_transfer_report_path = EVIDENCE_ROOT / "stock-sector-transfer" / "report.json"
context.historical_manifest_path = EVIDENCE_ROOT / "market-data" / "manifest.json"


class VercelQueryPathApp:
    """Restore rewrite paths and cache only anonymous successful GET responses."""

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
        request_headers = dict(scope.get("headers", []))
        cacheable_request = (
            scope.get("method") == "GET"
            and b"authorization" not in request_headers
            and b"cookie" not in request_headers
        )

        async def send_with_cache(message: Message) -> None:
            if (
                cacheable_request
                and message["type"] == "http.response.start"
                and 200 <= message["status"] < 300
            ):
                cached_message = dict(message)
                headers = list(message.get("headers", []))
                headers.extend(
                    (
                        (b"cache-control", b"public, max-age=15"),
                        (
                            b"vercel-cdn-cache-control",
                            b"public, max-age=60, stale-while-revalidate=300",
                        ),
                    )
                )
                cached_message["headers"] = headers
                await send(cached_message)
                return
            await send(message)

        await self.inner(routed_scope, receive, send_with_cache)


app = VercelQueryPathApp(create_app(context, auth=ApiAuthConfig()))
