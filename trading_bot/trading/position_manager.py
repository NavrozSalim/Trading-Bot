"""Track bot-owned vs unexpected broker positions. No clicks here."""

from __future__ import annotations

from trading_bot.monitoring.logger import get_logger
from trading_bot.safety.kill_switch import KillSwitch
from trading_bot.trading.executor import Position, TradingExecutor

log = get_logger("trading_bot.positions")


class PositionManager:
    def __init__(self, executor: TradingExecutor, kill_switch: KillSwitch) -> None:
        self.executor = executor
        self.kill_switch = kill_switch
        self._bot_ids: set[str] = set()

    def remember_bot_position(self, broker_id: str) -> None:
        self._bot_ids.add(broker_id)

    async def sync(self) -> list[Position]:
        positions = await self.executor.get_positions()
        unexpected = [
            p for p in positions if p.broker_id not in self._bot_ids and not p.opened_by_bot
        ]
        if unexpected:
            log.error(
                "unexpected_positions",
                ids=[p.broker_id for p in unexpected],
                count=len(unexpected),
            )
            self.kill_switch.pause_new_trades(
                "unexpected positions on the account; resolve before opening new trades"
            )
        return positions
