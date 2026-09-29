"""Playwright-backed executor.

Phase 1–2: the click path is intentionally unimplemented.
Phase 6 will fill open_long / open_short with the guarded workflow:
verify health → login → market → symbol → price → signal → duplicate →
risk → ticket fields → confirm-ticket match → Confirm → verify position.
"""

from __future__ import annotations

from trading_bot.exceptions import ExecutionNotEnabled
from trading_bot.monitoring.logger import get_logger
from trading_bot.trading.executor import (
    NullExecutor,
    OrderRequest,
    OrderResult,
    Position,
    TradingExecutor,
)

log = get_logger("trading_bot.playwright_executor")


class PlaywrightExecutor(NullExecutor, TradingExecutor):
    """Does not click trading controls until Phase 6 and TRADING_MODE=DEMO."""

    async def open_long(self, order: OrderRequest) -> OrderResult:
        log.info(
            "would_open_long",
            symbol=order.symbol,
            quantity=order.quantity,
            sl=order.stop_loss,
            tp=order.take_profit,
            signal_id=order.signal_id,
        )
        raise ExecutionNotEnabled(
            "Playwright order execution is not enabled in Phase 1–2. "
            "Use DRY_RUN and wait for Phase 6 (demo clicks)."
        )

    async def open_short(self, order: OrderRequest) -> OrderResult:
        log.info("would_open_short", signal_id=order.signal_id)
        raise ExecutionNotEnabled(
            "Playwright order execution is not enabled in Phase 1–2."
        )

    async def close_position(self, position: Position) -> OrderResult:
        raise ExecutionNotEnabled("Position closing is Phase 8.")
