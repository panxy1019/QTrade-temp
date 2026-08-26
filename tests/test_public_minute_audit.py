from scripts.audit_public_minute_bars import (
    _audit_write_boundary,
    _percentile,
    _select_sector_instruments,
    _signed,
    _validate_series,
)


def test_public_audit_selects_one_instrument_per_sector() -> None:
    report = {
        "minute_dataset": {
            "instruments": [
                {"symbol": "600001", "sector_code": "A", "sector_name": "甲"},
                {"symbol": "600002", "sector_code": "A", "sector_name": "甲"},
                {"symbol": "600003", "sector_code": "B", "sector_name": "乙"},
            ]
        }
    }
    assert _select_sector_instruments(report) == [
        {"symbol": "600001", "sector_code": "A", "sector_name": "甲"},
        {"symbol": "600003", "sector_code": "B", "sector_name": "乙"},
    ]


def test_public_audit_validates_china_session_clock() -> None:
    bars = [
        {
            "trading_day": "2026-08-25",
            "timestamp": f"2026-08-25T{hour:02d}:{minute:02d}:00+08:00",
            "observation_type": "HISTORICAL_FIVE_MINUTE_BAR",
        }
        for hour, minute in (
            [(9, value) for value in range(35, 60, 5)]
            + [(10, value) for value in range(0, 60, 5)]
            + [(11, value) for value in range(0, 31, 5)]
            + [(13, value) for value in range(5, 60, 5)]
            + [(14, value) for value in range(0, 60, 5)]
            + [(15, 0)]
        )
    ]
    assert len(bars) == 48
    payload = {
        "symbol": "600001",
        "selected_trading_day": "2026-08-25",
        "dataset_checksum": "a" * 64,
        "total_count": 48,
        "bars": bars,
    }
    assert _validate_series(
        payload,
        symbol="600001",
        trading_day="2026-08-25",
        checksum="a" * 64,
    ) == []
    payload["bars"][-1]["timestamp"] = "2026-08-25T14:55:00+08:00"
    assert "SESSION_CLOCK_MISMATCH" in _validate_series(
        payload,
        symbol="600001",
        trading_day="2026-08-25",
        checksum="a" * 64,
    )


def test_public_audit_helpers_are_stable() -> None:
    assert _percentile([9.0, 1.0, 5.0, 3.0], 0.95) == 5.0
    assert _signed({"value": 1})["evidence_sha256"] == (
        "48208f9428d64634bd8e28ff345bf0eab60d53c18fa2fbdb0b9bc1e84df2b5f6"
    )


def test_public_audit_requires_every_write_path_to_be_blocked(monkeypatch) -> None:
    class BlockedResponse:
        status = 503

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(
        "scripts.audit_public_minute_bars.urllib.request.urlopen",
        lambda *_args, **_kwargs: BlockedResponse(),
    )
    result = _audit_write_boundary("https://example.test/api")
    assert result["request_count"] == 7
    assert result["all_blocked"] is True
    assert {row["path"] for row in result["checks"]} == {
        "/system/start",
        "/system/stop",
        "/system/pause",
        "/system/resume",
        "/system/kill",
        "/system/recover",
        "/orders/cancel",
    }
