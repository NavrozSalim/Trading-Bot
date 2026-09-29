"""Risk limits. Never invent martingale / grid / size-up-after-loss logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from trading_bot.config import Settings
from trading_bot.exceptions import RiskLimitError
from trading_bot.monitoring.logger import get_logger

log = get_logger("trading_bot.risk")


@dataclass
class RiskState:
    trades_today: int = 0
    open_trades: int = 0
    realized_pnl_today: float = 0.0
    consecutive_losses: int = 0
    day: str = field(default_factory=lambda: datetime.now(timezone.utc).date().isoformat())


class RiskManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state = RiskState()

    def _roll_day(self) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        if self.state.day != today:
            self.state = RiskState(day=today, open_trades=self.state.open_trades)

    def assert_can_open(self, quantity: float | None) -> None:
        self._roll_day()
        if quantity is None or quantity <= 0:
            raise RiskLimitError("Position size could not be calculated confidently.")
        if quantity > self.settings.max_position_size:
            raise RiskLimitError(
                f"Quantity {quantity} exceeds MAX_POSITION_SIZE {self.settings.max_position_size}."
            )
        if self.state.open_trades >= self.settings.max_open_trades:
            raise RiskLimitError("MAX_OPEN_TRADES reached.")
        if self.state.trades_today >= self.settings.max_trades_per_day:
            raise RiskLimitError("MAX_TRADES_PER_DAY reached.")
        if self.state.realized_pnl_today <= -abs(self.settings.max_daily_loss):
            raise RiskLimitError("MAX_DAILY_LOSS reached.")
        if self.state.consecutive_losses >= self.settings.max_consecutive_losses:
            raise RiskLimitError("MAX_CONSECUTIVE_LOSSES reached.")

    def set_open_trades(self, count: int) -> None:
        """Match the live broker count. Call after recording any closes."""
        self._roll_day()
        self.state.open_trades = max(0, int(count))

    def record_open(self) -> None:
        self._roll_day()
        self.state.open_trades += 1
        self.state.trades_today += 1

    def record_close(self, pnl: float) -> None:
        self._roll_day()
        self.state.open_trades = max(0, self.state.open_trades - 1)
        self.state.realized_pnl_today += pnl
        if pnl < 0:
            self.state.consecutive_losses += 1
        elif pnl > 0:
            self.state.consecutive_losses = 0
