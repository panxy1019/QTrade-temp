from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from decimal import Decimal

from qtrade.data.models import Bar, QualityIssue


class BarQualityGate:
    def __init__(self, *, abnormal_jump_threshold: Decimal = Decimal("0.50")) -> None:
        if abnormal_jump_threshold <= 0:
            raise ValueError("abnormal_jump_threshold must be positive")
        self._jump_threshold = abnormal_jump_threshold

    def validate(
        self,
        bars: Iterable[Bar],
        *,
        expected_symbol: str | None = None,
        expected_trading_days: set[object] | None = None,
    ) -> tuple[QualityIssue, ...]:
        records = list(bars)
        issues: list[QualityIssue] = []
        identities = [(bar.symbol, bar.exchange, bar.timestamp) for bar in records]
        duplicates = {identity for identity, count in Counter(identities).items() if count > 1}
        previous_by_instrument: dict[tuple[str, str], Bar] = {}
        observed_days: set[object] = set()
        for row, bar in enumerate(records):
            observed_days.add(bar.trading_day)
            if identities[row] in duplicates:
                issues.append(QualityIssue("DUPLICATE", "duplicate symbol timestamp", row))
            if bar.timestamp.tzinfo is None:
                issues.append(QualityIssue("MISSING_TIMEZONE", "timestamp is naive", row))
            if expected_symbol is not None and bar.symbol != expected_symbol:
                issues.append(QualityIssue("SYMBOL_MISMATCH", "unexpected symbol", row))
            numeric_values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
            if any(not value.is_finite() for value in numeric_values):
                issues.append(QualityIssue("NON_FINITE_VALUE", "numeric value is not finite", row))
                continue
            if bar.volume < 0:
                issues.append(QualityIssue("NEGATIVE_VOLUME", "volume is negative", row))
            if min(bar.open, bar.high, bar.low, bar.close) <= 0:
                issues.append(QualityIssue("NON_POSITIVE_PRICE", "price must be positive", row))
            if bar.high < max(bar.open, bar.low, bar.close) or bar.low > min(
                bar.open, bar.high, bar.close
            ):
                issues.append(QualityIssue("INVALID_OHLC", "OHLC relationship is invalid", row))
            key = (bar.symbol, bar.exchange)
            previous = previous_by_instrument.get(key)
            if previous is not None:
                if bar.timestamp <= previous.timestamp:
                    issues.append(
                        QualityIssue("TIMESTAMP_DISORDER", "timestamps are unordered", row)
                    )
                if (
                    previous.close > 0
                    and abs(bar.close / previous.close - 1) > self._jump_threshold
                ):
                    issues.append(
                        QualityIssue("ABNORMAL_JUMP", "close-to-close jump exceeded", row)
                    )
            previous_by_instrument[key] = bar
        if expected_trading_days is not None:
            missing = expected_trading_days - observed_days
            if missing:
                issues.append(QualityIssue("CALENDAR_MISMATCH", f"missing trading days: {missing}"))
        return tuple(issues)
