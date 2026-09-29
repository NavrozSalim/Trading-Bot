from __future__ import annotations

from datetime import datetime

from trading_bot.strategy.signals import Signal, SignalType, build_signal_id
from trading_bot.strategy.strategy import PlaceholderStrategy


def test_signal_id_format() -> None:
    signal_id = build_signal_id(
        "BTCUSD",
        "5M",
        datetime(2026, 9, 21, 12, 30),
        SignalType.BUY,
    )
    assert signal_id == "BTCUSD_5M_2026-09-21T12:30_BUY"


def test_placeholder_strategy_never_invents_entries() -> None:
    strategy = PlaceholderStrategy()
    signal = strategy.evaluate([], symbol="EURUSD", timeframe="15M")
    assert signal.signal is SignalType.NO_TRADE
    assert signal.reason == "strategy_not_configured"
    assert signal.symbol == "EURUSD"


def test_signal_model_fills_id() -> None:
    signal = Signal(
        signal=SignalType.SELL,
        reason="user_rule",
        symbol="BTCUSD",
        timeframe="5M",
        candle_timestamp="2026-09-21T12:30:00",
    )
    assert signal.signal_id == "BTCUSD_5M_2026-09-21T12:30_SELL"
