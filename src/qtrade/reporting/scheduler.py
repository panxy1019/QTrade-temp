from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from hashlib import sha256
from pathlib import Path
from threading import Event, Lock
from typing import Protocol
from zoneinfo import ZoneInfo

from qtrade.instruments import TradingCalendar, TradingStatus

_CHINA_ZONE = ZoneInfo("Asia/Shanghai")
_MAX_REPORT_BYTES = 4 * 1024 * 1024


class DailyReportGenerator(Protocol):
    def __call__(
        self,
        *,
        trading_day: date,
        data_version: str,
        strategy_versions: tuple[str, ...],
        report_root: Path,
        generated_at: datetime | None = None,
    ) -> tuple[Path, str]: ...


@dataclass(frozen=True, slots=True)
class DailyReportScheduleConfig:
    venue: str
    data_version: str
    strategy_versions: tuple[str, ...]
    report_root: Path
    cutoff: time = time(15, 30)

    def __post_init__(self) -> None:
        if (
            not self.venue.strip()
            or len(self.venue) > 32
            or not self.data_version.strip()
            or len(self.data_version) > 256
        ):
            raise ValueError("report venue and data version are required")
        if (
            not self.strategy_versions
            or len(self.strategy_versions) > 128
            or len(set(self.strategy_versions)) != len(self.strategy_versions)
            or any(
                not version.strip() or len(version) > 256
                for version in self.strategy_versions
            )
        ):
            raise ValueError("report strategy versions must be nonempty and unique")
        if self.cutoff.tzinfo is not None:
            raise ValueError("report cutoff must be a local wall-clock time")


class DailyReportScheduler:
    """Generate one immutable PAPER report after cutoff on a verified open session."""

    def __init__(
        self,
        *,
        config: DailyReportScheduleConfig,
        calendar: TradingCalendar,
        generate: DailyReportGenerator,
        clock: Callable[[], datetime],
        incident_handler: Callable[[str], None] | None = None,
    ) -> None:
        self._config = config
        self._calendar = calendar
        self._generate = generate
        self._clock = clock
        self._incident_handler = incident_handler
        self._report_root = config.report_root.resolve()
        self._lock = Lock()
        self._state = "WAITING_FOR_CUTOFF"
        self._last_attempt_day: date | None = None
        self._last_generated_day: date | None = None
        self._last_report_id: str | None = None
        self._last_sha256: str | None = None
        self._error_code: str | None = None

    def poll(self, now: datetime | None = None) -> dict[str, object]:
        timestamp = now or self._clock()
        if timestamp.tzinfo is None:
            raise ValueError("report scheduler clock must be timezone-aware")
        local = timestamp.astimezone(_CHINA_ZONE)
        with self._lock:
            self._poll_locked(local)
            return self._snapshot_locked()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return self._snapshot_locked()

    def _poll_locked(self, local: datetime) -> None:
        trading_day = local.date()
        cutoff = datetime.combine(trading_day, self._config.cutoff, tzinfo=_CHINA_ZONE)
        if local < cutoff:
            self._set_state("WAITING_FOR_CUTOFF")
            return
        try:
            status = self._calendar.status(
                self._config.venue, trading_day, known_on=trading_day
            )
        except LookupError:
            self._set_state("CALENDAR_SESSION_UNAVAILABLE")
            return
        if status is not TradingStatus.OPEN:
            self._set_state("NON_TRADING_DAY")
            return

        report_id = f"paper-{trading_day.isoformat()}"
        target = self._report_root / f"{report_id}.json"
        if target.exists():
            existing = _verified_report_record(target)
            if existing is not None and existing[0] == (
                report_id,
                trading_day,
                self._config.data_version,
                self._config.strategy_versions,
            ):
                self._last_generated_day = trading_day
                self._last_report_id = report_id
                self._last_sha256 = existing[1]
                self._set_state("ALREADY_GENERATED")
            else:
                self._fail("EXISTING_REPORT_IDENTITY_INVALID")
            return

        self._last_attempt_day = trading_day
        try:
            path, digest = self._generate(
                trading_day=trading_day,
                data_version=self._config.data_version,
                strategy_versions=self._config.strategy_versions,
                report_root=self._report_root,
                generated_at=local.astimezone(UTC),
            )
            if path.resolve() != target or _verified_report_digest(path) != digest:
                self._fail("GENERATED_REPORT_VERIFICATION_FAILED")
                return
        except Exception:
            existing = _verified_report_record(target)
            if existing is not None and existing[0] == (
                report_id,
                trading_day,
                self._config.data_version,
                self._config.strategy_versions,
            ):
                self._last_generated_day = trading_day
                self._last_report_id = report_id
                self._last_sha256 = existing[1]
                self._set_state("ALREADY_GENERATED")
            else:
                self._fail("REPORT_GENERATION_FAILED")
            return
        self._last_generated_day = trading_day
        self._last_report_id = report_id
        self._last_sha256 = digest
        self._set_state("GENERATED")

    def _set_state(self, state: str) -> None:
        self._state = state
        self._error_code = None

    def _fail(self, error_code: str) -> None:
        changed = self._error_code != error_code
        self._state = "FAILED"
        self._error_code = error_code
        if changed and self._incident_handler is not None:
            with suppress(Exception):
                self._incident_handler(f"PAPER daily report scheduler: {error_code}")

    def _snapshot_locked(self) -> dict[str, object]:
        return {
            "classification": "TRUSTED_PAPER_EOD_REPORTING",
            "readiness": "CONFIGURED",
            "automatic": True,
            "state": self._state,
            "venue": self._config.venue,
            "timezone": str(_CHINA_ZONE),
            "cutoff": self._config.cutoff.strftime("%H:%M"),
            "data_version": self._config.data_version,
            "strategy_versions": list(self._config.strategy_versions),
            "last_attempt_day": (
                None if self._last_attempt_day is None else self._last_attempt_day.isoformat()
            ),
            "last_generated_day": (
                None
                if self._last_generated_day is None
                else self._last_generated_day.isoformat()
            ),
            "last_report_id": self._last_report_id,
            "last_sha256": self._last_sha256,
            "error_code": self._error_code,
            "caller_supplied_identity": False,
            "external_order_transmission": False,
        }


def run_daily_report_worker(
    scheduler: DailyReportScheduler, stop: Event, *, interval_seconds: float = 30.0
) -> None:
    if interval_seconds <= 0:
        raise ValueError("report scheduler interval must be positive")
    while not stop.is_set():
        scheduler.poll()
        stop.wait(interval_seconds)


def _verified_report_envelope(path: Path) -> tuple[dict[str, object], str] | None:
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_REPORT_BYTES + 1)
        if len(raw) > _MAX_REPORT_BYTES:
            return None
        envelope = json.loads(raw.decode("utf-8"))
        if not isinstance(envelope, dict):
            return None
        report = envelope.get("report")
        if not isinstance(report, dict):
            return None
        digest = envelope.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            return None
        canonical = json.dumps(
            report, sort_keys=True, separators=(",", ":"), default=str
        )
        if sha256(canonical.encode()).hexdigest() != digest:
            return None
        return report, digest
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _verified_report_digest(path: Path) -> str | None:
    verified = _verified_report_envelope(path)
    return None if verified is None else verified[1]


def _verified_report_record(
    path: Path,
) -> tuple[tuple[str, date, str, tuple[str, ...]], str] | None:
    verified = _verified_report_envelope(path)
    if verified is None:
        return None
    try:
        report, digest = verified
        report_id = report.get("report_id")
        raw_trading_day = report.get("trading_day")
        data_version = report.get("data_version")
        raw_strategy_versions = report.get("strategy_versions")
        if (
            not isinstance(report_id, str)
            or path.stem != report_id
            or not isinstance(raw_trading_day, str)
            or not isinstance(data_version, str)
            or not data_version
            or not isinstance(raw_strategy_versions, list)
            or not raw_strategy_versions
            or any(
                not isinstance(version, str) or not version
                for version in raw_strategy_versions
            )
        ):
            return None
        trading_day = date.fromisoformat(raw_trading_day)
        strategy_versions = tuple(raw_strategy_versions)
        return (report_id, trading_day, data_version, strategy_versions), digest
    except (KeyError, TypeError, ValueError):
        return None
