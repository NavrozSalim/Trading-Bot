from __future__ import annotations

import pytest

from tests.conftest import FakeLocator, FakePage
from trading_bot.browser.login import AuthStatus, LoginManager
from trading_bot.browser.selectors import SelectorRegistry
from trading_bot.exceptions import HumanActionRequired
from trading_bot.monitoring.screenshots import ScreenshotManager


@pytest.fixture
def login_manager(settings) -> LoginManager:  # type: ignore[no-untyped-def]
    registry = SelectorRegistry()
    registry.set("logged_in_indicator", "[data-testid='account']")
    registry.set("login_username", "#user")
    registry.set("login_password", "#pass")
    registry.set("login_submit", "#submit")
    registry.set("login_error", "[data-testid='login-error']")
    registry.set("captcha", "[data-testid='captcha']")
    registry.set("otp_input", "[data-testid='otp']")
    shots = ScreenshotManager(settings.screenshots_dir)
    return LoginManager(settings, registry, shots)


@pytest.mark.asyncio
async def test_detects_logged_in(login_manager: LoginManager) -> None:
    page = FakePage(
        {"[data-testid='account']": FakeLocator(visible=True, count=1, text="Demo")}
    )
    assert await login_manager.detect_status(page) is AuthStatus.LOGGED_IN


@pytest.mark.asyncio
async def test_detects_captcha_before_login_form(login_manager: LoginManager) -> None:
    page = FakePage(
        {
            "[data-testid='captcha']": FakeLocator(visible=True, count=1),
            "#user": FakeLocator(visible=True, count=1),
        }
    )
    assert await login_manager.detect_status(page) is AuthStatus.CAPTCHA


@pytest.mark.asyncio
async def test_detects_otp(login_manager: LoginManager) -> None:
    page = FakePage({"[data-testid='otp']": FakeLocator(visible=True, count=1)})
    assert await login_manager.detect_status(page) is AuthStatus.OTP_REQUIRED


@pytest.mark.asyncio
async def test_detects_login_failure(login_manager: LoginManager) -> None:
    page = FakePage({"[data-testid='login-error']": FakeLocator(visible=True, count=1)})
    assert await login_manager.detect_status(page) is AuthStatus.LOGIN_FAILED


@pytest.mark.asyncio
async def test_pause_times_out_without_solving_captcha(
    login_manager: LoginManager, settings
) -> None:
    settings.human_action_timeout_seconds = 1
    settings.login_poll_interval_seconds = 0.2
    page = FakePage({"[data-testid='captcha']": FakeLocator(visible=True, count=1)})
    with pytest.raises(HumanActionRequired):
        await login_manager.pause_for_human(page, AuthStatus.CAPTCHA)


@pytest.mark.asyncio
async def test_does_not_login_again_when_session_valid(login_manager: LoginManager) -> None:
    page = FakePage(
        {"[data-testid='account']": FakeLocator(visible=True, count=1, text="ok")}
    )
    status = await login_manager.ensure_logged_in(page)
    assert status is AuthStatus.LOGGED_IN
    assert page.goto_calls == []


@pytest.mark.asyncio
async def test_saved_session_skips_username_password(settings) -> None:  # type: ignore[no-untyped-def]
    settings.broker_username = ""
    settings.broker_password = type(settings.broker_password)("")
    registry = SelectorRegistry()
    manager = LoginManager(
        settings, registry, ScreenshotManager(settings.screenshots_dir)
    )
    page = FakePage(url="https://www.tradingview.com/chart/")
    status = await manager.ensure_logged_in(page)
    assert status is AuthStatus.LOGGED_IN
    assert page.goto_calls == []
