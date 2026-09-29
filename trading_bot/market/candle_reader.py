"""Candle extraction — PLACEHOLDER for your existing candle implementation.

Paste your candle code into this module (or import it from a sibling file).
Preferred sources, in order:
1. DOM values
2. JavaScript page state (page.evaluate)
3. Network responses already visible to the browser
4. Canvas/chart data if accessible

Do not use OCR. Do not infer candles from pixels unless no other option exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from playwright.async_api import Page

from trading_bot.exceptions import ExecutionNotEnabled
from trading_bot.monitoring.logger import get_logger

log = get_logger("trading_bot.candle_reader")


@dataclass(frozen=True)
class Candle:
    timestamp: datetime | str
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    is_closed: bool = True


class CandleReader:
    """Phase 3 hook. Returns nothing until you paste candle extraction code."""

    async def read(self, page: Page, *, symbol: str, timeframe: str) -> list[Candle]:
        log.warning(
            "candle_reader_not_implemented",
            symbol=symbol,
            timeframe=timeframe,
            hint="Paste your candle implementation into market/candle_reader.py",
        )
        raise ExecutionNotEnabled(
            "Candle reader is not implemented yet (Phase 3). "
            "Paste your candle code into trading_bot/market/candle_reader.py."
        )

    async def read_from_dom(self, page: Page) -> list[Candle]:
        raise ExecutionNotEnabled("DOM candle extraction is not configured.")

    async def read_from_javascript(self, page: Page, expression: str) -> Any:
        """Inspect page JS state only through Playwright evaluate — never via OCR."""
        return await page.evaluate(expression)
