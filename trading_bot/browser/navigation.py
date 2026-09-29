"""Navigate to the broker trading page and confirm the configured instrument."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from playwright.async_api import Page

from trading_bot.browser.selectors import SelectorRegistry
from trading_bot.config import Settings
from trading_bot.exceptions import ConfigurationError, SymbolMismatch
from trading_bot.monitoring.logger import get_logger

log = get_logger("trading_bot.navigation")

_TV_INTERVALS = {
    "1": "1",
    "1M": "1",
    "3M": "3",
    "5M": "5",
    "15M": "15",
    "30M": "30",
    "45M": "45",
    "1H": "60",
    "2H": "120",
    "3H": "180",
    "4H": "240",
    "1D": "D",
    "D": "D",
    "1W": "W",
    "W": "W",
    "1MO": "M",
}


def normalize_symbol(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", value).upper()


def tradingview_interval(timeframe: str) -> str:
    key = timeframe.strip().upper()
    if key in _TV_INTERVALS:
        return _TV_INTERVALS[key]
    if key.endswith("M") and key[:-1].isdigit():
        return key[:-1]
    return key


def chart_url(settings: Settings) -> str:
    """Build the trading page URL, injecting symbol/timeframe for TradingView."""
    base = (settings.trading_page_url or settings.broker_url).strip()
    if not base:
        raise ConfigurationError("BROKER_URL / TRADING_PAGE_URL is empty.")
    if "tradingview.com" not in base.lower():
        return base
    parsed = urlparse(base)
    query = parse_qs(parsed.query)
    if not query.get("symbol"):
        query["symbol"] = [settings.symbol]
    if not query.get("interval"):
        query["interval"] = [tradingview_interval(settings.timeframe)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def page_matches_symbol(*, title: str, url: str, symbol: str) -> bool:
    expected = normalize_symbol(symbol)
    haystack = normalize_symbol(f"{title} {url}")
    if expected and expected in haystack:
        return True
    title_upper = title.upper()
    if expected == "XAUUSD" and ("GOLD" in title_upper or "XAU" in title_upper):
        return True
    return False


class Navigator:
    def __init__(self, settings: Settings, registry: SelectorRegistry) -> None:
        self.settings = settings
        self.registry = registry

    async def open_broker(self, page: Page) -> None:
        url = chart_url(self.settings)
        log.info("open_broker", url=url, symbol=self.settings.symbol)
        await page.goto(url, wait_until="domcontentloaded")
        await self._ensure_configured_chart(page)

    async def open_trading_page(self, page: Page) -> None:
        url = chart_url(self.settings)
        if page_matches_symbol(
            title=await _safe_title(page), url=page.url, symbol=self.settings.symbol
        ) and "tradingview.com/chart" in page.url:
            log.info("already_on_trading_page", url=page.url)
            return
        log.info("open_trading_page", url=url, symbol=self.settings.symbol)
        await page.goto(url, wait_until="domcontentloaded")
        await self._ensure_configured_chart(page)

    async def _ensure_configured_chart(self, page: Page) -> None:
        if "tradingview.com" not in page.url.lower():
            return
        await page.wait_for_timeout(1_500)
        if page_matches_symbol(
            title=await _safe_title(page), url=page.url, symbol=self.settings.symbol
        ):
            log.info("chart_symbol_ok", symbol=self.settings.symbol, title=await _safe_title(page))
            return
        await self._apply_tradingview_symbol(page)

    async def _apply_tradingview_symbol(self, page: Page) -> None:
        symbol = self.settings.symbol
        log.info("applying_tradingview_symbol", symbol=symbol)
        button = self.registry.optional_locator(page, "symbol_search_button")
        search = self.registry.optional_locator(page, "symbol_search_input")
        try:
            if button is not None:
                await button.first.click(timeout=8_000)
            if search is None:
                search = page.locator('[data-name="symbol-search-items-dialog"] input').first
            await search.first.wait_for(state="visible", timeout=8_000)
            await search.first.fill("")
            await search.first.fill(symbol)
            await search.first.press("Enter")
            await page.wait_for_timeout(2_000)
        except Exception as exc:  # noqa: BLE001
            log.warning("tradingview_symbol_search_failed", error=str(exc), symbol=symbol)
            return
        title = await _safe_title(page)
        if page_matches_symbol(title=title, url=page.url, symbol=symbol):
            log.info("chart_symbol_applied", symbol=symbol, title=title)
        else:
            log.warning("chart_symbol_unconfirmed", symbol=symbol, title=title, url=page.url)

    async def read_displayed_symbol(self, page: Page) -> str | None:
        locator = self.registry.optional_locator(page, "symbol_display")
        if locator is None:
            title = await _safe_title(page)
            if title:
                return title.split()[0]
            return None
        try:
            text = (await locator.first.inner_text()).strip()
            return text or None
        except Exception as exc:  # noqa: BLE001
            log.warning("symbol_read_failed", error=str(exc))
            return None

    async def assert_symbol(self, page: Page) -> str:
        expected = self.settings.symbol
        displayed = await self.read_displayed_symbol(page)
        if displayed is None:
            raise SymbolMismatch(
                "Cannot confirm the selected instrument: selector 'symbol_display' "
                "is missing or empty. Refusing to trade."
            )
        if not page_matches_symbol(title=displayed, url="", symbol=expected) and (
            normalize_symbol(displayed) != normalize_symbol(expected)
        ):
            raise SymbolMismatch(
                f"Displayed symbol '{displayed}' does not match configured SYMBOL '{expected}'."
            )
        log.info("symbol_confirmed", displayed=displayed, expected=expected)
        return displayed


async def _safe_title(page: Page) -> str:
    try:
        return await page.title()
    except Exception:  # noqa: BLE001
        return ""
