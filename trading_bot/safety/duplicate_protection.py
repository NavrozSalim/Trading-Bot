"""Prevent opening multiple trades from the same strategy signal."""

from __future__ import annotations

from datetime import datetime

from trading_bot.exceptions import DuplicateSignalError
from trading_bot.monitoring.logger import get_logger
from trading_bot.strategy.signals import Signal, SignalType, build_signal_id

log = get_logger("trading_bot.duplicate_protection")


class DuplicateProtection:
    """In-memory store for Phase 1–2. Persistence is added with the database phase."""

    def __init__(self) -> None:
        self._executed: set[str] = set()

    def known(self) -> frozenset[str]:
        return frozenset(self._executed)

    def remember(self, signal_id: str) -> None:
        self._executed.add(signal_id)

    def was_executed(self, signal_id: str) -> bool:
        return signal_id in self._executed

    def assert_new(self, signal: Signal) -> None:
        if signal.signal is SignalType.NO_TRADE:
            return
        if self.was_executed(signal.signal_id):
            log.warning("duplicate_signal_blocked", signal_id=signal.signal_id)
            raise DuplicateSignalError(f"Signal already executed: {signal.signal_id}")

    def mark_executed(self, signal: Signal) -> None:
        self._executed.add(signal.signal_id)

    @staticmethod
    def make_id(
        symbol: str,
        timeframe: str,
        candle_time: datetime | str,
        signal_type: str | SignalType,
    ) -> str:
        return build_signal_id(symbol, timeframe, candle_time, signal_type)
