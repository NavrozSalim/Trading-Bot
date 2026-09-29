"""Live price reading. Phase 3 will fill DOM / JS / network extractors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from playwright.async_api import Page

from trading_bot.browser.selectors import SelectorRegistry
from trading_bot.exceptions import ExecutionNotEnabled
from trading_bot.monitoring.logger import get_logger

log = get_logger("trading_bot.price_reader")


@dataclass(frozen=True)
class PriceQuote:
    symbol: str
    text: str
    value: float | None
    observed_at: datetime


class PriceReader:
    def __init__(self, registry: SelectorRegistry) -> None:
        self.registry = registry

    async def read(self, page: Page, symbol: str) -> PriceQuote:
        locator = self.registry.optional_locator(page, "price_display")
        if locator is None:
            raise ExecutionNotEnabled(
                "price_display selector is not configured. Complete --setup first."
            )
        text = (await locator.first.inner_text()).strip()
        value = _parse_float(text)
        quote = PriceQuote(
            symbol=symbol,
            text=text,
            value=value,
            observed_at=datetime.now(timezone.utc),
        )
        log.info("price_read", symbol=symbol, text=text, value=value)
        return quote


def _parse_float(text: str) -> float | None:
    cleaned = (
        text.replace(",", "")
        .replace(" ", "")
        .replace("$", "")
        .replace("€", "")
        .replace("£", "")
    )
    try:
        return float(cleaned)
    except ValueError:
        return None
