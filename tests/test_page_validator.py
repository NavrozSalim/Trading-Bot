from __future__ import annotations

import pytest

from tests.conftest import FakeLocator, FakePage
from trading_bot.browser.navigation import Navigator, normalize_symbol
from trading_bot.browser.selectors import SelectorRegistry
from trading_bot.exceptions import SymbolMismatch
from trading_bot.monitoring.screenshots import ScreenshotManager
from trading_bot.safety.kill_switch import KillSwitch
from trading_bot.safety.page_validator import PageValidator


def _registry() -> SelectorRegistry:
    registry = SelectorRegistry()
    registry.set("logged_in_indicator", "[data-testid='account']")
    registry.set("symbol_display", "[data-testid='symbol']")
    registry.set("price_display", "[data-testid='price']")
    registry.set("trading_panel", "[data-testid='ticket']")
    registry.set("buy_button", "[data-testid='buy']")
    registry.set("sell_button", "[data-testid='sell']")
    registry.set("positions_panel", "[data-testid='positions']")
    registry.set("session_expired", "[data-testid='expired']")
    registry.set("loading_overlay", "[data-testid='loading']")
    registry.set("maintenance_page", "[data-testid='maint']")
    registry.set("error_modal", "[data-testid='error']")
    return registry


def _validator(settings) -> PageValidator:  # type: ignore[no-untyped-def]
    registry = _registry()
    return PageValidator(
        settings,
        registry,
        Navigator(settings, registry),
        ScreenshotManager(settings.screenshots_dir),
        KillSwitch(),
    )


def _healthy_page() -> FakePage:
    return FakePage(
        {
            "[data-testid='account']": FakeLocator(visible=True, count=1, text="Demo"),
            "[data-testid='symbol']": FakeLocator(visible=True, count=1, text="BTCUSD"),
            "[data-testid='price']": FakeLocator(visible=True, count=1, text="68250"),
            "[data-testid='ticket']": FakeLocator(visible=True, count=1),
            "[data-testid='buy']": FakeLocator(visible=True, count=1, enabled=True),
            "[data-testid='sell']": FakeLocator(visible=True, count=1, enabled=True),
            "[data-testid='positions']": FakeLocator(visible=True, count=1),
        }
    )


@pytest.mark.asyncio
async def test_healthy_page_passes(settings) -> None:  # type: ignore[no-untyped-def]
    result = await _validator(settings).validate(_healthy_page())
    assert result.ok is True


@pytest.mark.asyncio
async def test_wrong_symbol_fails_closed(settings) -> None:  # type: ignore[no-untyped-def]
    page = _healthy_page()
    page._locators["[data-testid='symbol']"] = FakeLocator(
        visible=True, count=1, text="ETHUSD"
    )
    validator = _validator(settings)
    result = await validator.validate(page)
    assert result.ok is False
    assert validator.kill_switch.can_open_new_trades() is False
    with pytest.raises(SymbolMismatch):
        await validator.assert_ready_for_new_trade(page)


@pytest.mark.asyncio
async def test_missing_buy_sell_stops_new_trades(settings) -> None:  # type: ignore[no-untyped-def]
    page = _healthy_page()
    page._locators.pop("[data-testid='buy']")
    page._locators.pop("[data-testid='sell']")
    validator = _validator(settings)
    result = await validator.validate(page)
    assert result.ok is False
    assert validator.kill_switch.can_open_new_trades() is False


@pytest.mark.asyncio
async def test_loading_screen_is_not_tradable(settings) -> None:  # type: ignore[no-untyped-def]
    page = _healthy_page()
    page._locators["[data-testid='loading']"] = FakeLocator(visible=True, count=1)
    result = await _validator(settings).validate(page)
    assert result.ok is False


def test_normalize_symbol_ignores_separators() -> None:
    assert normalize_symbol("BTC/USD") == normalize_symbol("btcusd")
