from scripts.build_akshare_sector_stock_universe import (
    SECTOR_DEFINITIONS,
    select_sector_balanced_universe,
)


def test_sector_universe_selects_highest_csi_weights_without_overlap():
    csi_rows = []
    sector_rows = {}
    for sector_index, (sector_code, _) in enumerate(SECTOR_DEFINITIONS):
        rows = []
        for stock_index in range(7):
            symbol = f"{sector_index:02d}{stock_index:04d}"
            csi_rows.append(
                {
                    "日期": "2026-07-31",
                    "成分券代码": symbol,
                    "成分券名称": f"股票{symbol}",
                    "交易所": "深圳证券交易所",
                    "权重": str(stock_index + 1),
                }
            )
            rows.append(
                {
                    "证券代码": symbol,
                    "证券名称": f"股票{symbol}",
                    "计入日期": "2025-01-01",
                }
            )
        sector_rows[sector_code] = rows
    for index in range(195):
        symbol = f"9{index:05d}"
        csi_rows.append(
            {
                "日期": "2026-07-31",
                "成分券代码": symbol,
                "成分券名称": f"填充{symbol}",
                "交易所": "上海证券交易所",
                "权重": "0",
            }
        )

    selected = select_sector_balanced_universe(
        csi_rows, sector_rows, per_sector=5
    )
    assert len(selected) == 75
    assert len({item["symbol"] for item in selected}) == 75
    assert all(
        sum(item["sector_code"] == sector_code for item in selected) == 5
        for sector_code, _ in SECTOR_DEFINITIONS
    )
    first_sector = [
        item["symbol"]
        for item in selected
        if item["sector_code"] == SECTOR_DEFINITIONS[0][0]
    ]
    assert first_sector == ["000006", "000005", "000004", "000003", "000002"]


def test_sector_universe_fails_when_a_sector_has_too_few_index_members():
    csi_rows = [
        {
            "日期": "2026-07-31",
            "成分券代码": f"00000{index}",
            "成分券名称": f"股票{index}",
            "交易所": "深圳证券交易所",
            "权重": str(index),
        }
        for index in range(5)
    ]
    csi_rows.extend(
        {
            "日期": "2026-07-31",
            "成分券代码": f"9{index:05d}",
            "成分券名称": f"填充{index}",
            "交易所": "上海证券交易所",
            "权重": "0",
        }
        for index in range(295)
    )
    sector_rows = {
        sector_code: [
            {
                "证券代码": f"00000{index}",
                "证券名称": f"股票{index}",
                "计入日期": "2025-01-01",
            }
            for index in range(5)
        ]
        for sector_code, _ in SECTOR_DEFINITIONS
    }
    sector_rows[SECTOR_DEFINITIONS[0][0]] = sector_rows[SECTOR_DEFINITIONS[0][0]][:4]
    try:
        select_sector_balanced_universe(csi_rows, sector_rows, per_sector=5)
    except ValueError as error:
        assert "too few" in str(error)
    else:
        raise AssertionError("sector underfill must fail closed")
