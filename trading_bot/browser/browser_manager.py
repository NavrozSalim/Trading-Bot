"""Playwright lifecycle: persistent Chromium profile, cookies, session restore.

Chrome named profiles are attached over CDP. Playwright's launch_persistent_context
hangs on a real Chrome User Data directory (Profile 52), so we start Chrome ourselves
and connect. Does not place trades. Headless defaults to False for development.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from trading_bot.browser.chrome_profile import (
    assert_chrome_unlocked,
    assert_named_profile_exists,
    copy_named_profile,
    cdp_endpoint,
    cdp_is_ready,
    find_chrome_executable,
    is_default_chrome_user_data_dir,
    spawn_chrome_with_cdp,
)
from trading_bot.config import Settings
from trading_bot.exceptions import ConfigurationError
from trading_bot.monitoring.logger import get_logger
from trading_bot.monitoring.screenshots import ScreenshotManager

log = get_logger("trading_bot.browser")


class BrowserManager:
    """Owns the Playwright driver and a persistent browser context."""

    def __init__(
        self,
        settings: Settings,
        screenshots: ScreenshotManager,
    ) -> None:
        self.settings = settings
        self.screenshots = screenshots
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._cdp_browser: Browser | None = None
        self._chrome_proc: subprocess.Popen[bytes] | None = None

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser is not started. Call BrowserManager.start() first.")
        return self._page

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("Browser is not started. Call BrowserManager.start() first.")
        return self._context

    @property
    def is_connected(self) -> bool:
        if self._cdp_browser is not None:
            try:
                return self._cdp_browser.is_connected()
            except Exception:  # noqa: BLE001
                return False
        if self._context is None:
            return False
        try:
            return self._context.browser is None or self._context.browser.is_connected()
        except Exception:  # noqa: BLE001
            return False

    async def start(self) -> Page:
        if self._playwright is not None:
            log.warning("browser_already_started")
            return self.page

        profile_dir = Path(self.settings.browser_profile_dir)
        profile_dir.mkdir(parents=True, exist_ok=True)

        chrome_profile = (self.settings.chrome_profile_directory or "").strip()
        use_cdp = self.settings.browser_channel == "chrome" and bool(chrome_profile)

        if chrome_profile:
            assert_chrome_unlocked(require_closed=self.settings.require_chrome_closed)
            if use_cdp:
                self._prepare_cdp_user_data(profile_dir, chrome_profile)
            resolved = assert_named_profile_exists(profile_dir, chrome_profile)
        else:
            resolved = profile_dir

        log.info(
            "browser_starting",
            profile=str(profile_dir),
            resolved_profile=str(resolved),
            headless=self.settings.headless,
            channel=self.settings.browser_channel,
            chrome_profile_directory=chrome_profile or "Default",
            attach_mode="cdp" if use_cdp else "persistent_context",
        )
        self._playwright = await async_playwright().start()
        try:
            if use_cdp:
                return await self._start_chrome_cdp(profile_dir, chrome_profile)
            return await self._start_persistent_context(profile_dir, chrome_profile)
        except Exception:
            await self.close()
            raise

    def _prepare_cdp_user_data(self, profile_dir: Path, chrome_profile: str) -> None:
        if is_default_chrome_user_data_dir(profile_dir):
            raise ConfigurationError(
                "Chrome blocks remote debugging on the live User Data folder, so the bot "
                "cannot control Profile 52 there.\n"
                "Set BROWSER_PROFILE_DIR=./browser_profile and run:\n"
                "  python main.py --copy-chrome-profile\n"
                "  python main.py --setup"
            )
        dest_profile = profile_dir / chrome_profile
        source = self.settings.chrome_source_user_data_dir
        if dest_profile.exists():
            return
        if source is None:
            raise ConfigurationError(
                f"Chrome profile {chrome_profile!r} is missing in {profile_dir}. "
                "Set CHROME_SOURCE_USER_DATA_DIR and run python main.py --copy-chrome-profile"
            )
        copy_named_profile(source, profile_dir, chrome_profile)

    async def _start_chrome_cdp(self, user_data_dir: Path, chrome_profile: str) -> Page:
        port = self.settings.chrome_debug_port
        timeout = self.settings.chrome_connect_timeout_seconds
        chrome_path = find_chrome_executable(self.settings.chrome_path)
        print(f"Starting Chrome with {chrome_profile} and attaching (up to {timeout}s)...")
        await asyncio.sleep(1.5)
        self._chrome_proc = spawn_chrome_with_cdp(
            chrome_path=chrome_path,
            user_data_dir=user_data_dir,
            profile_directory=chrome_profile,
            port=port,
            headless=self.settings.headless,
        )
        await self._wait_for_cdp(port, timeout)
        assert self._playwright is not None
        endpoint = cdp_endpoint(port)
        log.info("connecting_over_cdp", endpoint=endpoint)
        self._cdp_browser = await self._playwright.chromium.connect_over_cdp(endpoint)
        if not self._cdp_browser.contexts:
            raise ConfigurationError("Chrome started but exposed no browser context over CDP.")
        self._context = self._cdp_browser.contexts[0]
        if self._context.pages:
            self._page = self._pick_page(self._context)
        else:
            self._page = await self._context.new_page()
        self._page.set_default_timeout(15_000)
        log.info("browser_started", pages=len(self._context.pages), url=self._page.url, mode="cdp")
        return self._page

    async def _wait_for_cdp(self, port: int, timeout: int) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self._chrome_proc is not None and self._chrome_proc.poll() is not None:
                raise ConfigurationError(
                    "Chrome exited before the bot could attach. "
                    "Close Chrome completely (taskkill /IM chrome.exe /F) and retry."
                )
            if await asyncio.to_thread(cdp_is_ready, port):
                return
            await asyncio.sleep(0.25)
        raise ConfigurationError(
            f"Chrome opened but did not accept control on port {port} within {timeout}s.\n"
            "Chrome blocks debugging on the live User Data folder. Use a copy:\n"
            "  python main.py --copy-chrome-profile\n"
            "  python main.py --setup"
        )

    def _pick_page(self, context: BrowserContext) -> Page:
        for existing in context.pages:
            if "tradingview.com" in (existing.url or "").lower():
                return existing
        if context.pages:
            return context.pages[0]
        raise ConfigurationError("Chrome attached but has no open tabs.")

    async def _start_persistent_context(self, profile_dir: Path, chrome_profile: str) -> Page:
        assert self._playwright is not None
        launch_args = [
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if chrome_profile:
            launch_args.append(f"--profile-directory={chrome_profile}")

        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(profile_dir),
            "headless": self.settings.headless,
            "slow_mo": self.settings.slow_mo_ms,
            "accept_downloads": False,
            "args": launch_args,
            "timeout": self.settings.chrome_connect_timeout_seconds * 1000,
        }
        if self.settings.browser_channel != "chromium":
            launch_kwargs["channel"] = self.settings.browser_channel

        self._context = await self._playwright.chromium.launch_persistent_context(**launch_kwargs)
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        self._page.set_default_timeout(15_000)
        log.info("browser_started", pages=len(self._context.pages), url=self._page.url, mode="persistent")
        return self._page

    async def new_page(self) -> Page:
        self._page = await self.context.new_page()
        self._page.set_default_timeout(15_000)
        return self._page

    async def bring_to_front(self) -> Page:
        await self.page.bring_to_front()
        return self.page

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        if not url:
            raise ValueError("Cannot navigate: URL is empty. Set BROKER_URL in .env.")
        log.info("navigating", url=url)
        await self.page.goto(url, wait_until=wait_until)

    async def restart(self) -> Page:
        """Phase 11 foundation: recycle Playwright while keeping the profile on disk."""
        log.warning("browser_restarting")
        await self.close()
        return await self.start()

    async def close(self) -> None:
        context = self._context
        cdp_browser = self._cdp_browser
        chrome_proc = self._chrome_proc
        playwright = self._playwright
        self._page = None
        self._context = None
        self._cdp_browser = None
        self._chrome_proc = None
        self._playwright = None

        if cdp_browser is not None:
            try:
                await cdp_browser.close()
            except Exception as exc:  # noqa: BLE001
                log.warning("cdp_browser_close_failed", error=str(exc))
        elif context is not None:
            try:
                await context.close()
            except Exception as exc:  # noqa: BLE001
                log.warning("browser_context_close_failed", error=str(exc))

        if chrome_proc is not None and chrome_proc.poll() is None:
            chrome_proc.terminate()
            try:
                chrome_proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                chrome_proc.kill()

        if playwright is not None:
            try:
                await playwright.stop()
            except Exception as exc:  # noqa: BLE001
                log.warning("playwright_stop_failed", error=str(exc))
        log.info("browser_closed")
