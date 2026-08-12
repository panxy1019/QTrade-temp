from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DailyPaperReport:
    report_id: str
    trading_day: date
    generated_at: datetime
    data_version: str
    strategy_versions: tuple[str, ...]
    market_data_summary: tuple[tuple[str, str], ...]
    signals: tuple[dict[str, object], ...]
    trades: tuple[dict[str, object], ...]
    positions: tuple[dict[str, object], ...]
    pnl: tuple[tuple[str, str], ...]
    risk: tuple[tuple[str, str], ...]
    data_quality: tuple[tuple[str, str], ...]
    incidents: tuple[str, ...]
    strategy_health: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not self.report_id or not self.data_version or self.generated_at.tzinfo is None:
            raise ValueError("daily report identity, version and timestamp are required")
        if not self.strategy_versions:
            raise ValueError("daily report requires exact strategy versions")


class DailyReportStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def save(self, report: DailyPaperReport) -> tuple[Path, str]:
        payload = asdict(report)
        payload["trading_day"] = report.trading_day.isoformat()
        payload["generated_at"] = report.generated_at.isoformat()
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        digest = sha256(canonical.encode()).hexdigest()
        self._root.mkdir(parents=True, exist_ok=True)
        target = self._root / f"{report.report_id}.json"
        if target.exists():
            raise FileExistsError("daily report artifacts are immutable")
        target.write_text(
            json.dumps({"report": payload, "sha256": digest}, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        return target, digest

    def verify(self, path: Path) -> bool:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        canonical = json.dumps(
            envelope["report"], sort_keys=True, separators=(",", ":"), default=str
        )
        return sha256(canonical.encode()).hexdigest() == str(envelope["sha256"])
