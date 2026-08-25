from pathlib import Path

from fastapi import FastAPI

from qtrade.api.app import ApiAuthConfig, bootstrap_context, create_app

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_ROOT = PROJECT_ROOT / "deployment" / "evidence"

context = bootstrap_context()
context.factor_search_report_path = EVIDENCE_ROOT / "factor-search" / "report.json"
context.robust_factor_search_report_path = EVIDENCE_ROOT / "factor-robustness" / "report.json"
context.stock_sector_transfer_report_path = EVIDENCE_ROOT / "stock-sector-transfer" / "report.json"
context.historical_manifest_path = EVIDENCE_ROOT / "market-data" / "manifest.json"

app = FastAPI(title="QTrade read-only deployment", version="0.2.0")
app.mount("/api", create_app(context, auth=ApiAuthConfig()))
