from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trading_bot.exceptions import StalePriceError
from trading_bot.safety.stale_data import StaleDataGuard


def test_price_change_is_fresh() -> None:
    now = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    guard = StaleDataGuard(max_staleness_seconds=10, now=lambda: now)
    guard.observe("100")
    assert guard.is_stale() is False
    guard.assert_fresh()


def test_unchanged_price_eventually_stale() -> None:
    current = {"t": datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)}

    def now() -> datetime:
        return current["t"]

    guard = StaleDataGuard(max_staleness_seconds=1, now=now)
    guard.observe("100")
    current["t"] = current["t"] + timedelta(seconds=5)
    assert guard.is_stale() is True
    with pytest.raises(StalePriceError):
        guard.assert_fresh()
