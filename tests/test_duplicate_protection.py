from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trading_bot.exceptions import DuplicateSignalError
from trading_bot.safety.duplicate_protection import DuplicateProtection
from trading_bot.strategy.signals import Signal, SignalType


def test_same_signal_id_is_blocked() -> None:
    guard = DuplicateProtection()
    signal = Signal(
        signal=SignalType.BUY,
        reason="test",
        symbol="BTCUSD",
        timeframe="5M",
        candle_timestamp="2026-09-21T12:30:00",
    )
    guard.assert_new(signal)
    guard.mark_executed(signal)
    with pytest.raises(DuplicateSignalError):
        guard.assert_new(signal)


def test_no_trade_is_not_stored_as_duplicate() -> None:
    guard = DuplicateProtection()
    signal = Signal(
        signal=SignalType.NO_TRADE,
        reason="none",
        symbol="BTCUSD",
        timeframe="5M",
        candle_timestamp=datetime.now(timezone.utc),
    )
    guard.assert_new(signal)
    guard.assert_new(signal)


def test_make_id_matches_documented_format() -> None:
    signal_id = DuplicateProtection.make_id(
        "BTCUSD", "5M", "2026-09-21T12:30:00", "BUY"
    )
    assert signal_id == "BTCUSD_5M_2026-09-21T12:30_BUY"
