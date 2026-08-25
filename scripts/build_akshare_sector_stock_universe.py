"""Freeze a sector-balanced current CSI 300 stock universe for diagnostics."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from importlib import import_module
from pathlib import Path

SECTOR_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("801780", "银行"),
    ("801790", "非银金融"),
    ("801120", "食品饮料"),
    ("801150", "医药生物"),
    ("801080", "电子"),
    ("801750", "计算机"),
    ("801730", "电力设备"),
    ("801880", "汽车"),
    ("801890", "机械设备"),
    ("801050", "有色金属"),
    ("801030", "基础化工"),
    ("801160", "公用事业"),
    ("801740", "国防军工"),
    ("801770", "通信"),
    ("801760", "传媒"),
)


def _symbol(value: object) -> str:
    result = str(value).strip().removesuffix(".0").zfill(6)
    if len(result) != 6 or not result.isdigit():
        raise ValueError("constituent symbol must contain six digits")
    return result


def _weight(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("CSI constituent weight must be numeric") from error
    if not result.is_finite() or result < 0:
        raise ValueError("CSI constituent weight must be finite and nonnegative")
    return result


def select_sector_balanced_universe(
    csi_rows: Iterable[Mapping[str, object]],
    sector_rows: Mapping[str, Iterable[Mapping[str, object]]],
    *,
    per_sector: int = 5,
) -> tuple[dict[str, object], ...]:
    if per_sector <= 0:
        raise ValueError("stocks per sector must be positive")
    constituents: dict[str, dict[str, object]] = {}
    constituent_dates: set[str] = set()
    for row in csi_rows:
        symbol = _symbol(row.get("成分券代码"))
        if symbol in constituents:
            raise ValueError("CSI constituent rows contain duplicate symbols")
        constituent_date = str(row.get("日期", "")).strip()
        if not constituent_date:
            raise ValueError("CSI constituent row is missing its as-of date")
        constituent_dates.add(constituent_date)
        constituents[symbol] = {
            "symbol": symbol,
            "name": str(row.get("成分券名称", "")).strip(),
            "exchange": str(row.get("交易所", "")).strip(),
            "csi300_weight": _weight(row.get("权重")),
            "constituent_as_of": constituent_date,
        }
    if len(constituents) != 300 or len(constituent_dates) != 1:
        raise ValueError("expected one dated 300-member CSI constituent snapshot")

    selected: list[dict[str, object]] = []
    seen: set[str] = set()
    for sector_code, sector_name in SECTOR_DEFINITIONS:
        rows = sector_rows.get(sector_code)
        if rows is None:
            raise ValueError(f"missing SW sector rows for {sector_code}")
        membership: dict[str, str] = {}
        for row in rows:
            symbol = _symbol(row.get("证券代码"))
            included_at = str(row.get("计入日期", "")).strip()
            if not included_at:
                raise ValueError("SW sector row is missing its inclusion date")
            membership[symbol] = included_at
        eligible = [
            constituents[symbol]
            for symbol in membership
            if symbol in constituents and symbol not in seen
        ]
        eligible.sort(
            key=lambda item: (-Decimal(str(item["csi300_weight"])), str(item["symbol"]))
        )
        if len(eligible) < per_sector:
            raise ValueError(f"sector {sector_code} has too few eligible CSI 300 members")
        for item in eligible[:per_sector]:
            symbol = str(item["symbol"])
            seen.add(symbol)
            selected.append(
                {
                    **item,
                    "sector_code": sector_code,
                    "sector_name": sector_name,
                    "sw_included_at": membership[symbol],
                }
            )
    expected = len(SECTOR_DEFINITIONS) * per_sector
    if len(selected) != expected or len(seen) != expected:
        raise ValueError("sector-balanced universe identity is not unique and complete")
    return tuple(selected)


def _sign(value: dict[str, object]) -> dict[str, object]:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return {**value, "evidence_sha256": sha256(canonical.encode()).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "runtime/universes/akshare-csi300-sw15x5-current-20260731-v1.json"
        ),
    )
    args = parser.parse_args()
    akshare = import_module("akshare")
    csi_frame = akshare.index_stock_cons_weight_csindex(symbol="000300")
    sector_frames = {
        sector_code: akshare.index_component_sw(symbol=sector_code)
        for sector_code, _ in SECTOR_DEFINITIONS
    }
    stocks = select_sector_balanced_universe(
        csi_frame.to_dict(orient="records"),
        {
            code: frame.to_dict(orient="records")
            for code, frame in sector_frames.items()
        },
    )
    constituent_as_of = str(stocks[0]["constituent_as_of"])
    payload = _sign(
        {
            "universe_id": f"akshare-csi300-sw15x5-current-{constituent_as_of.replace('-', '')}-v1",
            "provider": "AKShare",
            "provider_version": str(akshare.__version__),
            "source_functions": [
                "index_stock_cons_weight_csindex",
                "index_component_sw",
            ],
            "known_at": datetime.now(UTC).isoformat(),
            "constituent_as_of": constituent_as_of,
            "classification": "current_csi300_sw1_sector_balanced_non_pit",
            "point_in_time": False,
            "survivorship_bias_warning": (
                "Current 2026 constituents are projected backward and are not a "
                "point-in-time historical universe."
            ),
            "selection": {
                "index": "CSI 300",
                "sector_taxonomy": "SW level 1 current membership",
                "sector_count": len(SECTOR_DEFINITIONS),
                "stocks_per_sector": 5,
                "within_sector_rank": "current CSI 300 weight descending",
                "candidate_data_access_before_freeze": False,
            },
            "sectors": [
                {"sector_code": code, "sector_name": name}
                for code, name in SECTOR_DEFINITIONS
            ],
            "symbols": [item["symbol"] for item in stocks],
            "stocks": stocks,
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite frozen universe: {args.output}")
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
