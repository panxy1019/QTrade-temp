from contextlib import nullcontext
from types import SimpleNamespace

from scripts import collect_pit_evidence as collector


def test_backfilled_vendor_state_never_invents_historical_availability(tmp_path, monkeypatch):
    import json

    base = tmp_path
    source = base / "qlib-data-20261023"
    source.mkdir()
    (source / "dataset.json").write_text(json.dumps({
        "stocks": [{"symbol": "000001"}], "end": "2026-10-23", "accepted_symbols": 1,
    }))

    class Result:
        error_code = "0"

        def __init__(self, rows):
            self.fields = list(rows[0])
            self.values = iter(rows)

        def next(self):
            self.row = next(self.values, None)
            return self.row is not None

        def get_row_data(self):
            return list(self.row.values())

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    fake = SimpleNamespace(
        login=lambda: SimpleNamespace(error_code="0"), logout=lambda: None,
        query_stock_basic=lambda: Result([{"code": "sz.000001", "status": "0"}]),
        query_history_k_data_plus=lambda *a, **kw: Result([{
            "code": "sz.000001", "date": "2020-01-02", "tradestatus": "0", "isST": "1",
        }]),
    )
    monkeypatch.setitem(__import__("sys").modules, "baostock", fake)
    monkeypatch.setattr(collector, "baostock_session", nullcontext)
    monkeypatch.setattr(collector, "write_json_atomic", write)
    result = collector.collect(base, 1)
    assert result["historical_available_at_known"] is False
    assert result["historical_pit_certified"] is False
    assert result["execution_enforcement"] == "NOT_USED_DAILY_PROXY_REMAINS"
    assert result["status_rows"] == 1
    assert result["master_inactive_rows"] == 1
    status = json.loads((base / "pit-repair/status/000001.json").read_text())
    assert status["historical_available_at"] is None
    assert status["certified_at_decision"] is False
