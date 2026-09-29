"""Reject price/candle data that has not moved (or been sampled) recently."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from trading_bot.exceptions import StalePriceError
from trading_bot.monitoring.logger import get_logger

log = get_logger("trading_bot.stale_data")

NowFn = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StaleDataGuard:
    def __init__(self, max_staleness_seconds: int, now: NowFn | None = None) -> None:
        self.max_staleness_seconds = max_staleness_seconds
        self._now = now or _utcnow
        self._last_price: str | None = None
        self._last_change_at: datetime | None = None
        self._last_sample_at: datetime | None = None

    def observe(self, price: str | None) -> None:
        now = self._now()
        self._last_sample_at = now
        if price is None:
            return
        if price != self._last_price:
            self._last_price = price
            self._last_change_at = now

    def seconds_since_change(self) -> float | None:
        if self._last_change_at is None:
            return None
        return (self._now() - self._last_change_at).total_seconds()

    def is_stale(self) -> bool:
        elapsed = self.seconds_since_change()
        if elapsed is None:
            # Never observed a price change — treat as stale once we have a sample.
            return self._last_sample_at is not None
        return elapsed > self.max_staleness_seconds

    def assert_fresh(self) -> None:
        if self.is_stale():
            elapsed = self.seconds_since_change()
            log.error(
                "price_stale",
                last_price=self._last_price,
                seconds_since_change=elapsed,
                max_seconds=self.max_staleness_seconds,
            )
            raise StalePriceError(
                f"Price data is stale (last change {elapsed}s ago; "
                f"limit {self.max_staleness_seconds}s)."
            )
