"""Manual browser setup and selector testing. Never submits an order."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

from playwright.async_api import Page

from trading_bot.browser.browser_manager import BrowserManager
from trading_bot.browser.click_guard import ClickGuard
from trading_bot.browser.login import LoginManager
from trading_bot.browser.navigation import Navigator
from trading_bot.browser.selectors import SELECTOR_TEST_KEYS, SelectorRegistry
from trading_bot.config import Settings
from trading_bot.monitoring.logger import get_logger
from trading_bot.monitoring.screenshots import ScreenshotManager
from trading_bot.safety.kill_switch import KillSwitch
from trading_bot.safety.page_validator import PageValidator

log = get_logger("trading_bot.setup")

INSPECT_JS = """
() => {
  if (window.__tbSetupInstalled) return;
  window.__tbSetupInstalled = true;
  window.__tbLast = null;
  const describe = (el) => {
    if (!el || !el.getAttribute) return null;
    const text = (el.innerText || el.value || "").trim().slice(0, 80);
    return {
      tag: el.tagName,
      id: el.id || "",
      testId: el.getAttribute("data-testid") || "",
      ariaLabel: el.getAttribute("aria-label") || "",
      name: el.getAttribute("name") || "",
      role: el.getAttribute("role") || "",
      type: el.getAttribute("type") || "",
      className: (el.className || "").toString().slice(0, 120),
      text,
      suggested: suggest(el),
    };
  };
  const suggest = (el) => {
    const testId = el.getAttribute("data-testid");
    if (testId) return `[data-testid="${testId}"]`;
    const aria = el.getAttribute("aria-label");
    if (aria) return `[aria-label="${aria}"]`;
    const role = el.getAttribute("role");
    if (role && el.getAttribute("name")) return `[role="${role}"][name="${el.getAttribute("name")}"]`;
    if (el.id) return `#${el.id}`;
    return el.tagName.toLowerCase();
  };
  document.addEventListener("click", (event) => {
    window.__tbLast = describe(event.target);
  }, true);
}
"""


def _input(prompt: str) -> Awaitable[str]:
    return asyncio.to_thread(input, prompt)


class SetupWizard:
    def __init__(
        self,
        settings: Settings,
        registry: SelectorRegistry,
        browser: BrowserManager,
        login: LoginManager,
        navigator: Navigator,
        validator: PageValidator,
        screenshots: ScreenshotManager,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.browser = browser
        self.login = login
        self.navigator = navigator
        self.validator = validator
        self.screenshots = screenshots
        self.click_guard = ClickGuard(registry)

    async def run_setup(self) -> None:
        print("\n=== Trading bot setup (manual login, no orders) ===\n")
        profile_path = self.settings.chrome_profile_path
        print(f"Chrome user data: {self.settings.browser_profile_dir}")
        print(f"Named profile:    {self.settings.chrome_profile_directory or 'Default'}")
        print(f"Profile folder:   {profile_path}")
        print("Close Google Chrome first:  taskkill /IM chrome.exe /F")
        print("This uses a COPY of that Chrome profile in ./browser_profile (Chrome blocks")
        print("debugging on the live User Data folder). If the copy is missing, run:")
        print("  python main.py --copy-chrome-profile")
        print("Log in yourself only if TradingView asks. Complete CAPTCHA / 2FA yourself.")
        print("Navigate to the trading page for your instrument.")
        print("This mode will NEVER click Buy, Sell, or Confirm.\n")
        page = await self.browser.start()
        if self.settings.broker_url:
            await self.navigator.open_broker(page)
        await page.evaluate(INSPECT_JS)

        await _input("Press Enter when the trading page is visible and you are logged in...")
        await self.screenshots.capture(page, "setup_trading_page", symbol=self.settings.symbol)

        status = await self.login.detect_status(page)
        print(f"Auth status: {status.value}")
        print(f"URL: {page.url}")
        print(f"Title: {await page.title()}")

        print("\nClick any control in the broker UI, then come back here.")
        while True:
            answer = (await _input(
                "Commands: [inspect] [bind NAME] [test] [save] [quit] > "
            )).strip()
            if not answer or answer in {"q", "quit", "exit"}:
                break
            if answer == "inspect":
                await self._print_last_click(page)
                continue
            if answer == "test":
                await self.test_selectors(page, highlight=True)
                continue
            if answer == "save":
                path = self.registry.save_overlay(self.settings.selectors_file)
                print(f"Saved {path}")
                continue
            if answer.startswith("bind "):
                name = answer.split(maxsplit=1)[1].strip()
                await self._bind_from_last_click(page, name)
                continue
            print("Unknown command.")

        path = self.registry.save_overlay(self.settings.selectors_file)
        print(f"\nSession remains in {self.settings.browser_profile_dir}")
        print(f"Selectors saved to {path}")
        print("Next: python main.py --test-selectors\n")

    async def run_test_selectors(self) -> None:
        print("\n=== Selector test (NO orders will be submitted) ===\n")
        page = await self.browser.start()
        if self.settings.broker_url:
            await self.navigator.open_trading_page(page)
        await self.test_selectors(page, highlight=True)
        await self.screenshots.capture(page, "test_selectors", symbol=self.settings.symbol)
        await _input("Press Enter to close the browser...")

    async def test_selectors(self, page: Page, *, highlight: bool) -> None:
        print(f"{'name':<24} {'ok':<6} {'vis':<6} {'en':<6} {'n':<4} selector")
        print("-" * 80)
        for name in SELECTOR_TEST_KEYS:
            result = await self.registry.probe(page, name)
            ok = "YES" if result.ok else "NO"
            vis = "-" if result.visible is None else ("Y" if result.visible else "N")
            en = "-" if result.enabled is None else ("Y" if result.enabled else "N")
            print(
                f"{name:<24} {ok:<6} {vis:<6} {en:<6} {result.count:<4} "
                f"{result.selector or result.error or '(empty)'}"
            )
            if highlight and result.ok:
                await self.click_guard.highlight(page, name)
        print("\nNo Buy/Sell/Confirm clicks were issued.\n")

    async def _print_last_click(self, page: Page) -> None:
        data = await page.evaluate("() => window.__tbLast")
        if not data:
            print("No click captured yet. Click an element in the broker window first.")
            return
        print("Last clicked element:")
        for key, value in data.items():
            print(f"  {key}: {value}")

    async def _bind_from_last_click(self, page: Page, name: str) -> None:
        data = await page.evaluate("() => window.__tbLast")
        if not data or not data.get("suggested"):
            print("Click an element first, then run: bind <selector_name>")
            return
        suggested = str(data["suggested"])
        print(f"Suggested locator for {name}: {suggested}")
        confirm = (await _input("Save this locator? [y/N] ")).strip().lower()
        if confirm != "y":
            print("Skipped.")
            return
        self.registry.set(name, suggested)
        print(f"Bound {name} -> {suggested}")


def build_setup_stack(
    settings: Settings,
) -> tuple[SetupWizard, BrowserManager]:
    settings.ensure_runtime_dirs()
    screenshots = ScreenshotManager(settings.screenshots_dir)
    registry = SelectorRegistry(settings.selectors_file)
    browser = BrowserManager(settings, screenshots)
    login = LoginManager(settings, registry, screenshots)
    navigator = Navigator(settings, registry)
    kill_switch = KillSwitch()
    validator = PageValidator(settings, registry, navigator, screenshots, kill_switch)
    wizard = SetupWizard(
        settings, registry, browser, login, navigator, validator, screenshots
    )
    return wizard, browser
