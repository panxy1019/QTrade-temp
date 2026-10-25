"""Read-only Qlib results; all computation and artifact writes stay on the cluster."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException, Response

from qtrade.research.qlib_publication import validate_publication

router = APIRouter(prefix="/qlib")
URL = "https://raw.githubusercontent.com/panxy1019/QTrade-observations/main/qlib.json"
MAX_BYTES = 2_000_000


@router.get("/baseline")
def baseline(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        bucket = int(datetime.now(UTC).timestamp()) // 60
        request = Request(URL + f"?v={bucket}", headers={"User-Agent": "QTrade-Qlib/1"})
        with urlopen(request, timeout=8) as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("QLIB_PUBLICATION_TOO_LARGE")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("QLIB_PUBLICATION_NOT_OBJECT")
        return validate_publication(value)
    except Exception as error:
        raise HTTPException(503, "Qlib research publication unavailable") from error
