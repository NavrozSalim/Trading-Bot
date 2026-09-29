from __future__ import annotations

import pytest

from tests.conftest import FakeLocator, FakePage
from trading_bot.browser.click_guard import ClickGuard
from trading_bot.browser.selectors import SelectorRegistry
from trading_bot.exceptions import PageValidationError, SelectorNotConfigured, SelectorNotFound


def _guard() -> ClickGuard:
    registry = SelectorRegistry()
    registry.set("buy_button", "[data-testid='buy']")
    return ClickGuard(registry)


@pytest.mark.asyncio
async def test_missing_button_raises() -> None:
    page = FakePage()
    with pytest.raises(SelectorNotFound):
        await _guard().resolve(page, "buy_button")


@pytest.mark.asyncio
async def test_disabled_button_raises() -> None:
    page = FakePage(
        {"[data-testid='buy']": FakeLocator(visible=True, enabled=False, count=1)}
    )
    with pytest.raises(PageValidationError, match="disabled"):
        await _guard().resolve(page, "buy_button")


@pytest.mark.asyncio
async def test_ambiguous_button_raises() -> None:
    page = FakePage(
        {"[data-testid='buy']": FakeLocator(visible=True, enabled=True, count=3)}
    )
    with pytest.raises(PageValidationError, match="ambiguous"):
        await _guard().resolve(page, "buy_button")


@pytest.mark.asyncio
async def test_unconfigured_selector_raises() -> None:
    page = FakePage()
    with pytest.raises(SelectorNotConfigured):
        await ClickGuard(SelectorRegistry()).resolve(page, "buy_button")
