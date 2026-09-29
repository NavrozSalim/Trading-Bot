"""Login detection and credential submission.

Never bypasses CAPTCHA or 2FA. If a human step is required the bot PAUSES
and waits until the session is authenticated, then continues.
"""

from __future__ import annotations

import asyncio
from enum import Enum

from playwright.async_api import Page

from trading_bot.browser.selectors import (
    BUILTIN_CAPTCHA_LOCATORS,
    BUILTIN_LOGGED_IN_HINTS,
    BUILTIN_OTP_LOCATORS,
    SelectorRegistry,
)
from trading_bot.config import Settings
from trading_bot.exceptions import (
    AuthenticationError,
    HumanActionRequired,
    SelectorNotConfigured,
)
from trading_bot.monitoring.logger import get_logger
from trading_bot.monitoring.screenshots import ScreenshotManager

log = get_logger("trading_bot.login")


class AuthStatus(str, Enum):
    LOGGED_IN = "logged_in"
    LOGGED_OUT = "logged_out"
    CAPTCHA = "captcha"
    OTP_REQUIRED = "otp_required"
    LOGIN_FAILED = "login_failed"
    UNKNOWN = "unknown"


class LoginManager:
    def __init__(
        self,
        settings: Settings,
        registry: SelectorRegistry,
        screenshots: ScreenshotManager,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.screenshots = screenshots

    async def detect_status(self, page: Page) -> AuthStatus:
        if await self._visible_any(page, BUILTIN_CAPTCHA_LOCATORS) or await self._named_visible(
            page, "captcha"
        ):
            return AuthStatus.CAPTCHA
        if await self._visible_any(page, BUILTIN_OTP_LOCATORS) or await self._named_visible(
            page, "otp_input"
        ):
            return AuthStatus.OTP_REQUIRED
        if await self._named_visible(page, "login_error"):
            return AuthStatus.LOGIN_FAILED
        if await self._named_visible(page, "session_expired"):
            return AuthStatus.LOGGED_OUT
        if await self._is_logged_in(page):
            return AuthStatus.LOGGED_IN
        if await self._login_form_visible(page):
            return AuthStatus.LOGGED_OUT
        return AuthStatus.UNKNOWN

    async def ensure_logged_in(self, page: Page) -> AuthStatus:
        status = await self.detect_status(page)
        log.info("auth_status", status=status.value, url=page.url)

        if status is AuthStatus.LOGGED_IN:
            return status

        if status in {AuthStatus.CAPTCHA, AuthStatus.OTP_REQUIRED}:
            return await self.pause_for_human(page, status)

        if status is AuthStatus.UNKNOWN:
            if await self._is_logged_in(page):
                return AuthStatus.LOGGED_IN
            if not self._has_credentials() and not await self._login_form_visible(page):
                log.info(
                    "using_saved_browser_session",
                    hint="No username/password configured; trusting the persistent profile.",
                )
                return AuthStatus.LOGGED_IN
            if self._has_credentials():
                await self._goto_login(page)
                status = await self.detect_status(page)
                if status is AuthStatus.LOGGED_IN:
                    return status
                if status in {AuthStatus.CAPTCHA, AuthStatus.OTP_REQUIRED}:
                    return await self.pause_for_human(page, status)
            else:
                return await self.pause_for_human(page, AuthStatus.UNKNOWN)

        if status is AuthStatus.LOGGED_OUT:
            if not self._has_credentials():
                log.info("logged_out_session_only_waiting_for_human")
                return await self.pause_for_human(page, status)
            submitted = await self._try_automatic_login(page)
            status = await self.detect_status(page)
            if status is AuthStatus.LOGGED_IN:
                log.info("login_succeeded", automatic=submitted)
                return status
            if status in {AuthStatus.CAPTCHA, AuthStatus.OTP_REQUIRED}:
                return await self.pause_for_human(page, status)
            if status is AuthStatus.LOGIN_FAILED:
                await self.screenshots.capture(page, "login_failed")
                raise AuthenticationError("Broker rejected the login credentials.")
            return await self.pause_for_human(page, AuthStatus.UNKNOWN)

        if status is AuthStatus.LOGIN_FAILED:
            await self.screenshots.capture(page, "login_failed")
            raise AuthenticationError("Login error is visible on the page.")

        return await self.pause_for_human(page, status)

    async def pause_for_human(self, page: Page, reason: AuthStatus) -> AuthStatus:
        await self.screenshots.capture(page, f"human_required_{reason.value}")
        timeout = self.settings.human_action_timeout_seconds
        message = (
            f"BOT PAUSED: {reason.value.replace('_', ' ')} requires manual action in the browser. "
            "Complete CAPTCHA / 2FA / login yourself. The bot will not bypass security. "
            f"Waiting up to {timeout} seconds for a logged-in session..."
        )
        log.warning("human_action_required", reason=reason.value, timeout_seconds=timeout)
        print("\n" + "=" * 72)
        print(message)
        print("=" * 72 + "\n")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            await asyncio.sleep(self.settings.login_poll_interval_seconds)
            status = await self.detect_status(page)
            if status is AuthStatus.LOGGED_IN:
                log.info("human_action_completed_login")
                return status
            if status is AuthStatus.LOGIN_FAILED:
                await self.screenshots.capture(page, "login_failed_after_human")
                raise AuthenticationError("Login failed after manual interaction.")
        await self.screenshots.capture(page, "human_action_timeout")
        raise HumanActionRequired(
            f"Timed out waiting for manual {reason.value} after {timeout} seconds."
        )

    def _has_credentials(self) -> bool:
        username = self.settings.broker_username.strip()
        password = self.settings.broker_password.get_secret_value()
        return bool(username and password)

    async def _try_automatic_login(self, page: Page) -> bool:
        if not self._has_credentials():
            log.info("automatic_login_skipped_no_credentials")
            return False
        username = self.settings.broker_username.strip()
        password = self.settings.broker_password.get_secret_value()
        try:
            user_box = self.registry.locator(page, "login_username")
            pass_box = self.registry.locator(page, "login_password")
            submit = self.registry.locator(page, "login_submit")
        except SelectorNotConfigured:
            log.info("automatic_login_skipped_selectors_missing")
            return False

        log.info("automatic_login_submitting", username_set=bool(username))
        await user_box.first.fill(username)
        await pass_box.first.fill(password)
        await submit.first.click()
        await page.wait_for_timeout(1_000)
        return True

    async def _goto_login(self, page: Page) -> None:
        url = self.settings.login_target_url
        if not url:
            raise AuthenticationError("BROKER_URL / LOGIN_URL is not set.")
        if url not in (page.url or ""):
            log.info("opening_login_page", url=url)
            await page.goto(url, wait_until="domcontentloaded")

    async def _is_logged_in(self, page: Page) -> bool:
        if await self._named_visible(page, "logged_in_indicator"):
            return True
        if await self._named_visible(page, "account_name"):
            return True
        # Weak hints only when no explicit indicator is configured.
        if not self.registry.is_configured("logged_in_indicator"):
            if await self._visible_any(page, BUILTIN_LOGGED_IN_HINTS):
                log.warning(
                    "logged_in_inferred_from_generic_hint",
                    hint="Configure logged_in_indicator for a reliable check.",
                )
                return True
        return False

    async def _login_form_visible(self, page: Page) -> bool:
        if await self._named_visible(page, "login_username"):
            return True
        if await self._named_visible(page, "login_submit"):
            return True
        return False

    async def _named_visible(self, page: Page, name: str) -> bool:
        locator = self.registry.optional_locator(page, name)
        if locator is None:
            return False
        try:
            return await locator.first.is_visible()
        except Exception:  # noqa: BLE001
            return False

    async def _visible_any(self, page: Page, selectors: tuple[str, ...]) -> bool:
        for selector in selectors:
            try:
                if await page.locator(selector).first.is_visible():
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False
